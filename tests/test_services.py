import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from services.s3 import S3Service
from services.wise import WiseService

PROFILES = [{"id": 1, "type": "business"}, {"id": 7, "type": "PERSONAL"}]


def wise(handler, monkeypatch):
    monkeypatch.setenv("WISE_URL", "https://wise.test")
    monkeypatch.setenv("WISE_API_KEY", "key")
    return WiseService(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_wise_looks_up_personal_profile_once(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/profiles":
            return httpx.Response(200, json=PROFILES)
        return httpx.Response(200, json={"id": "quote-1"})

    service = wise(handler, monkeypatch)
    assert run(service.create_quote("12.30")) == "quote-1"
    assert run(service.create_quote(5)) == "quote-1"

    paths = [r.url.path for r in requests]
    assert paths == ["/v1/profiles", "/v2/quotes", "/v2/quotes"]
    assert requests[0].headers["Authorization"] == "Bearer key"
    body = json.loads(requests[1].content)
    assert body["profile"] == 7
    assert body["targetAmount"] == 12.3


def test_wise_fund_rejected_is_an_error(monkeypatch):
    def handler(request):
        if request.url.path == "/v1/profiles":
            return httpx.Response(200, json=PROFILES)
        # Wise reports a failed funding with 201 and status REJECTED
        return httpx.Response(
            201,
            json={
                "type": "BALANCE",
                "status": "REJECTED",
                "errorCode": "balance.insufficient-funds",
            },
        )

    service = wise(handler, monkeypatch)
    with pytest.raises(HTTPException) as ex:
        run(service.fund_transfer(99))
    assert ex.value.status_code == 502


def test_wise_fund_completed(monkeypatch):
    def handler(request):
        if request.url.path == "/v1/profiles":
            return httpx.Response(200, json=PROFILES)
        assert request.url.path == "/v3/profiles/7/transfers/99/payments"
        return httpx.Response(201, json={"type": "BALANCE", "status": "COMPLETED"})

    service = wise(handler, monkeypatch)
    assert run(service.fund_transfer(99))["status"] == "COMPLETED"


def test_wise_unexpected_status_is_logged(monkeypatch, caplog):
    service = wise(lambda request: httpx.Response(401, text="bad token"), monkeypatch)
    with pytest.raises(HTTPException) as ex:
        run(service.cancel_transfer(99))
    assert ex.value.status_code == 502
    assert "bad token" in caplog.text


def test_wise_network_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    service = wise(handler, monkeypatch)
    with pytest.raises(HTTPException) as ex:
        run(service.cancel_transfer(99))
    assert ex.value.status_code == 502


def test_wise_without_personal_profile(monkeypatch):
    service = wise(
        lambda request: httpx.Response(200, json=[{"id": 1, "type": "business"}]),
        monkeypatch,
    )
    with pytest.raises(HTTPException):
        run(service.get_profile_id())


def test_s3_presigned_url(monkeypatch):
    for name, value in {
        "AWS_ACCESS_KEY": "AKIAEXAMPLE",
        "AWS_SECRET": "secret",
        "AWS_REGION": "eu-west-1",
        "AWS_BUCKET": "photos",
    }.items():
        monkeypatch.setenv(name, value)
    service = S3Service()

    url = service.presigned_url("https://photos.s3.eu-west-1.amazonaws.com/abc.png")

    assert url.startswith("https://photos.s3.eu-west-1.amazonaws.com/abc.png?")
    assert "X-Amz-Signature=" in url
    assert "X-Amz-Expires=3600" in url
