"""POST /api/webhooks/paystack: signed events from Paystack (design section 8).

1. Verify x-paystack-signature (HMAC-SHA512 of the raw body); anything else gets 401.
2. Store the raw event once per event key and answer 200 straight away.
3. Process it after the response, so a slow job never makes Paystack retry.
Processing is idempotent: the same event twice changes nothing.
"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal
from app.core.errors import error_response
from app.models import WebhookEvent
from app.services import payments, paystack

log = logging.getLogger("app.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/paystack")
async def paystack_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()
    if not paystack.signature_valid(raw, request.headers.get("x-paystack-signature")):
        log.warning("Rejected a Paystack webhook with a bad signature from %s", request.client)
        return error_response(401, "bad_signature", "Invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        return error_response(400, "bad_payload", "Not JSON")

    async with SessionLocal() as db:
        event_id = await db.scalar(
            insert(WebhookEvent)
            .values(
                event_type=str(payload.get("event", ""))[:60],
                paystack_event_key=payments.event_key(payload),
                payload=payload, signature_ok=True, status="received",
            )
            .on_conflict_do_nothing(index_elements=["paystack_event_key"])
            .returning(WebhookEvent.id)
        )
        await db.commit()

    if event_id is not None:  # None: a repeat of an event we already have
        background.add_task(payments.process_event, event_id)
    return {"ok": True}
