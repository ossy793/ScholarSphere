"""Paystack API client (async httpx). The secret key lives only in the server's environment.

Banks, account resolution and transfer recipients (phase 4); transactions and webhook
signatures (phase 6); balance and transfers (phase 8); refunds (phase 10).
"""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import AppError

log = logging.getLogger("app.paystack")

BASE_URL = "https://api.paystack.co"
BANKS_CACHE_SECONDS = 24 * 3600


class TransferRefused(Exception):
    """Paystack definitely did not start the transfer (it answered 4xx)."""


class RefundRefused(Exception):
    """Paystack definitely did not accept the refund (it answered 4xx)."""


@dataclass(frozen=True)
class Bank:
    code: str
    name: str


class PaystackClient:
    def __init__(self, secret_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.secret_key = secret_key
        self.transport = transport  # tests pass a mock transport
        self._banks: list[Bank] | None = None
        self._banks_at = 0.0

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.secret_key}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                base_url=BASE_URL, timeout=20, transport=self.transport
            ) as client:
                return await client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("Paystack %s %s failed: %r", method, path, exc)
            raise AppError(
                "paystack_unavailable", "Couldn't reach Paystack. Please try again shortly.", 502
            ) from exc

    async def list_banks(self) -> list[Bank]:
        """Nigerian banks that accept transfers (NUBAN), cached for a day."""
        if self._banks is not None and time.monotonic() - self._banks_at < BANKS_CACHE_SECONDS:
            return self._banks
        resp = await self._request(
            "GET", "/bank", params={"country": "nigeria", "currency": "NGN", "perPage": 500}
        )
        if resp.status_code != 200 or not resp.json().get("status"):
            log.warning("Paystack bank list failed: %s %s", resp.status_code, resp.text[:300])
            raise AppError("paystack_unavailable", "Couldn't load the list of banks.", 502)
        banks = [
            Bank(code=str(b["code"]), name=b["name"].strip())
            for b in resp.json()["data"]
            if b.get("active", True) and not b.get("is_deleted") and b.get("type", "nuban") == "nuban"
        ]
        # Paystack can list the same code twice; keep the first.
        unique = {b.code: b for b in reversed(banks)}
        self._banks = sorted(unique.values(), key=lambda b: b.name.casefold())
        self._banks_at = time.monotonic()
        return self._banks

    async def bank_name(self, bank_code: str) -> str:
        for bank in await self.list_banks():
            if bank.code == bank_code:
                return bank.name
        raise AppError(
            "validation_error", "Some fields are invalid", 422,
            [{"field": "body.bank_code", "message": "Choose your bank from the list",
              "type": "bank_invalid"}],
        )

    async def resolve_account(self, account_number: str, bank_code: str) -> str:
        """Returns the account name the bank has for this account number."""
        resp = await self._request(
            "GET", "/bank/resolve", params={"account_number": account_number, "bank_code": bank_code}
        )
        body = resp.json() if resp.content else {}
        if resp.status_code == 200 and body.get("status") and body.get("data", {}).get("account_name"):
            return body["data"]["account_name"].strip()
        if resp.status_code in (400, 404, 422):
            raise AppError(
                "account_not_found",
                "We couldn't find that account. Check the bank and the 10-digit account number.",
                400,
            )
        log.warning("Paystack resolve failed: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Couldn't check the account right now. Try again shortly.", 502)

    async def create_transfer_recipient(
        self, *, account_name: str, account_number: str, bank_code: str
    ) -> str:
        """Registers a payout destination; returns its recipient code (RCP_...)."""
        resp = await self._request(
            "POST", "/transferrecipient",
            json={
                "type": "nuban",
                "name": account_name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": "NGN",
            },
        )
        body = resp.json() if resp.content else {}
        code = body.get("data", {}).get("recipient_code") if body.get("status") else None
        if resp.status_code in (200, 201) and code:
            return code
        log.warning("Paystack transfer recipient failed: %s %s", resp.status_code, resp.text[:300])
        raise AppError(
            "paystack_recipient_failed",
            f"Paystack didn't accept the payout account: {body.get('message', 'unknown error')}",
            502,
        )


    async def initialize_transaction(
        self, *, email: str, amount_kobo: int, reference: str, callback_url: str, metadata: dict[str, Any]
    ) -> str:
        """Starts a checkout; returns the authorization URL to send the student to."""
        resp = await self._request(
            "POST", "/transaction/initialize",
            json={
                "email": email, "amount": amount_kobo, "currency": "NGN", "reference": reference,
                "callback_url": callback_url, "metadata": metadata,
            },
        )
        body = resp.json() if resp.content else {}
        url = body.get("data", {}).get("authorization_url") if body.get("status") else None
        if resp.status_code == 200 and url:
            return url
        log.warning("Paystack initialize failed: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Couldn't start the payment. Please try again.", 502)

    async def verify_transaction(self, reference: str) -> dict[str, Any] | None:
        """The transaction as Paystack has it (same shape as charge.success data), or None
        if Paystack doesn't know the reference."""
        resp = await self._request("GET", f"/transaction/verify/{reference}")
        body = resp.json() if resp.content else {}
        if resp.status_code == 200 and body.get("status"):
            return body["data"]
        if resp.status_code in (400, 404):
            return None
        log.warning("Paystack verify failed: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Couldn't check the payment right now.", 502)


    async def get_balance(self) -> int:
        """Pritis's NGN balance on Paystack, in kobo. Transfers are paid from it."""
        resp = await self._request("GET", "/balance")
        body = resp.json() if resp.content else {}
        if resp.status_code == 200 and body.get("status"):
            for row in body.get("data") or []:
                if str(row.get("currency", "")).upper() == "NGN":
                    return int(row.get("balance") or 0)
            return 0
        log.warning("Paystack balance failed: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Couldn't read the Paystack balance.", 502)

    async def initiate_transfer(
        self, *, amount_kobo: int, recipient_code: str, reference: str, reason: str
    ) -> dict[str, Any]:
        """Starts a transfer from the Paystack balance. Returns Paystack's transfer data
        (status: success, pending, otp, failed...). A refusal raises TransferRefused; a
        timeout or 5xx raises AppError and the caller must check by reference before
        doing anything else."""
        resp = await self._request(
            "POST", "/transfer",
            json={"source": "balance", "amount": amount_kobo, "recipient": recipient_code,
                  "reference": reference, "reason": reason[:100], "currency": "NGN"},
        )
        body = resp.json() if resp.content else {}
        if resp.status_code in (200, 201) and body.get("status"):
            return body["data"]
        if 400 <= resp.status_code < 500:
            raise TransferRefused(str(body.get("message") or f"HTTP {resp.status_code}"))
        log.warning("Paystack transfer unclear: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Paystack didn't answer clearly; the transfer will be checked.", 502)

    async def verify_transfer(self, reference: str) -> dict[str, Any] | None:
        """The transfer as Paystack has it, or None if Paystack doesn't know the reference."""
        resp = await self._request("GET", f"/transfer/verify/{reference}")
        body = resp.json() if resp.content else {}
        if resp.status_code == 200 and body.get("status"):
            return body["data"]
        if resp.status_code in (400, 404):
            return None
        log.warning("Paystack transfer verify failed: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Couldn't check the transfer right now.", 502)

    async def create_refund(self, *, transaction_reference: str, amount_kobo: int, merchant_note: str) -> dict[str, Any]:
        """Asks Paystack to refund a transaction (from the Paystack balance). Returns the
        refund data (id, status: pending...). A refusal raises RefundRefused; a timeout or
        5xx raises AppError and the caller must check before trying again."""
        resp = await self._request(
            "POST", "/refund",
            json={"transaction": transaction_reference, "amount": amount_kobo, "currency": "NGN",
                  "merchant_note": merchant_note[:200]},
        )
        body = resp.json() if resp.content else {}
        if resp.status_code in (200, 201) and body.get("status"):
            return body["data"]
        if 400 <= resp.status_code < 500:
            raise RefundRefused(str(body.get("message") or f"HTTP {resp.status_code}"))
        log.warning("Paystack refund unclear: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Paystack didn't answer clearly; the refund will be checked.", 502)

    async def fetch_refund(self, refund_id: str) -> dict[str, Any] | None:
        resp = await self._request("GET", f"/refund/{refund_id}")
        body = resp.json() if resp.content else {}
        if resp.status_code == 200 and body.get("status"):
            return body["data"]
        if resp.status_code in (400, 404):
            return None
        log.warning("Paystack refund fetch failed: %s %s", resp.status_code, resp.text[:300])
        raise AppError("paystack_unavailable", "Couldn't check the refund right now.", 502)

    async def list_refunds(self, transaction_id: int | str) -> list[dict[str, Any]]:
        """Refunds Paystack has for one transaction (by Paystack's transaction id)."""
        return await self._all_pages("/refund", {"transaction": transaction_id, "currency": "NGN"}, max_pages=2)

    async def _all_pages(self, path: str, params: dict[str, Any], max_pages: int = 50) -> list[dict[str, Any]]:
        """Collects every page of a Paystack list endpoint."""
        items: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            resp = await self._request("GET", path, params={**params, "perPage": 100, "page": page})
            body = resp.json() if resp.content else {}
            if resp.status_code != 200 or not body.get("status"):
                log.warning("Paystack %s failed: %s %s", path, resp.status_code, resp.text[:300])
                raise AppError("paystack_unavailable", f"Couldn't read {path} from Paystack.", 502)
            items.extend(body.get("data") or [])
            page_count = (body.get("meta") or {}).get("pageCount") or 1
            if page >= page_count:
                break
            page += 1
        return items

    async def list_settlements(self, since: datetime) -> list[dict[str, Any]]:
        """Settlements (payouts from Paystack to Pritis's bank) since a date."""
        return await self._all_pages("/settlement", {"from": since.isoformat()})

    async def settlement_transactions(self, settlement_id: int | str) -> list[dict[str, Any]]:
        """The transactions a settlement paid out."""
        return await self._all_pages(f"/settlement/{settlement_id}/transactions", {})

    async def list_transactions(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Successful transactions in a period, for the nightly comparison."""
        return await self._all_pages(
            "/transaction", {"status": "success", "from": start.isoformat(), "to": end.isoformat()}
        )


def signature_valid(raw_body: bytes, signature: str | None) -> bool:
    """x-paystack-signature is the HMAC-SHA512 of the raw body with the secret key."""
    key = get_settings().paystack_secret_key
    if not key or not signature:
        return False
    expected = hmac.new(key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


_client: PaystackClient | None = None


def get_client() -> PaystackClient:
    """The shared client (keeps the bank list cache). Tests replace this function."""
    global _client
    key = get_settings().paystack_secret_key
    if not key:
        raise AppError("paystack_not_configured", "Paystack is not set up yet (PAYSTACK_SECRET_KEY).", 503)
    if _client is None or _client.secret_key != key:
        _client = PaystackClient(key)
    return _client
