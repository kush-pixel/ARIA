"""FHIR R4 Bundle ingestion — populates ARIA PostgreSQL tables from a FHIR Bundle.

Processing order respects foreign-key dependencies:
1. Patient resource  → patients table
2. All resources     → clinical_context table (pre-computed join)
3. Observation       → readings table  (clinic BP history)
4. Audit event       → audit_events table (always written, even on failure)

Idempotency strategy
--------------------
- patients:          INSERT … ON CONFLICT DO NOTHING on patient_id PK.
- clinical_context:  INSERT … ON CONFLICT DO UPDATE — refreshes on re-run.
- readings:          Per-observation ON CONFLICT DO NOTHING on
                     (patient_id, effective_datetime, source).  New readings
                     are added on re-run without skipping the entire batch.
- audit_events:      Always appended (one row per ingestion attempt).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.clinical_context import ClinicalContext
from app.models.patient import Patient
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# LOINC codes for BP panel components
_LOINC_SYSTOLIC = "8480-6"
_LOINC_DIASTOLIC = "8462-4"

# Top-level LOINC codes used to identify Observation type in the bundle
_LOINC_BP_PANEL = "55284-4"   # Blood pressure panel
_LOINC_PULSE    = "8867-4"    # Heart rate (Fix 6)
_LOINC_WEIGHT   = "29463-7"   # Body weight in kg (Fix 6)
_LOINC_SPO2     = "59408-5"   # Oxygen saturation (Fix 6)
_LOINC_TEMP     = "8310-5"    # Body temperature (Fix 6)

# Non-standard extension key for patient age (set by adapter.py, consumed here).
# Both files must reference this same key string.
_PATIENT_AGE_EXT = "_age"

# FHIR gender string → ARIA single-character gender code
_GENDER_MAP: dict[str, str] = {"male": "M", "female": "F", "unknown": "U"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _group_entries(bundle: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group FHIR Bundle entry resources by resourceType.

    Args:
        bundle: Parsed FHIR Bundle dict.

    Returns:
        Dict mapping resourceType string → list of resource dicts.
        Resources with a missing or non-string resourceType are dropped.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in bundle.get("entry", []):
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource", {})
        if not isinstance(resource, dict):
            continue
        rt = resource.get("resourceType")
        if not rt:
            continue
        groups.setdefault(rt, []).append(resource)
    return groups


def _determine_risk_tier(problem_codes: list[str]) -> tuple[str, str | None, str | None]:
    """Apply clinical auto-override rules to determine the initial risk tier.

    Checks problem codes in the order they appear; first match wins.
    The default when no override applies is ``"medium"``.

    Override rules:
    - Any code starting with ``I50`` (CHF)                  → high / "CHF in problem list"
    - Any code starting with ``I61`` (haemorrhagic stroke)   → high / "Haemorrhagic stroke history"
    - Any code starting with ``I63`` or ``I64`` (ischaemic stroke) → high / "Stroke history"
    - Any code starting with ``G45`` (TIA)                   → high / "TIA history"

    Args:
        problem_codes: ICD-10 / SNOMED codes from Condition resources.

    Returns:
        Tuple ``(risk_tier, tier_override, tier_override_source)``.
        ``tier_override`` and ``tier_override_source`` are ``None`` when the
        default "medium" tier applies.  ``tier_override_source`` is ``"system"``
        for all auto-override cases — these are immovable safety floors.
    """
    for code in problem_codes:
        if code.startswith("I50"):
            return "high", "CHF in problem list", "system"
        if code.startswith("I61"):
            return "high", "Haemorrhagic stroke history", "system"
        if code.startswith("I63") or code.startswith("I64"):
            return "high", "Stroke history", "system"
        if code.startswith("G45"):
            return "high", "TIA history", "system"
    return "medium", None, None


def _extract_obs_components(obs: dict[str, Any]) -> tuple[int | None, int | None]:
    """Extract systolic and diastolic values from a FHIR BP Observation.

    Looks for LOINC 8480-6 (systolic) and 8462-4 (diastolic) in the
    ``component`` array of the Observation resource.

    Args:
        obs: FHIR Observation resource dict.

    Returns:
        Tuple ``(systolic_mmhg, diastolic_mmhg)``.  Either value may be
        ``None`` if the corresponding component is absent or malformed.
    """
    systolic: int | None = None
    diastolic: int | None = None
    for component in obs.get("component", []):
        try:
            coding = component.get("code", {}).get("coding", [{}])
            code = coding[0].get("code", "") if coding else ""
            value = component.get("valueQuantity", {}).get("value")
            if code == _LOINC_SYSTOLIC and value is not None:
                systolic = int(value)
            elif code == _LOINC_DIASTOLIC and value is not None:
                diastolic = int(value)
        except (IndexError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed Observation component: %s", exc)
    return systolic, diastolic


def _parse_authored_on(date_str: str | None) -> date | None:
    """Parse a FHIR ``authoredOn`` string to a Python ``date``.

    Handles ISO date (``"2008-01-14"``) and ISO datetime
    (``"2008-01-14T14:39:00"``) formats.

    Args:
        date_str: ``authoredOn`` value from a MedicationRequest resource.

    Returns:
        Python ``date``, or ``None`` if the input is absent or unparseable.
    """
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).date()
    except ValueError:
        logger.warning("Cannot parse authoredOn date %r", date_str)
        return None


def _get_obs_loinc(obs: dict[str, Any]) -> str:
    """Return the top-level LOINC code of a FHIR Observation resource.

    Args:
        obs: FHIR Observation resource dict.

    Returns:
        LOINC code string, or ``""`` if absent.
    """
    coding = obs.get("code", {}).get("coding", [])
    return coding[0].get("code", "") if coding else ""


def _get_obs_scalar_value(obs: dict[str, Any]) -> float | None:
    """Extract the numeric value from a scalar FHIR Observation (``valueQuantity``).

    Args:
        obs: FHIR Observation resource dict with a ``valueQuantity`` element.

    Returns:
        Float value, or ``None`` if absent or non-numeric.
    """
    vq = obs.get("valueQuantity", {})
    v = vq.get("value")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ingest_fhir_bundle(
    bundle: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    """Ingest a FHIR R4 Bundle into the ARIA PostgreSQL database.

    Populates ``patients``, ``clinical_context``, ``readings``, and
    ``audit_events`` tables.  The operation is idempotent: re-running on
    the same bundle leaves the database unchanged (``patients_inserted=0``,
    ``readings_inserted=0`` on re-run).

    Idempotency for readings uses per-observation ON CONFLICT DO NOTHING on
    the unique index ``(patient_id, effective_datetime, source)``.  Each
    Observation is inserted independently — new readings are added without
    skipping the entire batch when any prior clinic reading already exists.

    Args:
        bundle: Parsed FHIR R4 Bundle dict, as produced by the iEMR adapter.
        session: SQLAlchemy async session.  The caller is responsible for
            session lifecycle; this function commits internally.

    Returns:
        Summary dict::

            {
                "patient_id":              str | None,
                "patients_inserted":       int,   # 0 or 1
                "clinical_context_upserted": int, # always 1 on success
                "readings_inserted":       int,   # 0 on re-run
                "audit_events_inserted":   int,   # always 1
            }

    Raises:
        ValueError: Bundle contains no Patient resource.
        Exception:  Any database error is re-raised after rollback and after
            the failure audit event is committed.
    """
    outcome = "failure"
    patient_id: str | None = None
    summary: dict[str, Any] = {
        "patient_id": None,
        "patients_inserted": 0,
        "clinical_context_upserted": 0,
        "readings_inserted": 0,
        "audit_events_inserted": 0,
    }

    try:
        groups = _group_entries(bundle)

        # ------------------------------------------------------------------ #
        # Step 1 — Patient                                                     #
        # ------------------------------------------------------------------ #
        patient_resources = groups.get("Patient", [])
        if not patient_resources:
            raise ValueError("FHIR Bundle contains no Patient resource")

        pat_resource = patient_resources[0]
        patient_id = pat_resource["id"]
        summary["patient_id"] = patient_id

        fhir_gender = pat_resource.get("gender", "unknown")
        gender = _GENDER_MAP.get(fhir_gender, "U")
        age: int | None = pat_resource.get(_PATIENT_AGE_EXT)

        conditions = groups.get("Condition", [])
        problem_codes: list[str] = []
        for cond in conditions:
            coding = cond.get("code", {}).get("coding", [])
            if coding:
                code = coding[0].get("code", "")
                if code:
                    problem_codes.append(code)

        risk_tier, tier_override, tier_override_source = _determine_risk_tier(problem_codes)

        # Check existence before the INSERT so we can track the inserted count
        # accurately (ON CONFLICT DO UPDATE rowcount is unreliable in asyncpg).
        existing_result = await session.execute(
            select(Patient).where(Patient.patient_id == patient_id)
        )
        patient_existed = existing_result.scalar_one_or_none() is not None

        # Two-step upsert:
        #
        # Step A — INSERT with ON CONFLICT DO UPDATE for demographics only.
        #   Tier columns are intentionally NOT included here — they are handled
        #   in Step B with explicit safety conditions.
        #
        # Step B — Conditional UPDATE of tier columns.
        #   When new source is "system" (auto-override): always overwrite.
        #   When current source is "system": overwrite to reflect the removal
        #     of a condition (e.g. CHF resolved in EHR).
        #   When current source is NULL: no prior override, safe to update.
        #   When current source is "clinician" or "system_score": DO NOT touch —
        #     clinician judgement and score-promoted tiers survive re-ingestion.
        #
        # Operational columns (monitoring_active, enrolled_at, enrolled_by,
        # next_appointment, risk_score, risk_score_computed_at,
        # tier_override_suppressed_until) are never touched by ingestion.
        await session.execute(
            pg_insert(Patient)
            .values(
                patient_id=patient_id,
                gender=gender,
                age=age,
                risk_tier=risk_tier,
                tier_override=tier_override,
                tier_override_source=tier_override_source,
                monitoring_active=True,
            )
            .on_conflict_do_update(
                index_elements=["patient_id"],
                set_={"gender": gender, "age": age},
            )
        )

        # Step B: update tier columns conditionally
        if tier_override_source == "system":
            # Auto-override always wins — no condition on current source
            tier_where = Patient.patient_id == patient_id
        else:
            # Preserve clinician and score-promoted tiers through re-ingestion
            tier_where = and_(
                Patient.patient_id == patient_id,
                or_(
                    Patient.tier_override_source == "system",  # was auto, now removed
                    Patient.tier_override_source.is_(None),    # no active override
                ),
            )
        await session.execute(
            update(Patient)
            .where(tier_where)
            .values(
                risk_tier=risk_tier,
                tier_override=tier_override,
                tier_override_source=tier_override_source,
            )
        )
        summary["patients_inserted"] = 0 if patient_existed else 1
        logger.info(
            "Patient %s: %s (tier=%s source=%s)",
            patient_id,
            "updated" if patient_existed else "inserted",
            risk_tier,
            tier_override_source or "none",
        )

        # ------------------------------------------------------------------ #
        # Step 2 — ClinicalContext                                             #
        # ------------------------------------------------------------------ #

        # Build parallel problem arrays; keep only entries that have both a
        # text name and an ICD-10/SNOMED code so alignment is guaranteed.
        problems_with_codes: list[tuple[str, str]] = []
        for cond in conditions:
            text_val = cond.get("code", {}).get("text", "")
            coding = cond.get("code", {}).get("coding", [])
            code_val = coding[0].get("code", "") if coding else ""
            if text_val and code_val:
                problems_with_codes.append((text_val, code_val))

        active_problems = [p for p, _ in problems_with_codes]
        problem_codes_list = [c for _, c in problems_with_codes]

        # Build parallel medication arrays.
        med_resources = groups.get("MedicationRequest", [])
        meds_with_codes: list[tuple[str, str]] = []
        for med in med_resources:
            mcc = med.get("medicationCodeableConcept", {})
            med_text = mcc.get("text", "")
            coding = mcc.get("coding") or []
            rxnorm = coding[0].get("code", "") if coding else ""
            if med_text:
                meds_with_codes.append((med_text, rxnorm))

        current_medications = [m for m, _ in meds_with_codes]
        med_rxnorm_codes = [c for _, c in meds_with_codes]

        authored_dates: list[date] = []
        for med in med_resources:
            d = _parse_authored_on(med.get("authoredOn"))
            if d:
                authored_dates.append(d)
        last_med_change: date | None = max(authored_dates) if authored_dates else None

        allergy_resources = groups.get("AllergyIntolerance", [])
        allergies = [
            a.get("code", {}).get("text", "")
            for a in allergy_resources
            if a.get("code", {}).get("text")
        ]

        sr_resources = groups.get("ServiceRequest", [])
        overdue_labs = [
            s.get("code", {}).get("text", "")
            for s in sr_resources
            if s.get("code", {}).get("text")
        ]

        # Non-FHIR metadata injected by adapter.py — full medication timeline.
        med_history: list[dict] = bundle.get("_aria_med_history") or []

        # Physician assessment texts per visit per problem (Fix 7).
        problem_assessments: list[dict] = bundle.get("_aria_problem_assessments") or []

        # All iEMR ADMIT_DATE values for last_visit_date (Fix 12).
        visit_dates_raw: list[str] = bundle.get("_aria_visit_dates") or []
        if visit_dates_raw:
            last_visit_date: date | None = max(
                date.fromisoformat(d) for d in visit_dates_raw
            )
        else:
            last_visit_date = None  # populated from obs loop fallback below

        # Social history free text (Fix 8).
        social_context: str | None = bundle.get("_aria_social_context")

        # Sort Observations by effectiveDateTime (ASC) for historical arrays.
        observations = groups.get("Observation", [])
        obs_with_dt: list[tuple[datetime, dict[str, Any]]] = []
        for obs in observations:
            eff_dt_str = obs.get("effectiveDateTime")
            if not eff_dt_str:
                continue
            try:
                eff_dt = datetime.fromisoformat(eff_dt_str)
                obs_with_dt.append((eff_dt, obs))
            except ValueError:
                logger.warning(
                    "Skipping Observation — unparseable effectiveDateTime: %r",
                    eff_dt_str,
                )

        obs_with_dt.sort(key=lambda x: x[0])

        historic_bp_systolic: list[int] = []
        historic_bp_dates: list[str] = []
        last_clinic_systolic: int | None = None
        last_clinic_diastolic: int | None = None
        # Scalar vital accumulators (Fix 6) — ASC sort means last value wins
        last_clinic_pulse: int | None = None
        last_clinic_weight_kg: float | None = None
        last_clinic_spo2: float | None = None
        historic_spo2: list[float] = []

        for eff_dt, obs in obs_with_dt:
            loinc = _get_obs_loinc(obs)

            if loinc == _LOINC_BP_PANEL:
                sys_val, dia_val = _extract_obs_components(obs)
                if sys_val is not None:
                    historic_bp_systolic.append(sys_val)
                    historic_bp_dates.append(eff_dt.date().isoformat())
                    last_clinic_systolic = sys_val
                if dia_val is not None:
                    last_clinic_diastolic = dia_val
                # Fallback: set last_visit_date from BP obs when _aria_visit_dates absent
                if not visit_dates_raw:
                    last_visit_date = eff_dt.date()

            elif loinc == _LOINC_PULSE:
                v = _get_obs_scalar_value(obs)
                if v is not None:
                    last_clinic_pulse = int(v)

            elif loinc == _LOINC_WEIGHT:
                v = _get_obs_scalar_value(obs)
                if v is not None:
                    last_clinic_weight_kg = round(v, 1)

            elif loinc == _LOINC_SPO2:
                v = _get_obs_scalar_value(obs)
                if v is not None:
                    last_clinic_spo2 = round(v, 1)
                    historic_spo2.append(round(v, 1))

        # Allergy reactions parallel array (Fix 9)
        allergy_reactions: list[str] = []
        for a in allergy_resources:
            reactions = a.get("reaction", [])
            if reactions:
                manifests = reactions[0].get("manifestation", [])
                reaction_text = manifests[0].get("text", "") if manifests else ""
            else:
                reaction_text = ""
            allergy_reactions.append(reaction_text)

        # Lab values skeleton (Fix 16) — always None for patient 1091 since iEMR
        # data does not contain structured LOINC lab observations.  Column is
        # ready for when structured lab data becomes available via the bundle.
        recent_labs_raw: list[dict] = bundle.get("_aria_recent_labs") or []
        recent_labs: dict | None = None
        if recent_labs_raw:
            recent_labs = {
                entry["loinc_code"]: {
                    "value": entry.get("value"),
                    "unit": entry.get("unit"),
                    "date": entry.get("date"),
                }
                for entry in recent_labs_raw
                if entry.get("loinc_code")
            }

        cc_values: dict[str, Any] = {
            "patient_id": patient_id,
            "active_problems": active_problems or None,
            "problem_codes": problem_codes_list or None,
            "current_medications": current_medications or None,
            "med_rxnorm_codes": med_rxnorm_codes or None,
            "med_history": med_history or None,
            "problem_assessments": problem_assessments or None,
            "last_med_change": last_med_change,
            "allergies": allergies or None,
            "allergy_reactions": allergy_reactions or None,
            "social_context": social_context,
            "overdue_labs": overdue_labs or None,
            "last_visit_date": last_visit_date,
            "last_clinic_systolic": last_clinic_systolic,
            "last_clinic_diastolic": last_clinic_diastolic,
            "historic_bp_systolic": historic_bp_systolic or None,
            "historic_bp_dates": historic_bp_dates or None,
            "last_clinic_pulse": last_clinic_pulse,
            "last_clinic_weight_kg": last_clinic_weight_kg,
            "last_clinic_spo2": last_clinic_spo2,
            "historic_spo2": historic_spo2 or None,
            "recent_labs": recent_labs,
        }

        set_on_conflict = {k: v for k, v in cc_values.items() if k != "patient_id"}
        set_on_conflict["last_updated"] = sa_func.now()

        await session.execute(
            pg_insert(ClinicalContext)
            .values(cc_values)
            .on_conflict_do_update(
                index_elements=["patient_id"],
                set_=set_on_conflict,
            )
        )
        summary["clinical_context_upserted"] = 1
        logger.info("ClinicalContext upserted for patient %s", patient_id)

        # ------------------------------------------------------------------ #
        # Step 3 — Readings (BP clinic Observations only)                      #
        # ------------------------------------------------------------------ #
        # Only BP panel Observations (LOINC 55284-4) become readings rows.
        # Scalar vital Observations (pulse, weight, SpO2, temp) are stored in
        # clinical_context columns and must not be inserted here.
        # Per-observation idempotency: ON CONFLICT DO NOTHING on
        # (patient_id, effective_datetime, source).
        readings_inserted = 0
        bp_obs_with_dt = [
            (dt, o) for dt, o in obs_with_dt if _get_obs_loinc(o) == _LOINC_BP_PANEL
        ]
        for eff_dt, obs in bp_obs_with_dt:
            sys_val, dia_val = _extract_obs_components(obs)
            if sys_val is None or dia_val is None:
                logger.warning(
                    "Skipping Observation at %s — missing systolic or diastolic component",
                    eff_dt.isoformat(),
                )
                continue
            stmt = (
                pg_insert(Reading)
                .values(
                    patient_id=patient_id,
                    systolic_1=sys_val,
                    diastolic_1=dia_val,
                    systolic_avg=float(sys_val),
                    diastolic_avg=float(dia_val),
                    effective_datetime=eff_dt,
                    session="ad_hoc",
                    source="clinic",
                    submitted_by="clinic",
                    consent_version="1.0",
                )
                .on_conflict_do_nothing(
                    index_elements=["patient_id", "effective_datetime", "source"]
                )
            )
            result = await session.execute(stmt)
            readings_inserted += result.rowcount

        summary["readings_inserted"] = readings_inserted

        await session.commit()
        outcome = "success"
        logger.info(
            "Ingestion complete for patient %s: patients=%d cc=1 readings=%d",
            patient_id,
            summary["patients_inserted"],
            readings_inserted,
        )

    except Exception as exc:
        await session.rollback()
        logger.error("Bundle ingestion failed for patient %s: %s", patient_id, exc)
        raise

    finally:
        # Always write an audit event — outcome reflects success or failure.
        try:
            details = (
                f"Ingested {summary['readings_inserted']} readings for patient {patient_id}"
                if patient_id
                else "Bundle import failed before patient_id could be extracted"
            )
            audit = AuditEvent(
                actor_type="system",
                action="bundle_import",
                resource_type="Bundle",
                patient_id=patient_id,
                outcome=outcome,
                details=details,
            )
            session.add(audit)
            await session.commit()
            summary["audit_events_inserted"] = 1
        except Exception as audit_exc:
            logger.error("Failed to write audit event for patient %s: %s", patient_id, audit_exc)

    return summary
