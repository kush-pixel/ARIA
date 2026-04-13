"""CLI script: start the ARIA background worker and 7:30 AM briefing scheduler.

What was implemented
---------------------
- Starts WorkerProcessor (polls processing_jobs every 30 seconds).
- Registers an APScheduler cron job that fires enqueue_briefing_jobs()
  at 7:30 AM UTC every day — identical to the 7:30 AM briefing enqueue
  described in the ARIA specification.
- Demo mode: the scheduler is also triggerable on demand via
  POST /api/admin/trigger-scheduler (handled in the API layer, not here).
- Handles Ctrl+C (KeyboardInterrupt) gracefully: stops the worker loop
  and shuts down the scheduler cleanly.

Usage (from project root, with the aria conda environment active):
    conda activate aria
    python scripts/run_worker.py

Keep this script running in a separate terminal while testing or demoing.
The worker must be running for briefing_generation and pattern_recompute
jobs enqueued by the API to be picked up.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure the backend package is importable when run from the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = PROJECT_ROOT / "backend"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

# Load DATABASE_URL and other settings from backend/.env before importing
# app modules that read config at import time
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_SRC / ".env")

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402

from app.services.worker.processor import WorkerProcessor  # noqa: E402
from app.services.worker.scheduler import enqueue_briefing_jobs  # noqa: E402
from app.utils.logging_utils import get_logger  # noqa: E402

logger = get_logger(__name__)

# 7:30 AM UTC daily — matches the ARIA specification briefing schedule
_SCHEDULER_HOUR: int = 7
_SCHEDULER_MINUTE: int = 30


async def _scheduled_enqueue() -> None:
    """Wrapper called by APScheduler at 7:30 AM UTC.

    Runs enqueue_briefing_jobs() and logs the result. Any exception is caught
    and logged so a single bad run does not kill the scheduler.
    """
    try:
        count = await enqueue_briefing_jobs()
        logger.info("Scheduled 7:30 AM run: enqueued %d briefing job(s)", count)
    except Exception as exc:
        logger.error("Scheduled 7:30 AM run failed: %s", exc)


async def main() -> None:
    """Start the APScheduler cron and the WorkerProcessor polling loop.

    Flow:
    1. Start AsyncIOScheduler with a daily cron at 07:30 UTC.
    2. Start WorkerProcessor.run() — runs indefinitely.
    3. On KeyboardInterrupt (Ctrl+C): stop the processor and scheduler cleanly.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _scheduled_enqueue,
        trigger="cron",
        hour=_SCHEDULER_HOUR,
        minute=_SCHEDULER_MINUTE,
        id="daily_briefing_enqueue",
        name="7:30 AM briefing enqueue",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "APScheduler started — briefing enqueue scheduled at %02d:%02dZ daily",
        _SCHEDULER_HOUR,
        _SCHEDULER_MINUTE,
    )

    processor = WorkerProcessor()

    try:
        await processor.run()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt — shutting down worker")
    finally:
        processor.stop()
        scheduler.shutdown(wait=False)
        logger.info("Worker and scheduler stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
