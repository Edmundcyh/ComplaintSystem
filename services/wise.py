import logging
import uuid

import httpx
from decouple import config
from fastapi import HTTPException

REQUEST_TIMEOUT = 10  # seconds

logger = logging.getLogger(__name__)


class WiseService:
    def __init__(self):
        self.main_url = config("WISE_URL")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config('WISE_API_KEY')}",
        }
        self._profile_id = None

    async def _request(self, method, path, expected_status, json=None):
        try:
            async with httpx.AsyncClient(
                base_url=self.main_url, headers=self.headers, timeout=REQUEST_TIMEOUT
            ) as client:
                resp = await client.request(method, path, json=json)
        except httpx.HTTPError:
            logger.exception("Wise request %s %s failed", method, path)
            raise HTTPException(502, "Payment provider is not available at the moment")
        if resp.status_code != expected_status:
            logger.error(
                "Wise request %s %s returned %s: %s",
                method,
                path,
                resp.status_code,
                resp.text,
            )
            raise HTTPException(502, "Payment provider is not available at the moment")
        return resp.json()

    async def get_profile_id(self):
        # Looked up on first use so the app can start without reaching Wise
        if self._profile_id is None:
            profiles = await self._request("GET", "/v1/profiles", 200)
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
        data = {
            "targetAccount": target_account_id,
            "quoteUuid": quote_id,
            "customerTransactionId": str(uuid.uuid4()),
            "details": {},
        }
        resp = await self._request("POST", "/v1/transfers", 200, json=data)
        return resp["id"]

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
