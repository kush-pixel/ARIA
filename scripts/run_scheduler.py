"""CLI script: manually trigger the ARIA 7:30 AM briefing scheduler.

Enqueues briefing_generation jobs for all monitoring-active patients whose
next_appointment falls on the target date and who do not yet have a briefing
row for that date.  This is the same logic fired automatically by APScheduler
at 07:30 UTC when run_worker.py is running.

Use cases
---------
- Demo mode: fire the scheduler without waiting for 07:30 UTC.
- Recovery: re-enqueue jobs after a failed overnight sweep.
- Testing: target a specific date with --date to check scheduler logic.

Usage (from project root, with the aria conda environment active):
    conda activate aria
    python scripts/run_scheduler.py
    python scripts/run_scheduler.py --date 2026-05-01

Needs: DATABASE_URL set in backend/.env
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

# Ensure the backend package is importable when run from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = PROJECT_ROOT / "backend"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

# Load DATABASE_URL and other settings from backend/.env before importing
# app modules that read config at import time.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_SRC / ".env")

from app.services.worker.scheduler import enqueue_briefing_jobs  # noqa: E402
from app.utils.logging_utils import get_logger  # noqa: E402

logger = get_logger(__name__)


async def _run(target_date: date) -> None:
    """Invoke enqueue_briefing_jobs() and print a summary."""
    print(f"Triggering scheduler for date: {target_date.isoformat()}")
    enqueued = await enqueue_briefing_jobs(target_date=target_date)
    if enqueued == 0:
        print(
            "No briefing jobs enqueued — either no patients have appointments "
            f"on {target_date.isoformat()}, or briefings already exist for today."
        )
    else:
        print(f"Enqueued {enqueued} briefing_generation job(s).")
        print(
            "Start run_worker.py (if not already running) to process the queued jobs."
        )


def main() -> None:
    """Parse CLI args and run the scheduler trigger."""
    parser = argparse.ArgumentParser(
        description=(
            "Manually trigger the ARIA 7:30 AM briefing scheduler. "
            "Enqueues briefing_generation jobs for appointment-day patients."
        )
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Target appointment date to check (default: today UTC). "
            "Example: --date 2026-05-01"
        ),
    )
    args = parser.parse_args()

    if args.date is not None:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(
                f"ERROR: --date must be in YYYY-MM-DD format, got: {args.date!r}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        target_date = date.today()

    asyncio.run(_run(target_date))


if __name__ == "__main__":
    main()
