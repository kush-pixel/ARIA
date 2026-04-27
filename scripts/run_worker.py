"""CLI script: start the ARIA background worker and scheduled cron jobs.

What was implemented
---------------------
- Starts WorkerProcessor with LISTEN/NOTIFY on the 'aria_jobs' channel (Fix 60).
  Falls back to 60-second polling if the asyncpg listener connection fails.
- Registers two APScheduler cron jobs:
    7:30 AM UTC  — enqueue_briefing_jobs() for appointment-day patients.
    midnight UTC — enqueue_pattern_recompute_sweep() for all active patients
                   so gap counters, risk scores, and alert flags stay current
                   between appointments.
- Demo mode: the briefing scheduler is also triggerable on demand via
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
from app.services.worker.scheduler import (  # noqa: E402
    enqueue_briefing_jobs,
    enqueue_pattern_recompute_sweep,
)
from app.config import settings  # noqa: E402
from app.utils.logging_utils import get_logger  # noqa: E402

logger = get_logger(__name__)

# Cron times (UTC)
_BRIEFING_HOUR: int = 7
_BRIEFING_MINUTE: int = 30
_SWEEP_HOUR: int = 0
_SWEEP_MINUTE: int = 0


async def _scheduled_briefing_enqueue() -> None:
    """Wrapper called by APScheduler at 7:30 AM UTC.

    Runs enqueue_briefing_jobs() and logs the result. Any exception is caught
    and logged so a single bad run does not kill the scheduler.
    """
    try:
        count = await enqueue_briefing_jobs()
        logger.info("Scheduled 7:30 AM run: enqueued %d briefing job(s)", count)
    except Exception as exc:
        logger.error("Scheduled 7:30 AM run failed: %s", exc)


async def _scheduled_pattern_sweep() -> None:
    """Wrapper called by APScheduler at midnight UTC.

    Runs enqueue_pattern_recompute_sweep() for all monitoring-active patients
    so gap counters, risk scores, and alert flags remain current between
    appointment days. Any exception is caught and logged.
    """
    try:
        count = await enqueue_pattern_recompute_sweep()
        logger.info("Scheduled midnight sweep: enqueued %d pattern_recompute job(s)", count)
    except Exception as exc:
        logger.error("Scheduled midnight sweep failed: %s", exc)


async def main() -> None:
    """Start the APScheduler cron jobs and the WorkerProcessor polling loop.

    Flow:
    1. Start AsyncIOScheduler with two daily crons:
         - 07:30 UTC: enqueue briefing_generation for appointment-day patients.
         - 00:00 UTC: enqueue pattern_recompute for ALL monitoring-active patients.
    2. Start WorkerProcessor.run() — runs indefinitely.
    3. On KeyboardInterrupt (Ctrl+C): stop the processor and scheduler cleanly.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _scheduled_briefing_enqueue,
        trigger="cron",
        hour=_BRIEFING_HOUR,
        minute=_BRIEFING_MINUTE,
        id="daily_briefing_enqueue",
        name="7:30 AM briefing enqueue",
        replace_existing=True,
    )
    scheduler.add_job(
        _scheduled_pattern_sweep,
        trigger="cron",
        hour=_SWEEP_HOUR,
        minute=_SWEEP_MINUTE,
        id="midnight_pattern_sweep",
        name="Midnight pattern_recompute sweep",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "APScheduler started — briefing enqueue at %02d:%02dZ, pattern sweep at %02d:%02dZ",
        _BRIEFING_HOUR,
        _BRIEFING_MINUTE,
        _SWEEP_HOUR,
        _SWEEP_MINUTE,
    )

    # Strip the SQLAlchemy dialect prefix so asyncpg can parse the raw URL (Fix 60)
    raw_db_url = settings.database_url.replace("+asyncpg", "")
    processor = WorkerProcessor(listen_url=raw_db_url)

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
