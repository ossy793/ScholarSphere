"""Fee rules and limits kept in app_settings. A key that was never saved uses its default."""

from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminUser, AppSetting
from app.schemas.settings import FeeSettings
from app.services import audit

# Paystack's transfer fees as last known (₦10 up to ₦5,000, ₦25 up to ₦50,000, ₦50 above).
# Confirm on Paystack's pricing page before launch.
DEFAULTS: dict[str, Any] = {
    "paystack_fee_percent": "1.5",
    "platform_fee_percent": "3",
    "payout_fee_percent": "1",
    "max_due_amount_kobo": 20_000_000,  # ₦200,000: above this only the super admin approves
    "transfer_fee_bands": [
        {"up_to_kobo": 500_000, "fee_kobo": 1_000},
        {"up_to_kobo": 5_000_000, "fee_kobo": 2_500},
        {"up_to_kobo": None, "fee_kobo": 5_000},
    ],
    "settlement_mode": "balance",
    "available_after_hours": 24,
}


async def get_fee_settings(db: AsyncSession) -> FeeSettings:
    rows = await db.scalars(select(AppSetting).where(AppSetting.key.in_(DEFAULTS)))
    values = {**DEFAULTS, **{row.key: row.value for row in rows}}
    return FeeSettings.model_validate(values)


async def update_fee_settings(
    db: AsyncSession, new: FeeSettings, actor: AdminUser, request: Request
) -> FeeSettings:
    """Saves the keys that changed and writes one audit row with their old and new values."""
    current = (await get_fee_settings(db)).model_dump(mode="json")
    wanted = new.model_dump(mode="json")
    changed = [key for key in wanted if wanted[key] != current[key]]
    if not changed:
        return new

    existing = {
        row.key: row
        for row in await db.scalars(
            select(AppSetting).where(AppSetting.key.in_(changed)).with_for_update()
        )
    }
    for key in changed:
        row = existing.get(key)
        if row is None:
            db.add(AppSetting(key=key, value=wanted[key], updated_by=actor.id))
        else:
            row.value = wanted[key]
            row.updated_by = actor.id

    await audit.record(
        db, actor=actor, action="settings.update", entity="app_settings",
        before={k: current[k] for k in changed}, after={k: wanted[k] for k in changed},
        request=request,
    )
    return new
