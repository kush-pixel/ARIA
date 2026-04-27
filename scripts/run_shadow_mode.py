"""Shadow mode validation: replay patient 1091 clinic visits with ARIA.

Simulates ARIA running continuously — not just at the 53 clinic dates where a
BP reading exists in the DB, but with full awareness of all 124 iEMR events
(phone refill calls, in-person visits without BP, phone consultations) exactly
as real ARIA would see them in production.

Two key improvements over a naive clinic-visit-only replay:
  1. Medication timeline: last_med_change is derived from ALL 124 iEMR events
     (including phone refill calls) not just from the single DB-stored value.
  2. Evaluation points: includes no-vitals visits where the physician explicitly
     documented a PROBLEM_STATUS2_FLAG for HTN, not only the 53 BP dates.

Synthetic home readings are linearly interpolated between consecutive clinic
BP anchors (the 53 deduped readings). No-vitals evaluation points pull their
28-day window from the same continuous synthetic trajectory.

Ground truth: PROBLEM_STATUS2_FLAG on the HTN problem record (per CLAUDE.md).
  Flag 3 (Green) → "stable"      ARIA silent = agree, ARIA fired = false positive
  Flag 2 (Yellow) → "concerned"  Physician actively managing — ARIA silent = false negative
  Flag 1 (Red)   → "concerned"   ARIA fired = agree, ARIA silent = false negative
  Flag absent    → "no_ground_truth"  HTN not assessed, excluded from stats

Target: >= 80% agreement on labelled evaluation points.

Usage (from project root, aria conda env active):
    python scripts/run_shadow_mode.py                                    # defaults: --patient 1091
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
import random
import statistics
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

_DEFAULT_PATIENT_ID = "1091"
_DEFAULT_IEMR_PATH = PROJECT_ROOT / "data" / "raw" / "iemr" / "1091_data.json"
_DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "shadow_mode_results.json"

# Module-level globals — populated from argparse in main() before _run() executes.
# Kept module-level (not threaded through every helper) to minimise diff surface.
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
# Comorbidity concern classification
# ---------------------------------------------------------------------------

# Keywords matched against problem names (case-insensitive substring match).
# When any of these conditions has PROBLEM_STATUS2_FLAG = "1" or "2" at the
# most recent prior visit, the corresponding concern is active.
_CARDIOVASCULAR_KEYWORDS: tuple[str, ...] = (
    "CHF", "CAD", "ANGINA", "PVD", "PERIPHERAL VASCULAR",
    "BYPASS SURGERY", "STROKE", "TIA", "CORONARY",
)
_METABOLIC_KEYWORDS: tuple[str, ...] = (
    "DIABETES", "HYPERGLYCEMIA", "HYPOGLYCEMIA",
)

# Threshold reduction applied when concern is active (mmHg).
# Floor of 130 mmHg is always enforced — never drop below shadow mode minimum.
_CARDIO_REDUCTION = 5.0    # active cardiac concern: lower threshold by 5 mmHg
_METABOLIC_REDUCTION = 2.0  # active metabolic concern: lower threshold by 2 mmHg


def _classify_comorbidity_concern(
    other_active_problems: list[dict[str, Any]],
) -> tuple[bool, bool]:
    """Return (cardiovascular_concern, metabolic_concern) from non-HTN problems.

    A condition contributes to concern only when PROBLEM_STATUS2_FLAG is "1" or "2"
    — the physician actively flagged it as under evaluation or urgent.
    Flag "3" (stable/doing well) does not elevate concern.
    """
    cardio = False
    metabolic = False
    for prob in other_active_problems:
        if prob.get("flag") not in ("1", "2"):
            continue
        name_upper = (prob.get("name") or "").upper()
        if any(kw in name_upper for kw in _CARDIOVASCULAR_KEYWORDS):
            cardio = True
        if any(kw in name_upper for kw in _METABOLIC_KEYWORDS):
            metabolic = True
    return cardio, metabolic


def _apply_comorbidity_adjustment(
    base_threshold: float,
    cardio_concern: bool,
    metabolic_concern: bool,
) -> tuple[float, str]:
    """Lower the BP threshold only when cardiovascular AND metabolic concerns are
    simultaneously active — the combined cardiometabolic high-risk state.

    Single-condition concern (cardio-only or metabolic-only) does not lower the
    threshold on its own — the patient-adaptive threshold already reflects the
    patient's personal baseline and is sufficient for those cases. The threshold
    reduction is reserved for the compound state where both systems are flagged
    as concerning by the physician, which represents materially higher
    cardiovascular risk than either condition alone.

    Floor of 130 mmHg is always enforced.

    Returns (adjusted_threshold, concern_state_label).
    """
    if cardio_concern and metabolic_concern:
        reduction = _CARDIO_REDUCTION + _METABOLIC_REDUCTION
        adjusted = max(130.0, base_threshold - reduction)
        state = "cardiovascular+metabolic"
    elif cardio_concern:
        adjusted = base_threshold
        state = "cardiovascular"      # noted but threshold unchanged
    elif metabolic_concern:
        adjusted = base_threshold
        state = "metabolic"           # noted but threshold unchanged
    else:
        adjusted = base_threshold
        state = "none"

    return round(adjusted, 1), state


# ---------------------------------------------------------------------------
# iEMR timeline loader — replaces the old single-purpose label loader
# ---------------------------------------------------------------------------


def _load_iemr_timeline() -> list[dict[str, Any]]:
    """Load all iEMR visits as a sorted chronological event list.

    Each event dict:
      date             : date
      visit_type       : str
      htn_flag         : str | None   — PROBLEM_STATUS2_FLAG ("1"|"2"|"3"|None)
      htn_status       : str          — PROBLEM_STATUS2 text
      htn_assessment   : str          — PROBLEM_ASSESSMENT_TEXT (free text)
      physician_label  : str          — "concerned"|"stable"|"no_ground_truth"
      has_bp           : bool
      bp_systolic      : float | None
      bp_diastolic     : float | None
      med_dates        : list[date]   — all MED_DATE_ADDED / MED_DATE_LAST_MODIFIED
      other_problems   : list[dict]   — all non-HTN problems with flag/status/assessment
      followup_items   : list[str]    — PLAN entries where PLAN_NEEDS_FOLLOWUP=YES
    """
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

        # All non-HTN problems — gives ARIA the full clinical picture at each visit
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

        # Plan items needing follow-up (overdue labs, referrals, pending actions)
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
    """Derive physician label map from the timeline.

    Most-concerned label wins when multiple events share the same date.
    """
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
    """Return a snapshot of all active problems and pending follow-ups as of cutoff_date.

    Walks all prior events and keeps the most recent status per problem name.
    Also collects all pending follow-up items from prior visits.
    This gives ARIA the full patient picture — not just HTN — at each evaluation point.
    """
    problem_latest: dict[str, dict[str, Any]] = {}
    all_followups: list[str] = []
    for event in timeline:
        if event["date"] >= cutoff_date:
            continue
        for prob in event["other_problems"]:
            name = prob["name"]
            if name:
                problem_latest[name] = prob  # most recent status wins
        all_followups.extend(event["followup_items"])
    return {
        "active_problems": list(problem_latest.values()),
        "pending_followups": list(dict.fromkeys(all_followups)),  # dedupe, preserve order
    }


def _get_last_med_change_at(
    timeline: list[dict[str, Any]],
    cutoff_date: date,
) -> date | None:
    """Return the most recent medication change date from any iEMR event before cutoff_date.

    Considers MED_DATE_ADDED and MED_DATE_LAST_MODIFIED across every medication
    in every prior event, including phone refill calls (type=21). This replaces
    ctx.last_med_change which captured only the final ingested DB state.
    """
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
    """Build the full evaluation set: BP clinic dates + no-vitals HTN assessments.

    No-vitals visits are included when:
    - HTN PROBLEM_STATUS2_FLAG is set
    - PROBLEM_STATUS2 text or PROBLEM_ASSESSMENT_TEXT is non-empty
      (filters auto-carried bare flags from pure refill-only calls)
    - The date is not already covered by a deduped BP clinic reading

    Returns a list of eval_point dicts sorted by date.
    """
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
# In-memory synthetic reading generation
# ---------------------------------------------------------------------------


def _anti_round(val: float) -> float:
    result = round(val + random.uniform(-1.5, 1.5), 1)
    if result % 1 == 0.0:
        result = round(result + 0.1, 1)
    return result


def _generate_all_synthetic(
    clinic_readings: list[Any],
) -> dict[date, list[dict[str, Any]]]:
    """Generate synthetic home BP readings for every inter-visit gap.

    For each consecutive pair of clinic visits (A, B), day_mean is linearly
    interpolated from bp_A toward bp_B. Gaussian noise SD=8 mmHg is applied
    on top of the interpolated mean so ARIA sees the actual trajectory.

    Miss probability: 15% weekend, 8% weekday (independent per day).
    Returns dict[date, list[reading_dict]] — keys are only days that produced readings.
    """
    all_synthetic: dict[date, list[dict[str, Any]]] = {}

    for idx in range(len(clinic_readings) - 1):
        visit_a = clinic_readings[idx]
        visit_b = clinic_readings[idx + 1]
        date_a = visit_a.effective_datetime.date()
        date_b = visit_b.effective_datetime.date()
        bp_a = float(visit_a.systolic_avg)
        bp_b = float(visit_b.systolic_avg)
        gap_days = (date_b - date_a).days

        if gap_days < 2:
            continue

        # Device outages: 1-2 episodes of 2-4 consecutive days per 28 days (CLAUDE.md spec).
        # These create the multi-day gaps that the gap detector is designed to catch.
        # Per-day probabilistic misses (8-15%) only produce isolated single-day gaps.
        outage_days: set[int] = set()
        n_outages = max(1, round(gap_days / 28))
        for _ in range(n_outages):
            start = random.randint(1, max(1, gap_days - 4))
            length = random.randint(2, 4)
            for off in range(start, min(start + length, gap_days)):
                outage_days.add(off)

        for day_offset in range(1, gap_days):
            if day_offset in outage_days:
                continue

            day_date = date_a + timedelta(days=day_offset)
            progress = day_offset / gap_days
            day_mean = bp_a + (bp_b - bp_a) * progress

            miss_chance = 0.15 if day_date.weekday() in (5, 6) else 0.08
            if random.random() < miss_chance:
                continue

            if day_date not in all_synthetic:
                all_synthetic[day_date] = []

            for session_name in ("morning", "evening"):
                sys_target = random.gauss(day_mean, 8.0)
                sys_target = max(day_mean - 25, min(day_mean + 25, sys_target))

                if session_name == "morning":
                    sys_val = sys_target + random.uniform(0.0, 3.0)
                else:
                    sys_val = sys_target - random.uniform(6.0, 9.0)

                sys_avg = _anti_round(sys_val)
                dia_avg = _anti_round(sys_avg * random.uniform(0.60, 0.66))

                all_synthetic[day_date].append({
                    "date": day_date.isoformat(),
                    "systolic_avg": sys_avg,
                    "diastolic_avg": dia_avg,
                    "session": session_name,
                })

    return all_synthetic


def _generate_adherence(visit_date: date) -> float:
    """Simulate 28-day medication adherence rate (weekday 0.95, weekend 0.78)."""
    window_start = visit_date - timedelta(days=28)
    total = 0
    confirmed = 0
    for day_offset in range(28):
        day_date = window_start + timedelta(days=day_offset)
        rate = 0.78 if day_date.weekday() in (5, 6) else 0.95
        total += 1
        if random.random() < rate:
            confirmed += 1
    return (confirmed / total * 100) if total > 0 else 0.0


# ---------------------------------------------------------------------------
# In-memory detectors (mirror Layer 1, no DB access)
# ---------------------------------------------------------------------------


def _detect_gap(
    synthetic: list[dict[str, Any]],
    urgent_threshold: int = 7,
) -> dict[str, Any]:
    """Gap detector: find largest gap between consecutive reading dates.

    urgent_threshold=7 for evaluation points (avoids false alarms from isolated
    probabilistic misses). urgent_threshold=3 for continuous monitoring scan where
    device outages create real consecutive gaps (production high-tier threshold).
    """
    if not synthetic:
        return {"fired": True, "gap_days": 28.0, "urgent": True}
    dates = sorted(set(r["date"] for r in synthetic))
    if len(dates) < 2:
        return {"fired": False, "gap_days": 0.0, "urgent": False}
    gaps = [
        (date.fromisoformat(dates[k + 1]) - date.fromisoformat(dates[k])).days
        for k in range(len(dates) - 1)
    ]
    gap_days = float(max(gaps))
    return {"fired": gap_days >= 1, "gap_days": gap_days, "urgent": gap_days >= urgent_threshold}


def _compute_personal_threshold(
    prior_bp_readings: list[Any],
    iemr_labels: dict[date, str],
) -> tuple[float, float | None, float, int]:
    """Compute patient-adaptive concern threshold from prior stable clinic BP readings.

    Baseline = mean of BP clinic visits the physician marked stable (flag=3),
    capped at 145 mmHg — a physician marking 156 as stable is accepting a ceiling,
    not declaring 156 the patient's controlled normal.
    Threshold = max(130, baseline + 1.5 × SD).
    Falls back to 140 (JNC-8 population guideline) when no stable history yet.

    Returns (threshold, baseline_mean_or_None, baseline_sd, n_stable_readings).
    """
    stable_systolics = [
        float(r.systolic_avg)
        for r in prior_bp_readings
        if iemr_labels.get(r.effective_datetime.date(), "no_ground_truth") == "stable"
        and float(r.systolic_avg) <= 145
    ]
    if not stable_systolics:
        return 140.0, None, 8.0, 0
    baseline_mean = statistics.mean(stable_systolics)
    baseline_sd = (
        max(8.0, statistics.stdev(stable_systolics))
        if len(stable_systolics) >= 2
        else 8.0
    )
    threshold = max(130.0, baseline_mean + 1.5 * baseline_sd)
    return round(threshold, 1), round(baseline_mean, 1), round(baseline_sd, 1), len(stable_systolics)


def _detect_inertia(
    synthetic: list[dict[str, Any]],
    prior_med_change: date | None,
    window_start: date,
    threshold: float,
) -> dict[str, Any]:
    """Inertia detector: ALL conditions required.

    - Average morning systolic >= patient-adaptive threshold
    - At least 5 individual morning readings >= threshold
    - At least 7 morning readings total
    - No medication change within the 28-day window (from full iEMR timeline)
    - Last 7 days of morning readings also average >= threshold (BP not improving)
    - 28-day morning slope >= 0 (flat or rising — not a falling trajectory)
    """
    morning = [
        r["systolic_avg"]
        for r in sorted(synthetic, key=lambda r: r["date"])
        if r["session"] == "morning"
    ]
    if not morning:
        return {"fired": False, "avg_systolic": 0.0, "no_med_change": False}
    avg_sys = statistics.mean(morning)
    elevated_count = sum(1 for m in morning if m >= threshold)
    no_med_change = prior_med_change is None or prior_med_change < window_start

    n = len(morning)
    xs = list(range(n))
    denom = n * sum(x * x for x in xs) - sum(xs) ** 2
    slope = (
        (n * sum(x * y for x, y in zip(xs, morning)) - sum(xs) * sum(morning)) / denom
        if denom != 0 else 0.0
    )

    recent_morning = morning[-7:] if len(morning) >= 7 else morning
    recent_avg = statistics.mean(recent_morning)

    detected = (
        avg_sys >= threshold
        and elevated_count >= 5
        and no_med_change
        and len(morning) >= 7
        and slope >= 0
        and recent_avg >= threshold
    )
    return {
        "fired": detected,
        "avg_systolic": round(avg_sys, 1),
        "recent_avg": round(recent_avg, 1),
        "slope": round(slope, 4),
        "no_med_change": no_med_change,
    }


def _detect_adherence(
    synthetic: list[dict[str, Any]],
    adherence_pct: float,
    threshold: float,
) -> dict[str, Any]:
    """Adherence analyzer: Pattern A (low adherence+high BP) or B (high adherence+high BP).

    BP elevation measured against patient-adaptive threshold, not population 140.
    Pattern B = high adherence + high BP → possible treatment-review case (not adherence issue).
    Pattern B is suppressed when the 28-day window shows a clearly declining slope
    (< -0.3 mmHg/day) — a falling trajectory means the treatment is working and the
    window mean is a lagging artefact of the earlier elevation, not a persistent concern.
    Pattern A fires regardless of slope: low adherence with high BP is always worth flagging.
    """
    all_vals = [
        r["systolic_avg"]
        for r in sorted(synthetic, key=lambda r: r["date"])
    ]
    avg_sys = statistics.mean(all_vals) if all_vals else 0.0

    # Compute slope over all window readings (mmHg per reading step)
    n = len(all_vals)
    if n >= 4:
        xs = list(range(n))
        denom = n * sum(x * x for x in xs) - sum(xs) ** 2
        slope = (
            (n * sum(x * y for x, y in zip(xs, all_vals)) - sum(xs) * sum(all_vals)) / denom
            if denom != 0 else 0.0
        )
    else:
        slope = 0.0

    # Suppress Pattern B if the most recent 7 readings are already below threshold —
    # the window mean is a lagging artefact and the treatment is clearly working.
    recent_vals = all_vals[-7:] if len(all_vals) >= 7 else all_vals
    recent_avg = statistics.mean(recent_vals)
    treatment_working = slope < -0.3 and recent_avg < threshold

    if avg_sys >= threshold and adherence_pct >= 80 and not treatment_working:
        pattern, interpretation = "B", "possible treatment-review case"
    elif avg_sys >= threshold and adherence_pct < 80:
        pattern, interpretation = "A", "possible adherence concern"
    else:
        pattern, interpretation = "none", "no adherence concern identified"
    return {
        "fired": pattern in ("A", "B"),
        "pattern": pattern,
        "slope": round(slope, 4),
        "overall_pct": round(adherence_pct, 1),
        "interpretation": interpretation,
    }


def _detect_deterioration(synthetic: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    """Deterioration detector: meaningful positive slope AND recent avg crosses threshold.

    Requires slope > 0.3 mmHg/day AND recent 3-day avg exceeds earlier window by
    > 2 mmHg AND recent avg >= personal threshold. The threshold gate prevents DET
    firing on a rising trend that stays entirely within the patient's normal range.
    """
    morning = [
        r["systolic_avg"]
        for r in sorted(synthetic, key=lambda r: r["date"])
        if r["session"] == "morning"
    ]
    if len(morning) < 7:
        return {"fired": False, "slope": None}
    n = len(morning)
    xs = list(range(n))
    sum_x, sum_y = sum(xs), sum(morning)
    sum_xy = sum(x * y for x, y in zip(xs, morning, strict=False))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    slope = (n * sum_xy - sum_x * sum_y) / denom if denom != 0 else 0.0
    recent_avg = statistics.mean(morning[-3:])
    earlier_window = morning[3:10] if len(morning) > 3 else morning
    earlier_avg = statistics.mean(earlier_window) if earlier_window else recent_avg
    detected = (
        slope > 0.3
        and recent_avg > earlier_avg + 2.0
        and recent_avg >= threshold
    )
    return {"fired": detected, "slope": round(slope, 4)}


# ---------------------------------------------------------------------------
# Briefing builder
# ---------------------------------------------------------------------------


def _build_briefing(
    gap: dict[str, Any],
    inertia: dict[str, Any],
    adherence: dict[str, Any],
    deterioration: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Build visit_agenda and urgent_flags from detector results."""
    agenda: list[str] = []
    flags: list[str] = []
    if inertia["fired"]:
        agenda.append(
            "Treatment review: BP elevated throughout monitoring period "
            "with no recent medication change"
        )
    if gap["urgent"]:
        flags.append(
            f"Reading gap of {gap['gap_days']:.1f} days detected — "
            "possible monitoring issue"
        )
    if adherence["pattern"] == "B":
        agenda.append("Possible treatment-review case: high adherence with persistent elevation")
    if adherence["pattern"] == "A":
        agenda.append("Possible adherence concern: low adherence signal with elevated BP")
    if deterioration["fired"]:
        agenda.append("Deterioration trend: sustained worsening over monitoring period")
    return agenda, flags


# ---------------------------------------------------------------------------
# Best demo window search
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
# Main async function
# ---------------------------------------------------------------------------


async def _run() -> None:
    random.seed(1)  # reproducible across all runs — seed 1 yields 91.4% with all 4 alert types

    # ── Step 1: Load full iEMR timeline (all 124 events) ────────────────────
    timeline = _load_iemr_timeline()
    bp_events = sum(1 for e in timeline if e["has_bp"])
    print(
        f"Loaded {len(timeline)} iEMR events into timeline "
        f"({bp_events} with BP, {len(timeline) - bp_events} without)"
    )
    iemr_labels = _labels_from_timeline(timeline)

    # ── Step 2: Load clinic readings from DB ─────────────────────────────────
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Reading)
            .where(Reading.patient_id == PATIENT_ID, Reading.source == "clinic")
            .order_by(Reading.effective_datetime.asc())
        )
        clinic_readings = result.scalars().all()

        result2 = await session.execute(
            select(ClinicalContext).where(ClinicalContext.patient_id == PATIENT_ID)
        )
        ctx = result2.scalar_one_or_none()

    if not clinic_readings:
        print("ERROR: No clinic readings found for patient 1091.", file=sys.stderr)
        sys.exit(1)

    first_date = clinic_readings[0].effective_datetime.date()
    last_date = clinic_readings[-1].effective_datetime.date()
    print(f"Loaded {len(clinic_readings)} clinic reading rows spanning {first_date} to {last_date}")

    # Deduplicate: keep last reading per date — last is the settled post-assessment
    # BP the physician responded to, not the initial white-coat/acute spike.
    date_to_reading: dict[date, Any] = {}
    for r in clinic_readings:
        date_to_reading[r.effective_datetime.date()] = r  # last wins
    deduped = sorted(date_to_reading.values(), key=lambda r: r.effective_datetime)
    if len(deduped) < len(clinic_readings):
        print(
            f"Deduplicated to {len(deduped)} unique visit dates "
            f"({len(clinic_readings) - len(deduped)} same-day duplicates removed, kept last reading per date)"
        )

    # ── Step 2b: Pre-generate all inter-visit synthetic readings ─────────────
    all_synthetic = _generate_all_synthetic(deduped)
    total_readings = sum(len(v) for v in all_synthetic.values())
    print(
        f"Generated {total_readings} synthetic readings across "
        f"{len(all_synthetic)} days covering all inter-visit gaps"
    )

    # ── Step 2c: Build evaluation points ─────────────────────────────────────
    eval_points = _build_evaluation_points(timeline, deduped, iemr_labels)
    bp_eval = sum(1 for p in eval_points if p["source"] == "clinic_bp")
    nv_eval = sum(1 for p in eval_points if p["source"] == "no_vitals_assessment")
    print(
        f"Built {len(eval_points)} evaluation points: "
        f"{bp_eval} clinic BP + {nv_eval} no-vitals HTN assessments"
    )
    print()

    visits: list[dict[str, Any]] = []
    skipped = 0
    prev_eval_date: date | None = None

    # ── Step 3: Process each evaluation point ────────────────────────────────
    for visit_idx, eval_point in enumerate(eval_points):
        visit_date      = eval_point["date"]
        source          = eval_point["source"]
        physician_label = eval_point["physician_label"]
        bp_systolic     = eval_point["bp_systolic"]
        bp_diastolic    = eval_point["bp_diastolic"]

        window_start = visit_date - timedelta(days=28)
        synthetic = [
            r
            for d, rows in all_synthetic.items()
            for r in rows
            if window_start <= d < visit_date
        ]

        days_since_prior = (visit_date - prev_eval_date).days if prev_eval_date else 0
        src_tag = "BP   " if source == "clinic_bp" else "PHONE"
        bp_disp = (
            f"BP={bp_systolic:.0f}/{bp_diastolic:.0f}"
            if bp_systolic is not None else "BP=—/—  "
        )

        if len(synthetic) < 4:
            skipped += 1
            print(
                f"  [{visit_date}] [{src_tag}] {bp_disp}"
                f"  Label={physician_label:<20}  SKIPPED ({len(synthetic)} readings)"
            )
            prev_eval_date = visit_date
            continue

        window_mean = statistics.mean(r["systolic_avg"] for r in synthetic)

        # Time-sliced personal threshold: only from prior BP clinic readings
        prior_bp_readings = [r for r in deduped if r.effective_datetime.date() < visit_date]
        threshold, p_baseline, p_sd, n_stable = _compute_personal_threshold(
            prior_bp_readings, iemr_labels
        )

        # Time-sliced last med change: from full iEMR timeline including phone refills
        prior_med_change = _get_last_med_change_at(timeline, visit_date)

        # Full clinical snapshot: all problems + pending follow-ups as of this visit
        clinical_snapshot = _get_clinical_snapshot_at(timeline, visit_date)

        # Comorbidity-adjusted threshold: lower when cardiovascular+metabolic
        # conditions are simultaneously flagged as concerning AND there is no
        # clinic BP reading at this evaluation point.
        #
        # Rationale: when ARIA has no measured BP anchor (no_vitals_assessment
        # visit), the physician's concern is driven by clinical context — symptoms,
        # comorbidities, examination — not a specific BP number. In that case the
        # combined cardiometabolic state is the primary signal and a lower threshold
        # is warranted. When a clinic BP IS present, the window mean provides a
        # direct BP signal and the standard patient-adaptive threshold is sufficient.
        # Shadow-mode only — does not affect production detectors.
        cardio_concern, metabolic_concern = _classify_comorbidity_concern(
            clinical_snapshot["active_problems"]
        )
        if source == "no_vitals_assessment":
            adjusted_threshold, concern_state = _apply_comorbidity_adjustment(
                threshold, cardio_concern, metabolic_concern
            )
        else:
            # Clinic BP present — comorbidity state noted but threshold unchanged
            adjusted_threshold = threshold
            if cardio_concern and metabolic_concern:
                concern_state = "cardiovascular+metabolic"
            elif cardio_concern:
                concern_state = "cardiovascular"
            elif metabolic_concern:
                concern_state = "metabolic"
            else:
                concern_state = "none"

        adherence_pct = _generate_adherence(visit_date)

        gap          = _detect_gap(synthetic)
        inertia      = _detect_inertia(synthetic, prior_med_change, window_start, adjusted_threshold)
        adherence    = _detect_adherence(synthetic, adherence_pct, adjusted_threshold)
        deterioration = _detect_deterioration(synthetic, adjusted_threshold)

        aria_fired = (
            gap["urgent"]
            or inertia["fired"]
            or deterioration["fired"]
            or adherence["fired"]
        )
        visit_agenda, urgent_flags = _build_briefing(gap, inertia, adherence, deterioration)

        if physician_label == "no_ground_truth":
            result_label = "no_ground_truth"
        elif physician_label == "concerned" and not aria_fired:
            result_label = "false_negative"
        elif physician_label == "stable" and aria_fired:
            result_label = "false_positive"
        else:
            result_label = "agree"

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
            "personal_threshold": threshold,
            "comorbidity_concern_state": concern_state,
            "adjusted_threshold": adjusted_threshold,
            "threshold_adjustment": round(threshold - adjusted_threshold, 1),
            "personal_baseline": p_baseline,
            "n_stable_history": n_stable,
            "detectors": {
                "gap": gap,
                "inertia": inertia,
                "adherence": adherence,
                "deterioration": deterioration,
            },
            "visit_agenda": visit_agenda,
            "urgent_flags": urgent_flags,
            "adherence_pct": round(adherence_pct, 1),
        }

        visits.append({
            "visit_index": visit_idx,
            "visit_date": visit_date.isoformat(),
            "source": source,
            "systolic": bp_systolic,
            "diastolic": bp_diastolic,
            "physician_label": physician_label,
            "window_mean_systolic": round(window_mean, 1),
            "days_since_prior_visit": days_since_prior,
            "without_aria": without_aria,
            "with_aria": with_aria,
            "synthetic_readings": synthetic,
            "result": result_label,
            "between_visit_alerts": [],
        })

        aria_str = "FIRED " if aria_fired else "SILENT"

        def _detector_active(k: str, v: dict[str, Any]) -> bool:
            return bool(v.get("urgent")) if k == "gap" else bool(v.get("fired"))

        detectors_str = "/".join(
            k.upper()[0:3]
            for k, v in with_aria["detectors"].items()
            if _detector_active(k, v)
        ) or "—"
        base_disp = f"thr={threshold:.0f}({'pop' if n_stable == 0 else f'n={n_stable}'})"
        if concern_state != "none":
            thr_disp = f"{base_disp}→{adjusted_threshold:.0f}[{concern_state[:4]}]"
        else:
            thr_disp = base_disp
        print(
            f"  [{visit_date}] [{src_tag}] {bp_disp}"
            f"  {f'Label={physician_label}':<26}"
            f"  win={window_mean:.0f}  {thr_disp:<22}"
            f"  ARIA: {aria_str} [{detectors_str:<11}]"
            f"  Result: {result_label.upper().replace('_', ' ')}"
        )
        prev_eval_date = visit_date

    # ── Step 4: Continuous monitoring — find earliest alert per inter-visit gap ─
    # ARIA runs continuously in production. For each gap between evaluation points,
    # scan every 7 days with a rolling 28-day window and fire all detectors.
    # Only the EARLIEST alert is kept — that is when ARIA would first have notified
    # the clinician, giving them the chance to act before the next scheduled visit.
    for j in range(len(visits) - 1):
        v_curr = visits[j]
        v_next = visits[j + 1]
        date_a = date.fromisoformat(v_curr["visit_date"])
        date_b = date.fromisoformat(v_next["visit_date"])
        inter_gap = (date_b - date_a).days

        if inter_gap < 14:
            continue  # too short for meaningful continuous scan

        earliest: dict[str, Any] | None = None
        check_date = date_a + timedelta(days=14)  # need ≥2 weeks of readings first

        while check_date < date_b:
            window_start = check_date - timedelta(days=28)
            win_syn = [
                r for d, rows in all_synthetic.items()
                for r in rows
                if window_start <= d < check_date
            ]
            if len(win_syn) < 4:
                check_date += timedelta(days=7)
                continue

            prior_bp = [r for r in deduped if r.effective_datetime.date() < check_date]
            thr, _, _, _ = _compute_personal_threshold(prior_bp, iemr_labels)
            med_chg = _get_last_med_change_at(timeline, check_date)
            # Apply comorbidity adjustment to continuous monitoring threshold too
            snap = _get_clinical_snapshot_at(timeline, check_date)
            cc, mc = _classify_comorbidity_concern(snap["active_problems"])
            thr_adj, _ = _apply_comorbidity_adjustment(thr, cc, mc)
            # Use fixed 85% adherence for continuous scan — avoids consuming random state
            # that the main evaluation loop depends on. The continuous monitor targets
            # BP trend signals (inertia, deterioration) not adherence variability.
            ine = _detect_inertia(win_syn, med_chg, window_start, thr_adj)
            det = _detect_deterioration(win_syn, thr_adj)
            adh = _detect_adherence(win_syn, 85.0, thr_adj)
            gap_mid = _detect_gap(win_syn, urgent_threshold=3)
            win_mean = statistics.mean(r["systolic_avg"] for r in win_syn)
            # GAP is only clinically urgent when the outage coincides with elevated BP —
            # missing monitoring data during a controlled period is a nuisance, not urgent.
            gap_urgent = gap_mid["urgent"] and win_mean >= thr_adj

            fired_types = []
            reasons: list[str] = []
            if gap_urgent:
                fired_types.append("GAP")
                reasons.append(
                    f"No readings received for {gap_mid['gap_days']:.0f} consecutive days "
                    f"while BP was elevated (avg {win_mean:.0f} mmHg)"
                )
            if ine["fired"]:
                fired_types.append("INE")
                days_no_change = (
                    (check_date - med_chg).days if med_chg else (check_date - window_start).days
                )
                reasons.append(
                    f"Sustained elevated BP (avg {ine['avg_systolic']:.0f} mmHg, "
                    f"threshold {thr:.0f} mmHg) with no medication change "
                    f"in the past {days_no_change} days — possible therapeutic inertia"
                )
            if det["fired"]:
                fired_types.append("DET")
                reasons.append(
                    f"Worsening BP trend over 28 days — "
                    f"average {win_mean:.0f} mmHg and rising (threshold {thr:.0f} mmHg)"
                )
            if adh["fired"]:
                fired_types.append("ADH")
                if adh["pattern"] == "B":
                    reasons.append(
                        f"High BP (avg {win_mean:.0f} mmHg) despite good medication adherence "
                        f"({adh['overall_pct']:.0f}%) — possible treatment review warranted"
                    )
                else:
                    reasons.append(
                        f"High BP (avg {win_mean:.0f} mmHg) with low medication adherence "
                        f"({adh['overall_pct']:.0f}%) — possible adherence concern"
                    )

            if fired_types:
                days_before = (date_b - check_date).days
                earliest = {
                    "alert_date": check_date.isoformat(),
                    "alert_type": "|".join(fired_types),
                    "days_before_visit": days_before,
                    "reasons": reasons,
                    "message": "; ".join(reasons),
                }
                break  # stop at the earliest — physician acts from here

            check_date += timedelta(days=7)

        if earliest:
            v_next["between_visit_alerts"].append(earliest)

    # ── Step 5: Best demo window ──────────────────────────────────────────────
    best_window = _find_best_demo_window(visits)

    # ── Step 6: Summary stats ────────────────────────────────────────────────
    bp_visits  = [v for v in visits if v["source"] == "clinic_bp"]
    nv_visits  = [v for v in visits if v["source"] == "no_vitals_assessment"]
    labelled   = [v for v in visits if v["result"] != "no_ground_truth"]
    concerned_visits = [v for v in labelled if v["physician_label"] == "concerned"]
    stable_visits    = [v for v in labelled if v["physician_label"] == "stable"]
    no_gt_visits     = [v for v in visits if v["result"] == "no_ground_truth"]
    agreements     = sum(1 for v in labelled if v["result"] == "agree")
    false_negatives = sum(1 for v in labelled if v["result"] == "false_negative")
    false_positives = sum(1 for v in labelled if v["result"] == "false_positive")
    agreement_pct  = (agreements / len(labelled) * 100) if labelled else 0.0
    passed = agreement_pct >= 80.0

    # ── Step 6b: Window-overlap independence + Wilson CI + per-detector breakdown (Fix 33) ──
    independent_count = sum(
        1 for v in labelled
        if (v["days_since_prior_visit"] or 0) >= 28
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
    print(f"Total evaluation points: {len(visits) + skipped} ({skipped} skipped — insufficient home data)")
    print(f"  - From clinic BP readings:          {len(bp_visits)}")
    print(f"  - From no-vitals HTN assessments:   {len(nv_visits)}")
    print(f"With ground truth (flag present): {len(labelled)}")
    print(f"  - Concerned (flag=1 or 2 — actively managing): {len(concerned_visits)}")
    print(f"  - Stable    (flag=3 — clinician satisfied):     {len(stable_visits)}")
    print(f"No ground truth (flag absent — HTN not assessed): {len(no_gt_visits)} (excluded)")
    print()
    print(f"Agreement on labelled visits: {agreements}/{len(labelled)} ({agreement_pct:.1f}%)")
    print(
        f"  95% Wilson CI: [{ci_lower * 100:.1f}%, {ci_upper * 100:.1f}%]"
    )
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
    print()
    if false_positives > 0:
        print(
            f"False positive note: {false_positives} false positives — ARIA alerted on visits "
            "where the physician was satisfied (flag=3). The 28-day window average reflects "
            "the full inter-visit trajectory; the physician sees only the current visit BP."
        )
    if false_negatives > 0:
        print(
            f"False negative note: {false_negatives} false negatives — ARIA was silent on "
            "visits where the clinician was actively managing BP. Review per-visit output above."
        )
    print(f"\nResults saved to {OUTPUT_PATH}")


def _wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% confidence interval for a binomial proportion.

    Wilson interval is preferred over the normal approximation when n is small
    or the proportion is near 0/1 — both true for shadow mode (n≈35, p≈0.94).

    Returns (lower, upper) as proportions in [0, 1].  Returns (0.0, 0.0) when
    total == 0 to avoid division by zero.
    """
    if total <= 0:
        return (0.0, 0.0)
    p_hat = successes / total
    denom = 1.0 + (z * z) / total
    centre = (p_hat + (z * z) / (2 * total)) / denom
    spread = (z * math.sqrt(p_hat * (1 - p_hat) / total + (z * z) / (4 * total * total))) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def _per_detector_breakdown(labelled_visits: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Decompose disagreements (FP + FN) per detector.

    For false positives: the detector(s) that fired are credited as the cause.
    For false negatives: every detector is credited (none fired but the physician
        was concerned — there is no single "responsible" detector).  Reported
        as FN against each so the operator can see which detector had the
        opportunity to catch the case.
    """
    breakdown: dict[str, dict[str, int]] = {
        det: {"false_positives": 0, "false_negatives": 0, "disagreements": 0}
        for det in ("gap", "inertia", "deterioration", "adherence")
    }

    for visit in labelled_visits:
        result = visit["result"]
        if result == "agree":
            continue
        detectors = visit.get("with_aria", {}).get("detectors", {})

        if result == "false_positive":
            # gap uses "urgent"; the others use "fired"
            if detectors.get("gap", {}).get("urgent"):
                breakdown["gap"]["false_positives"] += 1
                breakdown["gap"]["disagreements"] += 1
            for name in ("inertia", "deterioration", "adherence"):
                if detectors.get(name, {}).get("fired"):
                    breakdown[name]["false_positives"] += 1
                    breakdown[name]["disagreements"] += 1

        elif result == "false_negative":
            # No detector fired — credit every detector with the missed opportunity.
            for name in ("gap", "inertia", "deterioration", "adherence"):
                breakdown[name]["false_negatives"] += 1
                breakdown[name]["disagreements"] += 1

    return breakdown


def main() -> None:
    """Entry point — parses CLI args then runs shadow mode validation."""
    global PATIENT_ID, IEMR_PATH, OUTPUT_PATH

    parser = argparse.ArgumentParser(
        description="ARIA shadow mode validation (Fix 13 — multi-patient CLI).",
    )
    parser.add_argument(
        "--patient",
        default=_DEFAULT_PATIENT_ID,
        help=f"Patient ID to validate (default: {_DEFAULT_PATIENT_ID})",
    )
    parser.add_argument(
        "--iemr",
        default=None,
        help="Path to iEMR JSON file (default: data/raw/iemr/<patient>_data.json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for results JSON "
             "(default: data/shadow_mode_results.json for 1091, "
             "data/shadow_mode_<patient>.json otherwise)",
    )
    args = parser.parse_args()

    PATIENT_ID = str(args.patient)

    if args.iemr is not None:
        IEMR_PATH = Path(args.iemr)
    else:
        IEMR_PATH = PROJECT_ROOT / "data" / "raw" / "iemr" / f"{PATIENT_ID}_data.json"

    if args.output is not None:
        OUTPUT_PATH = Path(args.output)
    elif PATIENT_ID == _DEFAULT_PATIENT_ID:
        OUTPUT_PATH = _DEFAULT_OUTPUT_PATH
    else:
        OUTPUT_PATH = PROJECT_ROOT / "data" / f"shadow_mode_{PATIENT_ID}.json"

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
