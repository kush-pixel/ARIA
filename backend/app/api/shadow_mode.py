"""Shadow mode API routes for ARIA.

GET /api/shadow-mode/results  — return cached shadow mode validation results.

Results are produced by running scripts/run_shadow_mode.py, which writes
data/shadow_mode_results.json.  This endpoint reads that file; if missing it
returns 404 so the frontend can display the "run the script first" message.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["shadow_mode"])

# Resolve path relative to this file: backend/app/api/ -> ../../../../data/
# shadow_mode.py is at backend/app/api/shadow_mode.py
# .parent × 4 -> project root
RESULTS_PATH = (
    Path(__file__).parent.parent.parent.parent / "data" / "shadow_mode_results.json"
)


@router.get("/shadow-mode/results")
async def get_shadow_mode_results() -> dict:
    """Return shadow mode validation results from the cached JSON file.

    Returns:
        Full shadow mode results payload as JSON.

    Raises:
        HTTPException 404: If run_shadow_mode.py has not been run yet.
    """
    if not RESULTS_PATH.exists():
        logger.warning("Shadow mode results file not found at %s", RESULTS_PATH)
        raise HTTPException(
            status_code=404,
            detail="Run python scripts/run_shadow_mode.py first",
        )
    try:
        return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read shadow mode results: %s", exc)
        raise HTTPException(status_code=500, detail="Results file is unreadable") from exc
