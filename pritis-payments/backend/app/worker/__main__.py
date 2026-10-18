"""The background worker: a separate process from the API.

    python -m app.worker                      # run every job on its timetable (leave it running)
    python -m app.worker --once               # run every job once now, then exit
    python -m app.worker --once --job reconciliation
    python -m app.worker --list               # show the jobs and when they last ran
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models import WorkerJob
from app.worker.jobs import JOBS, JOBS_BY_NAME
from app.worker.runner import run_forever, run_once


async def show_jobs() -> None:
    async with SessionLocal() as db:
        states = {s.name: s for s in (await db.scalars(select(WorkerJob))).all()}
    for job in JOBS:
        when = f"every {job.every}" if job.every else f"daily at {job.daily_at:%H:%M} Lagos"
        s = states.get(job.name)
        last = (f"last {s.last_status} at {s.last_finished_at or s.last_started_at:%Y-%m-%d %H:%M} UTC"
                f" ({s.last_result or s.last_error or ''})") if s else "never run"
        print(f"{job.name:18} {when:24} {last}\n{'':18} {job.description}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="run the jobs once now, then exit")
    parser.add_argument("--job", action="append", choices=sorted(JOBS_BY_NAME), help="only this job")
    parser.add_argument("--list", action="store_true", help="show the jobs and their last run")
    args = parser.parse_args()

    configure_logging(get_settings().log_level)
    if args.list:
        asyncio.run(show_jobs())
        return
    jobs = [JOBS_BY_NAME[name] for name in args.job] if args.job else JOBS
    try:
        asyncio.run(run_once(jobs) if args.once else run_forever(jobs))
    except KeyboardInterrupt:
        print("Worker stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
