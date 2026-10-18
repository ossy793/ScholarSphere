"""Kept so the old command still works: `python -m app.worker.outbox` now starts the full
worker (emails and every other job). Prefer `python -m app.worker`.

    python -m app.worker.outbox --once   # one pass of the email job only, then exit
"""

import argparse
import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.worker.jobs import JOBS, JOBS_BY_NAME
from app.worker.runner import run_forever, run_once

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    try:
        if args.once:
            asyncio.run(run_once([JOBS_BY_NAME["email_outbox"]]))
        else:
            asyncio.run(run_forever(JOBS))
    except KeyboardInterrupt:
        print("Worker stopped.")
