"""The fee engine (design section 8). Whole kobo only; Decimal maths, rounded half up.

The student pays the due plus one service charge: the Paystack part (a percentage in
settings) plus the platform part (set per due by the approver). Only the total charge is
ever shown; the split stays internal.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


def kobo(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class StudentQuote:
    due_kobo: int
    paystack_part_kobo: int
    platform_part_kobo: int

    @property
    def service_charge_kobo(self) -> int:
        return self.paystack_part_kobo + self.platform_part_kobo

    @property
    def total_kobo(self) -> int:
        return self.due_kobo + self.service_charge_kobo


def student_total(due_kobo: int, paystack_percent: Decimal, platform_percent: Decimal) -> StudentQuote:
    """Percentages as plain percent: Decimal('1.5') means 1.5%."""
    return StudentQuote(
        due_kobo=due_kobo,
        paystack_part_kobo=kobo(Decimal(due_kobo) * paystack_percent / 100),
        platform_part_kobo=kobo(Decimal(due_kobo) * platform_percent / 100),
    )


@dataclass(frozen=True)
class PayoutQuote:
    payout_kobo: int
    platform_fee_kobo: int
    transfer_fee_kobo: int

    @property
    def service_charge_kobo(self) -> int:
        return self.platform_fee_kobo + self.transfer_fee_kobo

    @property
    def net_kobo(self) -> int:
        return self.payout_kobo - self.service_charge_kobo


def rep_payout(payout_kobo: int, payout_percent: Decimal, transfer_fee_kobo: int) -> PayoutQuote:
    """Used from phase 8: what a rep receives from a payout."""
    return PayoutQuote(
        payout_kobo=payout_kobo,
        platform_fee_kobo=kobo(Decimal(payout_kobo) * payout_percent / 100),
        transfer_fee_kobo=transfer_fee_kobo,
    )
