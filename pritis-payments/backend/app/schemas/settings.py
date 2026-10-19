from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

Percent = Annotated[Decimal, Field(ge=0, le=20, max_digits=4, decimal_places=2)]


class TransferFeeBand(BaseModel):
    # Payouts up to this amount pay fee_kobo. None means "and above" (the last band).
    up_to_kobo: int | None = Field(default=None, gt=0)
    fee_kobo: int = Field(ge=0, le=1_000_000)


class FeeSettings(BaseModel):
    """Fee rules and limits. Percentages are plain percent (1.5 means 1.5%); money is kobo."""

    paystack_fee_percent: Percent
    platform_fee_percent: Percent
    payout_fee_percent: Percent
    max_due_amount_kobo: int = Field(ge=10_000)  # at least ₦100
    transfer_fee_bands: list[TransferFeeBand] = Field(min_length=1, max_length=10)
    # Where Paystack keeps the money (owner's decision, 2026-10-19: the Paystack balance).
    #   balance: funds stay in the Paystack balance; a payment becomes Available to the rep
    #            available_after_hours after it was paid; Pritis withdraws revenue in the app.
    #   bank:    Paystack settles to Pritis's bank; payments become Available when Paystack
    #            reports the settlement (or the accountant marks it).
    settlement_mode: Literal["balance", "bank"] = "balance"
    available_after_hours: int = Field(default=24, ge=0, le=240)

    @model_validator(mode="after")
    def _bands_in_order(self) -> "FeeSettings":
        bands = self.transfer_fee_bands
        if bands[-1].up_to_kobo is not None:
            raise ValueError("The last transfer fee band must cover all larger amounts")
        limits = [b.up_to_kobo for b in bands[:-1]]
        if any(limit is None for limit in limits):
            raise ValueError("Only the last transfer fee band can be open-ended")
        if limits != sorted(set(limits)):
            raise ValueError("Transfer fee bands must go from smallest to largest amount")
        return self
