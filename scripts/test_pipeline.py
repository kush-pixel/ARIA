"""Quick full-pipeline smoke test: Layer 1 + Layer 2 for patient 1091."""
import asyncio
import sys
import os

backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, backend_dir)
os.chdir(backend_dir)  # config.py looks for .env relative to cwd

from app.db.base import AsyncSessionLocal
from app.services.pattern_engine.gap_detector import run_gap_detector
from app.services.pattern_engine.inertia_detector import run_inertia_detector
from app.services.pattern_engine.adherence_analyzer import run_adherence_analyzer
from app.services.pattern_engine.deterioration_detector import run_deterioration_detector
from app.services.pattern_engine.risk_scorer import compute_risk_score


async def main() -> None:
    pid = "1091"
    async with AsyncSessionLocal() as session:
        print("=== Layer 1 — Detectors ===")

        gap = await run_gap_detector(session, pid)
        print(f"Gap:            gap_days={gap['gap_days']}  status={gap['status']}")

        inertia = await run_inertia_detector(session, pid)
        print(f"Inertia:        detected={inertia['inertia_detected']}  avg_systolic={inertia.get('avg_systolic')}  duration_days={inertia.get('duration_days')}")

        adh = await run_adherence_analyzer(session, pid)
        print(f"Adherence:      pattern={adh['pattern']}  adherence_pct={adh.get('adherence_pct')}  interpretation={adh.get('interpretation')}")

        det = await run_deterioration_detector(session, pid)
        print(f"Deterioration:  detected={det['deterioration']}  slope={det.get('slope')}  recent_avg={det.get('recent_avg')}")

        print()
        print("=== Layer 2 — Risk Scorer ===")
        score = await compute_risk_score(pid, session)
        print(f"risk_score:     {score}")
        await session.commit()

        # Verify risk_score_computed_at was written (Fix 61)
        from sqlalchemy import select, text
        result = await session.execute(
            text("SELECT risk_score, risk_score_computed_at FROM patients WHERE patient_id = :pid"),
            {"pid": pid},
        )
        row = result.fetchone()
        print(f"DB risk_score:  {row[0]}")
        print(f"computed_at:    {row[1]}  (Fix 61 — should be non-null)")


asyncio.run(main())
