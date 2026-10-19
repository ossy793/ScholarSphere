"""The background jobs and their timetable (design section 11)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import time, timedelta

from app.core.errors import AppError
from app.services import payments, payouts, reconciliation, refunds, reminders, revenue, settlements
from app.services import paystack as paystack_service
from app.services.email import send_pending


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[], Awaitable[str]]  # returns a short note for the job log
    every: timedelta | None = None
    daily_at: time | None = None  # Lagos time
    description: str = ""


def _paystack() -> paystack_service.PaystackClient | None:
    try:
        return paystack_service.get_client()
    except AppError:
        return None


async def email_outbox() -> str:
    sent = await send_pending()
    return f"sent {sent}"


async def webhook_retry() -> str:
    return f"retried {await payments.retry_webhooks()}"


async def pending_payments() -> str:
    client = _paystack()
    if client is None:
        return "skipped: Paystack not configured"
    counts = await payments.check_stale_pending(client)
    return ", ".join(f"{k} {v}" for k, v in counts.items())


async def settlement_check() -> str:
    return await settlements.run(_paystack())


async def transfer_status_check() -> str:
    client = _paystack()
    if client is None:
        return "skipped: Paystack not configured"
    transfers = await payouts.check_processing(client)
    refund_counts = await refunds.check_processing(client)
    withdrawals = await revenue.check_processing(client)
    return (", ".join(f"transfers {k} {v}" for k, v in transfers.items()) + "; "
            + ", ".join(f"refunds {k} {v}" for k, v in refund_counts.items()) + "; "
            + ", ".join(f"withdrawals {k} {v}" for k, v in withdrawals.items()))


async def payment_reminders() -> str:
    return f"queued {await reminders.queue_reminders()}"


async def nightly_reconciliation() -> str:
    result = await reconciliation.run(_paystack())
    return f"{result.status}, {len(result.items)} item(s)"


JOBS: list[Job] = [
    Job("email_outbox", email_outbox, every=timedelta(seconds=10),
        description="Sends queued emails; retries with backoff, up to 5 attempts"),
    Job("webhook_retry", webhook_retry, every=timedelta(minutes=1),
        description="Processes stored Paystack events that aren't processed yet"),
    Job("pending_payments", pending_payments, every=timedelta(minutes=5),
        description="Verifies payments pending over 10 minutes; Abandoned after 24 hours"),
    Job("transfer_status_check", transfer_status_check, every=timedelta(minutes=10),
        description="Checks payouts and refunds still Processing after 10 minutes, in case a webhook was missed"),
    Job("settlement_check", settlement_check, every=timedelta(hours=1),
        description="Moves payments from the rep's Pending to Available (after the hold, or when Paystack settles)"),
    Job("reminders", payment_reminders, daily_at=time(8, 0),
        description="Emails unpaid students 3 days before a deadline and on the deadline day"),
    Job("reconciliation", nightly_reconciliation, daily_at=time(2, 0),
        description="Checks the ledger, rep balances and Paystack; emails on a mismatch"),
]

JOBS_BY_NAME = {job.name: job for job in JOBS}
