import logging
import uuid
from functools import lru_cache

import anyio
import httpx
from decouple import config
from fastapi import HTTPException

REQUEST_TIMEOUT = 10  # seconds
# Waits between attempts of requests that are safe to repeat
RETRY_DELAYS = (0.5, 2)  # seconds
MAX_RETRY_AFTER = 5  # seconds; longer Retry-After values are not waited for

# Wise transfer statuses (GET /v1/transfers/{id})
UNFUNDED = "incoming_payment_waiting"
CANCELLED = "cancelled"
FUNDED = {"processing", "funds_converted", "outgoing_payment_sent"}

logger = logging.getLogger(__name__)


def _error_summary(resp):
    # Wise error bodies can echo the request (IBAN, account holder name),
    # so only the error codes are logged
    try:
        body = resp.json()
    except ValueError:
        return f"{len(resp.content)} byte non-JSON body"
    if isinstance(body, dict):
        if isinstance(body.get("errors"), list):
            return ", ".join(
                str(e.get("code")) for e in body["errors"] if isinstance(e, dict)
            )
        for key in ("errorCode", "error", "code"):
            if key in body:
                return str(body[key])
    return "no error code"


def _retry_after(resp, default):
    """Seconds to wait as asked by a 429 response, or None if that's too long."""
    try:
        delay = float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return default
    return max(delay, 0) if delay <= MAX_RETRY_AFTER else None


class WiseService:
    def __init__(self, transport=None):
        # transport lets tests replace the network with httpx.MockTransport
        self.transport = transport
        self.main_url = config("WISE_URL")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config('WISE_API_KEY')}",
        }
        self._profile_id = None

    async def _send(self, method, path, json):
        async with httpx.AsyncClient(
            base_url=self.main_url,
            headers=self.headers,
            timeout=REQUEST_TIMEOUT,
            transport=self.transport,
        ) as client:
            return await client.request(method, path, json=json)

    async def _request(self, method, path, expected_status, json=None, retry=False):
        # retry: only for requests Wise handles at most once (reads, and
        # transfer creation with the same customerTransactionId)
        delays = list(RETRY_DELAYS) if retry else []
        while True:
            try:
                resp = await self._send(method, path, json)
            except httpx.HTTPError:
                if not delays:
                    logger.exception("Wise request %s %s failed", method, path)
                    raise HTTPException(
                        502, "Payment provider is not available at the moment"
                    )
                logger.warning(
                    "Wise request %s %s failed, retrying", method, path, exc_info=True
                )
                await anyio.sleep(delays.pop(0))
                continue
            if delays and (resp.status_code == 429 or resp.status_code >= 500):
                delay = delays.pop(0)
                if resp.status_code == 429:
                    delay = _retry_after(resp, default=delay)
                if delay is not None:
                    logger.warning(
                        "Wise request %s %s returned %s, retrying",
                        method,
                        path,
                        resp.status_code,
                    )
                    await anyio.sleep(delay)
                    continue
            break
        if resp.status_code != expected_status:
            logger.error(
                "Wise request %s %s returned %s: %s",
                method,
                path,
                resp.status_code,
                _error_summary(resp),
            )
            raise HTTPException(502, "Payment provider is not available at the moment")
        try:
            return resp.json()
        except ValueError:
            logger.error("Wise request %s %s returned invalid JSON", method, path)
            raise HTTPException(502, "Payment provider is not available at the moment")

    async def get_profile_id(self):
        # Looked up on first use so the app can start without reaching Wise
        if self._profile_id is None:
            profiles = await self._request("GET", "/v1/profiles", 200, retry=True)
            personal = [el["id"] for el in profiles if el["type"].lower() == "personal"]
            if not personal:
                logger.error("Wise account has no personal profile")
                raise HTTPException(
                    502, "Payment provider is not available at the moment"
                )
            self._profile_id = personal[0]
        return self._profile_id

    async def create_quote(self, amount):
        data = {
            "sourceCurrency": "EUR",
            "targetCurrency": "EUR",
            "targetAmount": float(amount),
            "profile": await self.get_profile_id(),
        }
        resp = await self._request("POST", "/v2/quotes", 200, json=data)
        return resp["id"]

    async def create_recipient_account(self, full_name, iban):
        data = {
            "currency": "EUR",
            "type": "iban",
            "profile": await self.get_profile_id(),
            "accountHolderName": full_name,
            "legalType": "PRIVATE",
            "details": {"iban": iban},
        }
        resp = await self._request("POST", "/v1/accounts", 200, json=data)
        return resp["id"]

    async def create_transfer(self, target_account_id, quote_id):
        # Wise creates at most one transfer per customerTransactionId, so the
        # request is retried with the same id: a timeout whose transfer was
        # created anyway then returns that transfer instead of losing track
        # of it
        customer_transaction_id = str(uuid.uuid4())
        data = {
            "targetAccount": target_account_id,
            "quoteUuid": quote_id,
            "customerTransactionId": customer_transaction_id,
            "details": {},
        }
        try:
            resp = await self._request(
                "POST", "/v1/transfers", 200, json=data, retry=True
            )
        except HTTPException:
            # Wise may still have created it; this id finds it in Wise
            logger.error(
                "Creating Wise transfer with customerTransactionId %s failed",
                customer_transaction_id,
            )
            raise
        return resp["id"]

    async def get_transfer_status(self, transfer_id):
        resp = await self._request(
            "GET", f"/v1/transfers/{transfer_id}", 200, retry=True
        )
        return resp.get("status")

    async def fund_transfer(self, transfer_id):
        profile_id = await self.get_profile_id()
        resp = await self._request(
            "POST",
            f"/v3/profiles/{profile_id}/transfers/{transfer_id}/payments",
            201,
            json={"type": "BALANCE"},
        )
        # Wise answers 201 even when funding fails (e.g. insufficient
        # balance) and reports the failure in the body
        if resp.get("status") == "REJECTED":
            logger.error(
                "Wise rejected funding transfer %s: %s",
                transfer_id,
                resp.get("errorCode"),
            )
            raise HTTPException(502, "Payment could not be completed")
        return resp

    async def cancel_transfer(self, transfer_id):
        resp = await self._request("PUT", f"/v1/transfers/{transfer_id}/cancel", 200)
        return resp["id"]


@lru_cache
def get_wise_service():
    return WiseService()
