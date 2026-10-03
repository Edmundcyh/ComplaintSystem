import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import anyio
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


def test_wise_errors_are_logged_without_personal_data(monkeypatch, caplog):
    body = {
        "errors": [
            {
                "code": "NOT_VALID",
                "message": "IBAN DE89370400440532013000 is not valid",
                "arguments": ["Jane Doe", "DE89370400440532013000"],
            }
        ]
    }
    service = wise(lambda request: httpx.Response(422, json=body), monkeypatch)
    with pytest.raises(HTTPException) as ex:
        run(service.cancel_transfer(99))
    assert ex.value.status_code == 502
    assert "NOT_VALID" in caplog.text
    assert "DE89" not in caplog.text and "Jane" not in caplog.text


def test_wise_invalid_json_is_502(monkeypatch):
    service = wise(lambda request: httpx.Response(200, text="<html>"), monkeypatch)
    with pytest.raises(HTTPException) as ex:
        run(service.get_transfer_status(99))
    assert ex.value.status_code == 502


def test_wise_transfer_status(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/transfers/99"
        return httpx.Response(200, json={"id": 99, "status": "processing"})

    assert run(wise(handler, monkeypatch).get_transfer_status(99)) == "processing"


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


@pytest.fixture
def sleeps(monkeypatch):
    waited = []

    async def fake_sleep(delay):
        waited.append(delay)

    monkeypatch.setattr("anyio.sleep", fake_sleep)
    return waited


def test_wise_transfer_creation_survives_lost_response(monkeypatch, sleeps):
    # Wise creates the transfer but the response times out; the retry with
    # the same customerTransactionId returns that transfer, not a new one
    transfers = {}
    calls = []

    def handler(request):
        if request.url.path == "/v1/profiles":
            return httpx.Response(200, json=PROFILES)
        key = json.loads(request.content)["customerTransactionId"]
        calls.append(key)
        created = key in transfers
        transfers.setdefault(key, 1000 + len(transfers))
        if not created:
            raise httpx.ReadTimeout("response lost")
        return httpx.Response(200, json={"id": transfers[key]})

    service = wise(handler, monkeypatch)

    assert run(service.create_transfer(42, "quote-1")) == 1000
    assert len(transfers) == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    assert sleeps == [0.5]


@pytest.mark.parametrize("status", [500, 502, 503])
def test_wise_transfer_creation_retries_server_errors(monkeypatch, sleeps, status):
    answers = iter([httpx.Response(status), httpx.Response(200, json={"id": 7})])
    service = wise(lambda request: next(answers), monkeypatch)
    assert run(service.create_transfer(42, "quote-1")) == 7


def test_wise_retry_honours_short_retry_after(monkeypatch, sleeps):
    answers = iter(
        [
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json={"id": 7}),
        ]
    )
    service = wise(lambda request: next(answers), monkeypatch)
    assert run(service.create_transfer(42, "quote-1")) == 7
    assert sleeps == [3.0]


def test_wise_long_retry_after_is_not_waited_for(monkeypatch, sleeps):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    with pytest.raises(HTTPException):
        run(wise(handler, monkeypatch).create_transfer(42, "quote-1"))
    assert len(requests) == 1 and sleeps == []


def test_wise_gives_up_after_retries_and_logs_the_id(monkeypatch, sleeps, caplog):
    keys = []

    def handler(request):
        keys.append(json.loads(request.content)["customerTransactionId"])
        raise httpx.ConnectTimeout("down")

    with pytest.raises(HTTPException) as ex:
        run(wise(handler, monkeypatch).create_transfer(42, "quote-1"))
    assert ex.value.status_code == 502
    assert len(keys) == 3 and len(set(keys)) == 1
    assert keys[0] in caplog.text


def test_wise_client_errors_are_not_retried(monkeypatch, sleeps):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={"errors": [{"code": "NOT_VALID"}]})

    with pytest.raises(HTTPException):
        run(wise(handler, monkeypatch).create_transfer(42, "quote-1"))
    assert len(requests) == 1


def test_wise_requests_that_could_repeat_an_action_are_not_retried(monkeypatch, sleeps):
    # A second quote, recipient, payment or cancel request isn't deduplicated
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/v1/profiles":
            return httpx.Response(200, json=PROFILES)
        raise httpx.ReadTimeout("response lost")

    service = wise(handler, monkeypatch)
    for call in (
        service.create_quote(5),
        service.create_recipient_account("Jane Doe", "DE89370400440532013000"),
        service.fund_transfer(99),
        service.cancel_transfer(99),
    ):
        with pytest.raises(HTTPException):
            run(call)
    assert requests.count("/v1/profiles") == 1
    assert len(requests) == 5  # profile lookup + one attempt each


def test_wise_lookups_are_not_retried(monkeypatch, sleeps):
    # They run while a complaint row is locked; retrying would hold the lock
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503)

    service = wise(handler, monkeypatch)
    with pytest.raises(HTTPException):
        run(service.get_transfer_status(99))
    with pytest.raises(HTTPException):
        run(service.get_profile_id())
    assert len(requests) == 2 and sleeps == []


def test_wise_transfer_creation_without_retry(monkeypatch, sleeps, caplog):
    keys = []

    def handler(request):
        keys.append(json.loads(request.content)["customerTransactionId"])
        raise httpx.ReadTimeout("response lost")

    with pytest.raises(HTTPException):
        run(wise(handler, monkeypatch).create_transfer(42, "quote-1", retry=False))
    assert len(keys) == 1 and keys[0] in caplog.text


def test_wise_attempt_is_capped_as_a_whole(monkeypatch):
    # A server that keeps the connection busy can't stretch an attempt past
    # REQUEST_TIMEOUT (httpx's own timeout is per network step)
    monkeypatch.setattr("services.wise.REQUEST_TIMEOUT", 0.1)

    async def handler(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"status": "processing"})

    service = wise(handler, monkeypatch)
    started = time.monotonic()
    with pytest.raises(HTTPException) as ex:
        run(service.get_transfer_status(99))
    assert ex.value.status_code == 502
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "retry_after, retried",
    [
        (
            lambda: format_datetime(
                datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True
            ),
            False,
        ),
        (
            lambda: format_datetime(
                datetime.now(timezone.utc) - timedelta(seconds=5), usegmt=True
            ),
            True,
        ),
        (lambda: "soon", False),
    ],
)
def test_wise_retry_after_dates_and_garbage(monkeypatch, sleeps, retry_after, retried):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after()})
        return httpx.Response(200, json={"id": 7})

    service = wise(handler, monkeypatch)
    if retried:
        assert run(service.create_transfer(42, "quote-1")) == 7
        assert sleeps == [0]
    else:
        with pytest.raises(HTTPException):
            run(service.create_transfer(42, "quote-1"))
        assert len(requests) == 1


def test_cancelled_transfer_creation_still_logs_the_id(monkeypatch, caplog):
    keys = []

    async def handler(request):
        keys.append(json.loads(request.content)["customerTransactionId"])
        await asyncio.sleep(5)

    service = wise(handler, monkeypatch)

    async def create_then_give_up():
        with anyio.move_on_after(0.1):
            await service.create_transfer(42, "quote-1")

    run(create_then_give_up())
    assert keys and keys[0] in caplog.text


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
