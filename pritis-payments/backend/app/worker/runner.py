"""Runs jobs on their timetable.

Job state lives in worker_jobs, so a restart doesn't skip the nightly check or run it
twice. Each job holds a PostgreSQL advisory lock while it runs, so two workers started
by mistake never run the same job at the same time.
"""

import asyncio
import logging
import zlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.clock import utcnow
from app.core.db import SessionLocal, engine
from app.models import WorkerJob
from app.worker.jobs import Job

log = logging.getLogger("app.worker")
LAGOS = ZoneInfo("Africa/Lagos")
TICK_SECONDS = 5


def latest_daily_slot(job: Job, now: datetime) -> datetime:
    """The most recent moment (at or before now) the daily job was scheduled for."""
    local = now.astimezone(LAGOS)
    slot = datetime.combine(local.date(), job.daily_at, tzinfo=LAGOS)
    return slot if slot <= local else slot - timedelta(days=1)


def is_due(job: Job, state: WorkerJob | None, now: datetime) -> bool:
    if job.every is not None:
        return state is None or state.last_started_at is None or now - state.last_started_at >= job.every
    # Daily: due if it hasn't succeeded since its latest slot (also catches a slot
    # missed while the worker was stopped).
    return state is None or state.last_success_at is None or state.last_success_at < latest_daily_slot(job, now)


async def _record(name: str, **values) -> None:
    async with SessionLocal() as db:
        await db.execute(
            insert(WorkerJob).values(name=name, **values)
            .on_conflict_do_update(index_elements=["name"], set_={**values, "updated_at": utcnow()})
        )
        await db.commit()


async def run_job(job: Job) -> str | None:
    """Runs one job now, unless another worker is already running it. Never raises."""
    key = zlib.crc32(job.name.encode())
    async with engine.connect() as lock_conn:
        if not await lock_conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}):
            return None
        try:
            started = utcnow()
            async with SessionLocal() as db:
                runs = (await db.scalar(select(WorkerJob.runs).where(WorkerJob.name == job.name))) or 0
            await _record(job.name, last_started_at=started, last_status="running", runs=runs + 1)
            try:
                result = await job.run()
            except Exception as exc:
                log.exception("Job %s failed", job.name)
                await _record(job.name, last_finished_at=utcnow(), last_status="error",
                              last_error=repr(exc)[:2000])
                return None
            await _record(job.name, last_finished_at=utcnow(), last_success_at=started,
                          last_status="ok", last_error=None, last_result=(result or "")[:300])
            if result and not result.startswith(("sent 0", "retried 0", "settled 0", "checked 0")):
                log.info("Job %s: %s", job.name, result)
            return result
        finally:
            await lock_conn.scalar(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


async def run_forever(jobs: list[Job]) -> None:
    log.info(
        "Worker started with %d jobs: %s. Leave this window open; it prints a line when a "
        "job does something or fails.",
        len(jobs), ", ".join(j.name for j in jobs),
    )
    running: dict[str, asyncio.Task] = {}
    while True:
        try:
            async with SessionLocal() as db:
                states = {s.name: s for s in (await db.scalars(select(WorkerJob))).all()}
            now = utcnow()
            for job in jobs:
                if job.name in running and not running[job.name].done():
                    continue
                if is_due(job, states.get(job.name), now):
                    running[job.name] = asyncio.create_task(run_job(job))
        except Exception:
            log.exception("Worker tick failed; trying again shortly")
        await asyncio.sleep(TICK_SECONDS)


async def run_once(jobs: list[Job]) -> None:
    """Every given job once, now, ignoring the timetable (for checks and tests)."""
    for job in jobs:
        result = await run_job(job)
        if result is None:
            log.info("Job %s: skipped (already running elsewhere, or it failed; see above)", job.name)
        elif result.startswith(("sent 0", "retried 0", "settled 0", "checked 0")):
            log.info("Job %s: %s", job.name, result)  # quiet results aren't logged by run_job
