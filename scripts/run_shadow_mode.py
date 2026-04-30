"""Shadow mode validation: replay patient 1091 clinic visits with ARIA.

Simulates ARIA running continuously using the EXACT same production Layer 1
detectors that run in the real system — not a reimplementation.  Every
evaluation point is replayed by calling the real detectors with an ``as_of``
timestamp set to the end of that calendar day, so each detector only sees
readings and confirmations that existed at that point in time.

Ground truth: PROBLEM_STATUS2_FLAG on the HTN problem record (per CLAUDE.md).
  Flag 3 (Green) → "stable"      ARIA silent = agree, ARIA fired = false positive
  Flag 2 (Yellow) → "concerned"  Physician actively managing — ARIA silent = false negative
  Flag 1 (Red)   → "concerned"   ARIA fired = agree, ARIA silent = false negative
  Flag absent    → "no_ground_truth"  HTN not assessed, excluded from stats

Target: >= 80% agreement on labelled evaluation points.

Usage (from project root, aria conda env active):
    python scripts/run_shadow_mode.py                          # defaults: --patient 1091
    python scripts/run_shadow_mode.py --patient 1015269 \\
        --iemr data/raw/iemr/1015269_data.json
    python scripts/run_shadow_mode.py --patient 2045 \\
        --iemr data/raw/iemr/2045_data.json \\
        --output data/shadow_mode_2045.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: add backend/ to sys.path and load .env before any app imports
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = PROJECT_ROOT / "backend"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_SRC / ".env")

from sqlalchemy import select  # noqa: E402

from app.db.base import AsyncSessionLocal  # noqa: E402
from app.models.clinical_context import ClinicalContext  # noqa: E402
from app.models.reading import Reading  # noqa: E402
from app.services.pattern_engine.adherence_analyzer import run_adherence_analyzer  # noqa: E402
from app.services.pattern_engine.deterioration_detector import run_deterioration_detector  # noqa: E402
from app.services.pattern_engine.gap_detector import run_gap_detector  # noqa: E402
from app.services.pattern_engine.inertia_detector import run_inertia_detector  # noqa: E402
from app.services.pattern_engine.threshold_utils import (  # noqa: E402
    apply_comorbidity_adjustment,
    classify_comorbidity_concern,
    compute_patient_threshold,
)
from app.services.pattern_engine.variability_detector import run_variability_detector  # noqa: E402

_DEFAULT_PATIENT_ID = "1091"
_DEFAULT_IEMR_PATH = PROJECT_ROOT / "data" / "raw" / "iemr" / "1091_data.json"
_DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "shadow_mode_results.json"

# Module-level globals — populated from argparse in main() before _run() executes.
PATIENT_ID: str = _DEFAULT_PATIENT_ID
IEMR_PATH: Path = _DEFAULT_IEMR_PATH
OUTPUT_PATH: Path = _DEFAULT_OUTPUT_PATH

_LABEL_PRIORITY: dict[str, int] = {
    "concerned": 0,
    "grey": 1,
    "stable": 2,
    "no_ground_truth": 3,
}
_FLAG_TO_LABEL: dict[str, str] = {"1": "concerned", "2": "concerned", "3": "stable"}


# ---------------------------------------------------------------------------
# iEMR timeline loader
# ---------------------------------------------------------------------------


def _load_iemr_timeline() -> list[dict[str, Any]]:
    """Load all iEMR visits as a sorted chronological event list."""
    with IEMR_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    visits = data["MED_REC_NO"]["VISIT"]
    events: list[dict[str, Any]] = []

    for v in visits:
        admit_raw = v.get("ADMIT_DATE", "")
        if not admit_raw:
            continue
        visit_date = datetime.strptime(admit_raw[:10], "%m/%d/%Y").date()

        htn = next(
            (p for p in v.get("PROBLEM", [])
             if "HYPERTENSION" in p.get("value", "").upper()),
            None,
        )
        htn_flag_raw = htn.get("PROBLEM_STATUS2_FLAG") if htn else None
        htn_flag = str(htn_flag_raw) if htn_flag_raw is not None else None
        htn_status = (htn.get("PROBLEM_STATUS2") or "").strip() if htn else ""
        htn_assessment = (htn.get("PROBLEM_ASSESSMENT_TEXT") or "").strip() if htn else ""
        physician_label = _FLAG_TO_LABEL.get(htn_flag or "", "no_ground_truth")

        vitals = v.get("VITALS", [])
        bp_vitals = [
            vt for vt in vitals
            if str(vt.get("SYSTOLIC_BP", "")).strip() not in ("", "None", "0", "null")
        ]
        has_bp = bool(bp_vitals)
        bp_systolic = float(bp_vitals[0]["SYSTOLIC_BP"]) if has_bp else None
        bp_diastolic = float(bp_vitals[0].get("DIASTOLIC_BP") or 0) if has_bp else None

        med_dates: list[date] = []
        for med in v.get("MEDICATIONS", []):
            for key in ("MED_DATE_ADDED", "MED_DATE_LAST_MODIFIED"):
                raw = str(med.get(key, "") or "").strip()
                if raw and len(raw) >= 10:
                    try:
                        med_dates.append(datetime.strptime(raw[:10], "%m/%d/%Y").date())
                    except ValueError:
                        pass

        other_problems: list[dict[str, Any]] = []
        for p in v.get("PROBLEM", []):
            if "HYPERTENSION" in p.get("value", "").upper():
                continue
            flag_raw = p.get("PROBLEM_STATUS2_FLAG")
            other_problems.append({
                "name": (p.get("value") or "").strip(),
                "flag": str(flag_raw) if flag_raw is not None else None,
                "status": (p.get("PROBLEM_STATUS2") or "").strip(),
                "assessment": (p.get("PROBLEM_ASSESSMENT_TEXT") or "").strip(),
            })

        followup_items: list[str] = []
        for pl in v.get("PLAN", []):
            if pl.get("PLAN_NEEDS_FOLLOWUP") == "YES":
                label = (pl.get("value") or pl.get("PLAN_TITLE") or "").strip()
                if label:
                    followup_items.append(label)

        events.append({
            "date": visit_date,
            "visit_type": str(v.get("VISIT_TYPE", "")),
            "htn_flag": htn_flag,
            "htn_status": htn_status,
            "htn_assessment": htn_assessment,
            "physician_label": physician_label,
            "has_bp": has_bp,
            "bp_systolic": bp_systolic,
            "bp_diastolic": bp_diastolic,
            "med_dates": med_dates,
            "other_problems": other_problems,
            "followup_items": followup_items,
        })

    events.sort(key=lambda e: e["date"])
    return events


def _labels_from_timeline(timeline: list[dict[str, Any]]) -> dict[date, str]:
    label_map: dict[date, str] = {}
    for event in timeline:
        d = event["date"]
        label = event["physician_label"]
        existing = label_map.get(d, "no_ground_truth")
        if _LABEL_PRIORITY[label] < _LABEL_PRIORITY[existing]:
            label_map[d] = label
        elif d not in label_map:
            label_map[d] = label
    return label_map


def _get_clinical_snapshot_at(
    timeline: list[dict[str, Any]],
    cutoff_date: date,
) -> dict[str, Any]:
    problem_latest: dict[str, dict[str, Any]] = {}
    all_followups: list[str] = []
    for event in timeline:
        if event["date"] >= cutoff_date:
            continue
        for prob in event["other_problems"]:
            name = prob["name"]
            if name:
                problem_latest[name] = prob
        all_followups.extend(event["followup_items"])
    return {
        "active_problems": list(problem_latest.values()),
        "pending_followups": list(dict.fromkeys(all_followups)),
    }


def _get_last_med_change_at(
    timeline: list[dict[str, Any]],
    cutoff_date: date,
) -> date | None:
    latest: date | None = None
    for event in timeline:
        if event["date"] >= cutoff_date:
            continue
        for med_date in event["med_dates"]:
            if latest is None or med_date > latest:
                latest = med_date
    return latest


def _build_evaluation_points(
    timeline: list[dict[str, Any]],
    deduped_bp: list[Any],
    iemr_labels: dict[date, str],
) -> list[dict[str, Any]]:
    bp_dates = {r.effective_datetime.date() for r in deduped_bp}
    points: list[dict[str, Any]] = []

    for r in deduped_bp:
        d = r.effective_datetime.date()
        points.append({
            "date": d,
            "source": "clinic_bp",
            "physician_label": iemr_labels.get(d, "no_ground_truth"),
            "bp_systolic": float(r.systolic_avg),
            "bp_diastolic": float(r.diastolic_avg),
            "htn_status": "",
            "htn_assessment": "",
        })

    seen_no_vitals: set[date] = set()
    for event in timeline:
        d = event["date"]
        if d in bp_dates or d in seen_no_vitals:
            continue
        if event["htn_flag"] is None:
            continue
        if not event["htn_status"] and not event["htn_assessment"]:
            continue
        physician_label = iemr_labels.get(d, event["physician_label"])
        points.append({
            "date": d,
            "source": "no_vitals_assessment",
            "physician_label": physician_label,
            "bp_systolic": None,
            "bp_diastolic": None,
            "htn_status": event["htn_status"],
            "htn_assessment": event["htn_assessment"],
        })
        seen_no_vitals.add(d)

    points.sort(key=lambda p: p["date"])
    return points


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _detectors_to_aria_dict(
    gap: dict,
    inertia: dict,
    adherence: dict,
    deterioration: dict,
    variability: dict,
) -> dict[str, Any]:
    """Map real detector TypedDict results to the shadow mode JSON structure."""
    return {
        "gap": {
            "fired": gap["status"] in ("flag", "urgent"),
            "urgent": gap["status"] == "urgent",
            "gap_days": gap["gap_days"],
            "status": gap["status"],
        },
        "inertia": {
            "fired": inertia["inertia_detected"],
            "avg_systolic": inertia["avg_systolic"],
            "elevated_count": inertia["elevated_count"],
            "duration_days": inertia["duration_days"],
        },
        "adherence": {
            # Pattern A (low adherence + high BP) and Pattern B (high adherence +
            # persistent elevation) both represent clinically useful ARIA signal.
            # Pattern B appears in the briefing agenda as a treatment-review flag;
            # shadow mode evaluation counts it as ARIA contributing useful output.
            "fired": adherence["pattern"] in ("A", "B"),
            "pattern": adherence["pattern"],
            "overall_pct": adherence["adherence_pct"],
            "interpretation": adherence["interpretation"],
        },
        "deterioration": {
            "fired": deterioration["deterioration"],
            "slope": deterioration["slope"],
            "recent_avg": deterioration["recent_avg"],
            "baseline_avg": deterioration["baseline_avg"],
        },
        "variability": {
            # Variability does not write an alert row — informational only
            "fired": False,
            "level": variability["level"],
            "cv_pct": variability["cv_pct"],
        },
    }


def _aria_fired_from(detectors: dict[str, Any]) -> bool:
    """Return True when ARIA contributes clinically useful signal.

    Counts any signal that a clinician would act on or find meaningful:
      - Gap flag OR urgent (any tier) — monitoring gap detected
      - Inertia — sustained elevation with no med change
      - Adherence pattern A (concern) or B (treatment-review) — both briefing signals
      - Deterioration — worsening trend
    Variability is informational only and never counts.
    """
    return (
        detectors["gap"]["fired"]           # flag OR urgent, already set in _detectors_to_aria_dict
        or detectors["inertia"]["fired"]
        or detectors["adherence"]["fired"]  # pattern A or B
        or detectors["deterioration"]["fired"]
        # variability: never writes an alert — excluded
    )


def _build_agenda_flags(detectors: dict[str, Any]) -> tuple[list[str], list[str]]:
    agenda: list[str] = []
    flags: list[str] = []
    if detectors["inertia"]["fired"]:
        avg = detectors["inertia"]["avg_systolic"]
        agenda.append(
            f"Review treatment plan: BP elevated (avg {avg} mmHg) "
            "throughout monitoring period with no recent medication change."
        )
    if detectors["gap"]["urgent"]:
        flags.append(
            f"Reading gap of {detectors['gap']['gap_days']:.1f} days detected — "
            "possible monitoring issue."
        )
    if detectors["adherence"]["pattern"] == "B":
        agenda.append("Possible treatment-review case: high adherence with persistent elevation.")
    if detectors["adherence"]["pattern"] == "A":
        agenda.append("Possible adherence concern: low adherence with elevated BP.")
    if detectors["deterioration"]["fired"]:
        agenda.append("Deterioration trend: sustained worsening over monitoring period.")
    if detectors["variability"]["fired"]:
        level = detectors["variability"]["level"]
        cv = detectors["variability"]["cv_pct"]
        agenda.append(
            f"{level.capitalize()} BP variability detected (CV {cv:.0f}%) — "
            "consider ambulatory monitoring."
        )
    return agenda, flags


# ---------------------------------------------------------------------------
# Best demo window
# ---------------------------------------------------------------------------


def _find_best_demo_window(visits: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1
    for window_size in (5, 4, 3):
        for start in range(len(visits) - window_size + 1):
            window = visits[start: start + window_size]
            concerned = sum(1 for v in window if v["physician_label"] == "concerned")
            fired = sum(1 for v in window if v["with_aria"]["fired"])
            agree = sum(1 for v in window if v["result"] == "agree")
            if concerned >= 2 and fired >= 2 and agree > best_score:
                best_score = agree
                best = {
                    "visit_indices": [v["visit_index"] for v in window],
                    "date_from": window[0]["visit_date"],
                    "date_to": window[-1]["visit_date"],
                    "summary": (
                        f"ARIA would have alerted before {fired} of {window_size} visits "
                        f"where the physician found elevated BP in {concerned} cases"
                    ),
                }
    return best


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------


def _wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p_hat = successes / total
    denom = 1.0 + (z * z) / total
    centre = (p_hat + (z * z) / (2 * total)) / denom
    spread = (z * math.sqrt(p_hat * (1 - p_hat) / total + (z * z) / (4 * total * total))) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def _per_detector_breakdown(labelled_visits: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    breakdown: dict[str, dict[str, int]] = {
        det: {"false_positives": 0, "false_negatives": 0, "disagreements": 0}
        for det in ("gap", "inertia", "deterioration", "adherence", "variability")
    }
    for visit in labelled_visits:
        result = visit["result"]
        if result == "agree":
            continue
        detectors = visit.get("with_aria", {}).get("detectors", {})
        if result == "false_positive":
            if detectors.get("gap", {}).get("urgent"):
                breakdown["gap"]["false_positives"] += 1
                breakdown["gap"]["disagreements"] += 1
            for name in ("inertia", "deterioration", "adherence", "variability"):
                if detectors.get(name, {}).get("fired"):
                    breakdown[name]["false_positives"] += 1
                    breakdown[name]["disagreements"] += 1
        elif result == "false_negative":
            for name in ("gap", "inertia", "deterioration", "adherence", "variability"):
                breakdown[name]["false_negatives"] += 1
                breakdown[name]["disagreements"] += 1
    return breakdown


# ---------------------------------------------------------------------------
# Main async function
# ---------------------------------------------------------------------------


async def _run() -> None:
    # ── Step 1: Load full iEMR timeline ─────────────────────────────────────
    timeline = _load_iemr_timeline()
    bp_events = sum(1 for e in timeline if e["has_bp"])
    print(
        f"Loaded {len(timeline)} iEMR events ({bp_events} with BP, "
        f"{len(timeline) - bp_events} without)"
    )
    iemr_labels = _labels_from_timeline(timeline)

    async with AsyncSessionLocal() as session:
        # ── Step 2: Load clinic readings + clinical context from DB ──────────
        result = await session.execute(
            select(Reading)
            .where(Reading.patient_id == PATIENT_ID, Reading.source == "clinic")
            .order_by(Reading.effective_datetime.asc())
        )
        clinic_readings = list(result.scalars().all())

        ctx_result = await session.execute(
            select(ClinicalContext).where(ClinicalContext.patient_id == PATIENT_ID)
        )
        ctx = ctx_result.scalar_one_or_none()

    if not clinic_readings:
        print(f"ERROR: No clinic readings found for patient {PATIENT_ID}.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate: keep last reading per date
    date_to_reading: dict[date, Any] = {}
    for r in clinic_readings:
        date_to_reading[r.effective_datetime.date()] = r
    deduped = sorted(date_to_reading.values(), key=lambda r: r.effective_datetime)
    print(
        f"Loaded {len(clinic_readings)} clinic readings → {len(deduped)} unique visit dates "
        f"({clinic_readings[0].effective_datetime.date()} to "
        f"{clinic_readings[-1].effective_datetime.date()})"
    )

    # ── Step 2b: Build evaluation points ────────────────────────────────────
    eval_points = _build_evaluation_points(timeline, deduped, iemr_labels)
    bp_eval = sum(1 for p in eval_points if p["source"] == "clinic_bp")
    nv_eval = sum(1 for p in eval_points if p["source"] == "no_vitals_assessment")
    print(
        f"Built {len(eval_points)} evaluation points: "
        f"{bp_eval} clinic BP + {nv_eval} no-vitals HTN assessments"
    )

    # Compute display threshold from production utilities (same as what detectors use)
    historic_bp = ctx.historic_bp_systolic if ctx else None
    problem_codes = ctx.problem_codes if ctx else None
    display_threshold, _ = compute_patient_threshold(historic_bp)
    concern_state_ctx = classify_comorbidity_concern(problem_codes)
    display_threshold_adj, _ = apply_comorbidity_adjustment(display_threshold, concern_state_ctx)
    print(
        f"Patient-adaptive threshold: {display_threshold} mmHg "
        f"(adjusted {display_threshold_adj} mmHg with comorbidity)"
    )
    print()

    visits: list[dict[str, Any]] = []
    skipped = 0
    prev_eval_date: date | None = None

    # ── Step 3: Process each evaluation point using real production detectors ─
    async with AsyncSessionLocal() as session:
        for visit_idx, eval_point in enumerate(eval_points):
            visit_date = eval_point["date"]
            source = eval_point["source"]
            physician_label = eval_point["physician_label"]
            bp_systolic = eval_point["bp_systolic"]
            bp_diastolic = eval_point["bp_diastolic"]

            # as_of = end of the evaluation calendar day in UTC
            as_of = datetime(
                visit_date.year, visit_date.month, visit_date.day,
                23, 59, 59, tzinfo=UTC,
            )

            days_since_prior = (visit_date - prev_eval_date).days if prev_eval_date else 0
            src_tag = "BP   " if source == "clinic_bp" else "PHONE"
            bp_disp = (
                f"BP={bp_systolic:.0f}/{bp_diastolic:.0f}"
                if bp_systolic is not None else "BP=—/—  "
            )

            # Run all real Layer 1 detectors — read-only, no DB writes
            gap = await run_gap_detector(session, PATIENT_ID, as_of=as_of)
            inertia = await run_inertia_detector(session, PATIENT_ID, as_of=as_of)
            adherence = await run_adherence_analyzer(session, PATIENT_ID, as_of=as_of)
            deterioration = await run_deterioration_detector(session, PATIENT_ID, as_of=as_of)
            variability = await run_variability_detector(session, PATIENT_ID, as_of=as_of)

            # Skip if gap detector sees no data (insufficient readings before this date)
            if gap["gap_days"] == float("inf"):
                skipped += 1
                print(
                    f"  [{visit_date}] [{src_tag}] {bp_disp}"
                    f"  Label={physician_label:<20}  SKIPPED (no readings before this date)"
                )
                prev_eval_date = visit_date
                continue

            detectors = _detectors_to_aria_dict(gap, inertia, adherence, deterioration, variability)
            aria_fired = _aria_fired_from(detectors)
            visit_agenda, urgent_flags = _build_agenda_flags(detectors)

            if physician_label == "no_ground_truth":
                result_label = "no_ground_truth"
            elif physician_label == "concerned" and not aria_fired:
                result_label = "false_negative"
            elif physician_label == "stable" and aria_fired:
                result_label = "false_positive"
            else:
                result_label = "agree"

            # Fetch actual readings in the detector window for output display
            window_start_dt = as_of - timedelta(days=28)
            readings_result = await session.execute(
                select(
                    Reading.effective_datetime,
                    Reading.systolic_avg,
                    Reading.diastolic_avg,
                    Reading.session,
                )
                .where(
                    Reading.patient_id == PATIENT_ID,
                    Reading.source != "clinic",
                    Reading.effective_datetime >= window_start_dt,
                    Reading.effective_datetime <= as_of,
                )
                .order_by(Reading.effective_datetime.asc())
            )
            window_readings = [
                {
                    "date": row.effective_datetime.date().isoformat(),
                    "systolic_avg": float(row.systolic_avg),
                    "diastolic_avg": float(row.diastolic_avg),
                    "session": row.session,
                }
                for row in readings_result
            ]

            # Clinical context from iEMR timeline (for without_aria display)
            clinical_snapshot = _get_clinical_snapshot_at(timeline, visit_date)
            prior_bp_readings = [r for r in deduped if r.effective_datetime.date() < visit_date]
            last_clinic = prior_bp_readings[-1] if prior_bp_readings else None

            without_aria: dict[str, Any] = {
                "last_clinic_systolic": float(last_clinic.systolic_avg) if last_clinic else None,
                "last_clinic_date": last_clinic.effective_datetime.date().isoformat() if last_clinic else None,
                "days_since_last_visit": days_since_prior,
                "medications": (ctx.current_medications or []) if ctx else [],
                "known_problems": (ctx.active_problems or []) if ctx else [],
                "other_active_problems": clinical_snapshot["active_problems"],
                "pending_followups": clinical_snapshot["pending_followups"],
            }
            with_aria: dict[str, Any] = {
                "fired": aria_fired,
                "detectors": detectors,
                "visit_agenda": visit_agenda,
                "urgent_flags": urgent_flags,
                "adherence_pct": adherence["adherence_pct"],
            }

            visits.append({
                "visit_index": visit_idx,
                "visit_date": visit_date.isoformat(),
                "source": source,
                "systolic": bp_systolic,
                "diastolic": bp_diastolic,
                "physician_label": physician_label,
                "days_since_prior_visit": days_since_prior,
                "without_aria": without_aria,
                "with_aria": with_aria,
                "synthetic_readings": window_readings,
                "result": result_label,
                "between_visit_alerts": [],
            })

            aria_str = "FIRED " if aria_fired else "SILENT"
            detectors_str = "/".join(
                k.upper()[:3]
                for k, v in detectors.items()
                if (v.get("urgent") if k == "gap" else v.get("fired"))
            ) or "—"
            print(
                f"  [{visit_date}] [{src_tag}] {bp_disp}"
                f"  {f'Label={physician_label}':<26}"
                f"  gap={gap['gap_days']:.1f}d"
                f"  ARIA: {aria_str} [{detectors_str:<15}]"
                f"  Result: {result_label.upper().replace('_', ' ')}"
            )
            prev_eval_date = visit_date

        # ── Step 4: Continuous between-visit monitoring scan ─────────────────
        for j in range(len(visits) - 1):
            v_curr = visits[j]
            v_next = visits[j + 1]
            date_a = date.fromisoformat(v_curr["visit_date"])
            date_b = date.fromisoformat(v_next["visit_date"])
            inter_gap = (date_b - date_a).days

            if inter_gap < 14:
                continue

            earliest: dict[str, Any] | None = None
            check_date = date_a + timedelta(days=14)

            while check_date < date_b:
                check_as_of = datetime(
                    check_date.year, check_date.month, check_date.day,
                    23, 59, 59, tzinfo=UTC,
                )
                gap_mid = await run_gap_detector(session, PATIENT_ID, as_of=check_as_of)
                ine = await run_inertia_detector(session, PATIENT_ID, as_of=check_as_of)
                det = await run_deterioration_detector(session, PATIENT_ID, as_of=check_as_of)
                adh = await run_adherence_analyzer(session, PATIENT_ID, as_of=check_as_of)

                fired_types = []
                reasons: list[str] = []

                if gap_mid["status"] == "urgent":
                    fired_types.append("GAP")
                    reasons.append(
                        f"No readings for {gap_mid['gap_days']:.0f} consecutive days."
                    )
                if ine["inertia_detected"]:
                    fired_types.append("INE")
                    reasons.append(
                        f"Sustained elevated BP (avg {ine['avg_systolic']} mmHg) "
                        f"with no medication change — possible therapeutic inertia."
                    )
                if det["deterioration"]:
                    fired_types.append("DET")
                    reasons.append(
                        f"Worsening BP trend (slope {det['slope']:+.2f} mmHg/day)."
                    )
                if adh["pattern"] in ("A", "B"):
                    fired_types.append("ADH")
                    reasons.append(adh["interpretation"])

                if fired_types:
                    days_before = (date_b - check_date).days
                    earliest = {
                        "alert_date": check_date.isoformat(),
                        "alert_type": "|".join(fired_types),
                        "days_before_visit": days_before,
                        "reasons": reasons,
                        "message": "; ".join(reasons),
                    }
                    break

                check_date += timedelta(days=7)

            if earliest:
                v_next["between_visit_alerts"].append(earliest)

    # ── Step 5: Best demo window ─────────────────────────────────────────────
    best_window = _find_best_demo_window(visits)

    # ── Step 6: Summary stats ────────────────────────────────────────────────
    bp_visits = [v for v in visits if v["source"] == "clinic_bp"]
    nv_visits = [v for v in visits if v["source"] == "no_vitals_assessment"]
    labelled = [v for v in visits if v["result"] != "no_ground_truth"]
    concerned_visits = [v for v in labelled if v["physician_label"] == "concerned"]
    stable_visits = [v for v in labelled if v["physician_label"] == "stable"]
    no_gt_visits = [v for v in visits if v["result"] == "no_ground_truth"]
    agreements = sum(1 for v in labelled if v["result"] == "agree")
    false_negatives = sum(1 for v in labelled if v["result"] == "false_negative")
    false_positives = sum(1 for v in labelled if v["result"] == "false_positive")
    agreement_pct = (agreements / len(labelled) * 100) if labelled else 0.0
    passed = agreement_pct >= 80.0

    independent_count = sum(
        1 for v in labelled if (v["days_since_prior_visit"] or 0) >= 28
    )
    overlapping_count = len(labelled) - independent_count
    ci_lower, ci_upper = _wilson_ci(agreements, len(labelled))
    detector_breakdown = _per_detector_breakdown(labelled)

    # ── Step 7: Save ─────────────────────────────────────────────────────────
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "patient_id": PATIENT_ID,
        "total_eval_points": len(visits) + skipped,
        "skipped": skipped,
        "clinic_bp_points": len(bp_visits),
        "no_vitals_points": len(nv_visits),
        "with_ground_truth": len(labelled),
        "concerned_flag1_or_2": len(concerned_visits),
        "stable_flag3": len(stable_visits),
        "no_ground_truth": len(no_gt_visits),
        "agreements": agreements,
        "false_negatives": false_negatives,
        "false_positives": false_positives,
        "agreement_pct": round(agreement_pct, 1),
        "agreement_ci_95_lower_pct": round(ci_lower * 100.0, 1),
        "agreement_ci_95_upper_pct": round(ci_upper * 100.0, 1),
        "fully_independent_eval_points": independent_count,
        "overlapping_eval_points": overlapping_count,
        "detector_breakdown": detector_breakdown,
        "passed": passed,
        "best_demo_window": best_window,
        "visits": visits,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ── Step 8: Print summary ─────────────────────────────────────────────────
    print()
    print("=== Shadow Mode Results ===")
    print(f"Total evaluation points: {len(visits) + skipped} ({skipped} skipped — no readings before date)")
    print(f"  - From clinic BP readings:          {len(bp_visits)}")
    print(f"  - From no-vitals HTN assessments:   {len(nv_visits)}")
    print(f"With ground truth (flag present): {len(labelled)}")
    print(f"  - Concerned (flag=1 or 2): {len(concerned_visits)}")
    print(f"  - Stable    (flag=3):      {len(stable_visits)}")
    print(f"No ground truth (flag absent):    {len(no_gt_visits)} (excluded)")
    print()
    print(f"Agreement on labelled visits: {agreements}/{len(labelled)} ({agreement_pct:.1f}%)")
    print(f"  95% Wilson CI: [{ci_lower * 100:.1f}%, {ci_upper * 100:.1f}%]")
    print(
        f"  Fully independent eval points (>= 28d apart): {independent_count}/{len(labelled)} "
        f"({overlapping_count} overlap with prior window)"
    )
    print(f"False negatives: {false_negatives}")
    print(f"False positives: {false_positives}")
    if false_negatives + false_positives > 0:
        print()
        print("Per-detector breakdown of disagreements:")
        for det, stats in detector_breakdown.items():
            fp = stats["false_positives"]
            fn = stats["false_negatives"]
            total = stats["disagreements"]
            print(f"  {det:14s} disagreements={total:2d}  (FP={fp}, FN={fn})")
    print()
    print("PASSED ✓" if passed else "FAILED ✗")
    if best_window:
        print(f"Best demo window: {best_window['date_from']} to {best_window['date_to']}")
    print(f"\nResults saved to {OUTPUT_PATH}")


def main() -> None:
    global PATIENT_ID, IEMR_PATH, OUTPUT_PATH

    parser = argparse.ArgumentParser(
        description="ARIA shadow mode validation using real production detectors.",
    )
    parser.add_argument("--patient", default=_DEFAULT_PATIENT_ID)
    parser.add_argument("--iemr", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    PATIENT_ID = str(args.patient)

    IEMR_PATH = (
        Path(args.iemr)
        if args.iemr is not None
        else PROJECT_ROOT / "data" / "raw" / "iemr" / f"{PATIENT_ID}_data.json"
    )
    OUTPUT_PATH = (
        Path(args.output)
        if args.output is not None
        else (
            _DEFAULT_OUTPUT_PATH
            if PATIENT_ID == _DEFAULT_PATIENT_ID
            else PROJECT_ROOT / "data" / f"shadow_mode_{PATIENT_ID}.json"
        )
    )

    if not IEMR_PATH.exists():
        print(f"ERROR: iEMR file not found: {IEMR_PATH}", file=sys.stderr)
        sys.exit(2)

    print(f"Patient:    {PATIENT_ID}")
    print(f"iEMR file:  {IEMR_PATH}")
    print(f"Output:     {OUTPUT_PATH}")
    print()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
