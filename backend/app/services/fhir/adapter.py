"""iEMR JSON to FHIR R4 Bundle adapter.

Converts the proprietary iEMR patient record format into a FHIR R4 Bundle
containing Patient, Condition, MedicationRequest, Observation,
AllergyIntolerance, and ServiceRequest resources.

Multi-visit deduplication strategy
-----------------------------------
iEMR visits are ordered chronologically oldest-first.  All resource types
except Observation are keyed by their internal iEMR code; later visits
overwrite earlier ones so the final set reflects the *most recent* state of
each problem, medication, allergy, and follow-up plan.  Observations
(clinic BP readings) are never deduplicated — every vitals entry becomes
its own resource to preserve the full BP history.

Non-FHIR metadata injected into the bundle dict
-------------------------------------------------
``_aria_med_history``         — full medication timeline (list of dicts)
``_aria_problem_assessments`` — per-visit physician assessment texts (list of dicts)
``_aria_visit_dates``         — all ADMIT_DATE values as ISO date strings (list)
``_aria_social_context``      — joined SOCIAL_HX text from most recent visit (str|None)
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import re
from datetime import datetime
from typing import Any

from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# FHIR system URIs (HL7/FHIR standards — do not change without
# a FHIR version migration)
# ---------------------------------------------------------------------------
_LOINC_SYSTEM = "http://loinc.org"
_ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10"
_CONDITION_CLINICAL_SYSTEM = "http://terminology.hl7.org/CodeSystem/condition-clinical"
_ALLERGY_CLINICAL_SYSTEM = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
_RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"
_UCUM_SYSTEM = "http://unitsofmeasure.org"

# LOINC codes for blood pressure observations
# 55284-4 = Blood pressure panel (systolic + diastolic together)
# 8480-6  = Systolic blood pressure component
# 8462-4  = Diastolic blood pressure component
_LOINC_BP_PANEL = "55284-4"
_LOINC_SYSTOLIC = "8480-6"
_LOINC_DIASTOLIC = "8462-4"

# UCUM unit code for millimetres of mercury
# mm[Hg] is correct UCUM syntax — the brackets are intentional
_UCUM_MMHG = "mm[Hg]"

# Additional vital-sign LOINC codes (Fix 6)
_LOINC_PULSE  = "8867-4"   # Heart rate / pulse
_LOINC_WEIGHT = "29463-7"  # Body weight
_LOINC_SPO2   = "59408-5"  # Oxygen saturation by pulse oximetry
_LOINC_TEMP   = "8310-5"   # Body temperature

# Pounds → kilograms conversion factor
_LB_TO_KG = 0.453592

# Non-standard extension key used to pass age from adapter to
# ingestion layer. Both files must use this same constant.
_PATIENT_AGE_EXT = "_age"

# Substrings (upper-cased) that identify iEMR MEDICATIONS entries that are
# supplies, diagnostic tests, or device prescriptions — not pharmaceuticals.
# Matched against the full upper-cased medication name string.
_NON_DRUG_MARKERS: tuple[str, ...] = (
    "RX FOR ",       # device/therapy scripts ("Rx for Compression Stockings")
    "SYRINGE",       # injection supply containers
    "SHARPS",        # sharps disposal bins
    " CONTAINER",    # generic supply containers (space-prefixed to avoid "Retainer")
    "PEN NEEDLE",    # insulin pen needle packs
    " TEST",         # diagnostic tests ("HEARING TEST") — space-prefixed to avoid "Attest"
)

# Exact upper-cased names that are diagnostic tests not identifiable by substring.
_NON_DRUG_EXACT: frozenset[str] = frozenset({"VNG"})

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_GENDER_MAP: dict[str, str] = {
    "m": "male",
    "1": "male",
    "f": "female",
    "2": "female",
    "u": "unknown",
}

_IEMR_DATE_FMT = "%m/%d/%Y %H:%M"
_IEMR_DATE_FMT_SHORT = "%m/%d/%Y"

# MED_ADJUD_TEXT stop/restart patterns — present when a medication was
# temporarily stopped (e.g. during illness) and later resumed.
# Example: "SULAR 10 MG TAB ER PO: Restart; stopped on 11/15/2008; restarted on 02/26/2009."
_ADJUD_RESTART_RE = re.compile(r"\bRestart\b")
_ADJUD_STOPPED_RE = re.compile(
    r"\bstopped\s+on\s+(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2})?)",
    re.IGNORECASE,
)
_ADJUD_RESTARTED_RE = re.compile(
    r"\brestarted\s+on\s+(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2})?)",
    re.IGNORECASE,
)


def _parse_iemr_datetime(date_str: str | None) -> str | None:
    """Convert an iEMR date string to ISO 8601 format.

    Args:
        date_str: Date in ``MM/DD/YYYY HH:MM`` format, or ``None``.

    Returns:
        ISO 8601 string ``YYYY-MM-DDTHH:MM:00``, or ``None`` on failure.
    """
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in (_IEMR_DATE_FMT, _IEMR_DATE_FMT_SHORT):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:00")
        except ValueError:
            continue
    logger.warning("Cannot parse iEMR datetime %r", date_str)
    return None


def _map_gender(gender_val: str | None) -> str:
    """Map an iEMR gender value to a FHIR gender string.

    Args:
        gender_val: Raw iEMR gender (``"M"``, ``"F"``, ``"U"``,
            ``"1"``, ``"2"``, or ``None``).

    Returns:
        One of ``"male"``, ``"female"``, or ``"unknown"``.
    """
    if gender_val is None:
        return "unknown"
    return _GENDER_MAP.get(gender_val.strip().lower(), "unknown")


def _extract_icd10(
    code_mappings: dict[str, Any] | None,
    fallback: str | None,
) -> str | None:
    """Extract an ICD-10 code from an iEMR code_mappings dict.

    Searches the ``code_mappings`` list inside the dict for an entry whose
    ``code_type`` contains ``"ICD10"`` or ``"ICD-10"``.  Falls back to
    *fallback* if no ICD-10 entry is found.

    Args:
        code_mappings: The ``code_mappings`` object on an iEMR item.
        fallback: Value returned when no ICD-10 code is found.

    Returns:
        ICD-10 code string, or *fallback*.
    """
    if not code_mappings:
        return fallback
    for entry in code_mappings.get("code_mappings", []):
        code_type = str(entry.get("code_type", ""))
        if "ICD10" in code_type or "ICD-10" in code_type:
            code = entry.get("code")
            if code:
                return str(code)
    return fallback


def _extract_rxnorm(code_mappings: dict[str, Any] | None) -> str | None:
    """Extract the first RxNorm code from an iEMR code_mappings dict.

    Args:
        code_mappings: The ``code_mappings`` object on an iEMR item.

    Returns:
        RxNorm code string, or ``None``.
    """
    if not code_mappings:
        return None
    for entry in code_mappings.get("code_mappings", []):
        if str(entry.get("code_type", "")).upper() == "RXNORM":
            code = entry.get("code")
            if code:
                return str(code)
    return None


def _pseudonymize_patient_id(med_rec_no: str, secret_key: str) -> str:
    """Return a 16-char HMAC-SHA256 hex prefix for the given MED_REC_NO.

    Deterministic and non-reversible without the key.
    Only called when ``PATIENT_PSEUDONYM_KEY`` is set in config.

    Args:
        med_rec_no: The raw iEMR medical record number (e.g. ``"1091"``).
        secret_key: The HMAC secret from ``PATIENT_PSEUDONYM_KEY`` in config.

    Returns:
        16-character lowercase hex string derived from HMAC-SHA256.
    """
    return _hmac.new(
        secret_key.encode(),
        med_rec_no.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Resource builders
# ---------------------------------------------------------------------------


def _build_patient(patient_id: str, visits: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a FHIR Patient resource from iEMR visits.

    Args:
        patient_id: The iEMR ``MED_REC_NO`` value.
        visits: All VISIT dicts for this patient.

    Returns:
        FHIR Patient resource dict.
    """
    gender_val: str | None = None
    age_val: int | None = None

    for visit in visits:
        if gender_val is None and visit.get("GENDER"):
            gender_val = visit["GENDER"]
        if age_val is None and visit.get("AGE") is not None:
            try:
                age_val = int(visit["AGE"])
            except (TypeError, ValueError):
                pass
        if gender_val is not None and age_val is not None:
            break

    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient_id,
        "gender": _map_gender(gender_val),
    }
    if age_val is not None:
        resource[_PATIENT_AGE_EXT] = age_val  # non-standard extension — used by ingestion layer
    return resource


def _build_conditions(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deduplicated FHIR Condition resources from iEMR PROBLEM arrays.

    Filters:
    - ``PROBLEM_ACTIVITY == "Active"``
    - ``PROBLEM_CLASSIFICATION != "PMH"``
    - ``PROBLEM_END_DATE`` absent, ``None``, or empty string
    - ICD-10 code not in Z00.x range (encounter-type administrative codes)

    Later visits overwrite earlier ones (most-recent wins).

    Args:
        visits: All VISIT dicts for this patient.

    Returns:
        List of FHIR Condition resource dicts.
    """
    seen: dict[str, dict[str, Any]] = {}

    for visit in visits:
        for problem in visit.get("PROBLEM", []):
            try:
                if problem.get("PROBLEM_ACTIVITY") != "Active":
                    continue
                if problem.get("PROBLEM_CLASSIFICATION") == "PMH":
                    continue
                end_date = problem.get("PROBLEM_END_DATE")
                if end_date:
                    continue

                key = str(problem.get("PROBLEM_CODE", problem.get("code", "")))
                icd10 = _extract_icd10(
                    problem.get("code_mappings"),
                    problem.get("PROBLEM_CODE"),
                )

                # Z00.x codes are encounter-type administrative codes
                # (e.g. Z00.00 = "General adult medical examination") — not
                # clinical problems and must not appear in the problem list.
                if icd10 and icd10.startswith("Z00"):
                    continue

                resource: dict[str, Any] = {
                    "resourceType": "Condition",
                    "clinicalStatus": {
                        "coding": [
                            {
                                "system": _CONDITION_CLINICAL_SYSTEM,
                                "code": "active",
                            }
                        ]
                    },
                    "code": {
                        "coding": [
                            {
                                "system": _ICD10_SYSTEM,
                                "code": icd10 or "",
                            }
                        ],
                        "text": problem.get("value") or problem.get("PROBLEM_DESCRIPTION", ""),
                    },
                }
                seen[key] = resource
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed PROBLEM entry: %s", exc)

    return list(seen.values())


def _build_medication_requests(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deduplicated FHIR MedicationRequest resources from iEMR MEDICATIONS.

    Filters:
    - ``MED_ACTIVITY == "Discontinue"`` entries are excluded; the most-recent
      state for that MED_CODE is a discontinuation, so the drug is not active.

    Deduplication:
    - Primary: by ``MED_CODE`` (most-recent visit wins), same as all other types.
    - Secondary: by normalised medication name (upper-cased), because the same
      drug can appear under multiple distinct MED_CODEs across visits.

    Args:
        visits: All VISIT dicts for this patient.

    Returns:
        List of FHIR MedicationRequest resource dicts.
    """
    # None sentinel: MED_CODE is in a discontinued state.
    # discontinued_names: upper-cased drug names that have been discontinued
    # under ANY MED_CODE — used to propagate discontinuations across the multiple
    # prescription codes iEMR assigns to refills of the same drug.
    seen: dict[str, dict[str, Any] | None] = {}
    discontinued_names: set[str] = set()

    for visit in visits:
        for med in visit.get("MEDICATIONS", []):
            try:
                key = str(med.get("MED_CODE", med.get("code", "")))

                # Tombstone this MED_CODE and record the drug name as discontinued.
                # Any active entry under a different MED_CODE for the same drug
                # name will be filtered out in the secondary dedup step below.
                if med.get("MED_ACTIVITY") == "Discontinue":
                    seen[key] = None
                    disc_name = (
                        f"{med.get('MED_NAME', '')} {med.get('MED_DOSE', '')}".strip().upper()
                    )
                    if disc_name:
                        discontinued_names.add(disc_name)
                    continue

                med_name = med.get("MED_NAME", "")
                med_dose = med.get("MED_DOSE", "")
                med_text = f"{med_name} {med_dose}".strip()

                # Skip supplies, diagnostic tests, and device scripts that iEMR
                # stores in its MEDICATIONS array alongside actual pharmaceuticals.
                med_upper = med_text.upper()
                if med_upper in _NON_DRUG_EXACT or any(
                    marker in med_upper for marker in _NON_DRUG_MARKERS
                ):
                    logger.debug("Skipping non-drug MEDICATIONS entry: %r", med_text)
                    continue

                medication_concept: dict[str, Any] = {"text": med_text}
                rxnorm = _extract_rxnorm(med.get("code_mappings"))
                if rxnorm:
                    medication_concept["coding"] = [
                        {
                            "system": _RXNORM_SYSTEM,
                            "code": rxnorm,
                        }
                    ]

                resource: dict[str, Any] = {
                    "resourceType": "MedicationRequest",
                    "status": "active",
                    "intent": "order",
                    "medicationCodeableConcept": medication_concept,
                }
                authored_on = _parse_iemr_datetime(med.get("MED_DATE_ADDED"))
                if authored_on:
                    resource["authoredOn"] = authored_on

                seen[key] = resource
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed MEDICATIONS entry: %s", exc)

    # Secondary deduplication by normalised name.
    # A single drug can appear under multiple MED_CODEs across visits (e.g.
    # three refill prescriptions each assigned a new code).
    # Two exclusion rules:
    #   1. None sentinels (discontinued MED_CODE tombstones).
    #   2. Drug names in discontinued_names — catches the case where the
    #      Discontinue activity was recorded under a DIFFERENT MED_CODE than
    #      the active entry (common when EHR re-codes a refill as a new Rx).
    name_seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for resource in seen.values():
        if resource is None:
            continue  # tombstoned by MED_CODE
        name_key = resource["medicationCodeableConcept"]["text"].upper().strip()
        if name_key in discontinued_names:
            continue  # discontinued under a different MED_CODE for the same drug
        if name_key not in name_seen:
            name_seen.add(name_key)
            deduped.append(resource)
    return deduped


def _build_med_history(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a chronological medication history from all iEMR visits.

    Unlike :func:`_build_medication_requests`, this does **not** deduplicate
    by ``MED_CODE``.  Instead it collects every unique ``(name, date, activity)``
    combination across all visits so the briefing layer can show the full
    timeline of medication events (new prescriptions, dose increases, refills).

    Args:
        visits: All VISIT dicts for this patient, ordered oldest-first.

    Returns:
        List of dicts sorted chronologically ascending by date (nulls last)::

            [{"name": str, "rxnorm": str | None, "date": str | None, "activity": str | None}, ...]
    """
    seen: set[tuple[str | None, str | None, str | None]] = set()
    history: list[dict[str, Any]] = []

    for visit in visits:
        for med in visit.get("MEDICATIONS", []):
            try:
                med_name = med.get("MED_NAME", "")
                med_dose = med.get("MED_DOSE", "")
                name = f"{med_name} {med_dose}".strip() or None
                if not name:
                    continue

                iso_dt = _parse_iemr_datetime(med.get("MED_DATE_ADDED"))
                date_str: str | None = iso_dt[:10] if iso_dt else None
                activity: str | None = med.get("MED_ACTIVITY") or None
                rxnorm = _extract_rxnorm(med.get("code_mappings"))

                key = (name, date_str, activity)
                if key in seen:
                    continue
                seen.add(key)

                history.append(
                    {"name": name, "rxnorm": rxnorm, "date": date_str, "activity": activity}
                )

                # Parse MED_ADJUD_TEXT for explicit stop/restart events.
                # These arise when a patient temporarily stopped a medication
                # (e.g. during illness) and the physician later resumed it.
                # Format: "DRUG: Restart; stopped on MM/DD/YYYY; restarted on MM/DD/YYYY."
                adjud_text = med.get("MED_ADJUD_TEXT") or ""
                if adjud_text and _ADJUD_RESTART_RE.search(adjud_text):
                    _extract_stop_restart_events(
                        adjud_text, med, name, rxnorm, seen, history
                    )

            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed MEDICATIONS entry in med_history: %s", exc)

    history.sort(key=lambda e: (e["date"] is None, e["date"] or ""))
    return history


def _extract_stop_restart_events(
    adjud_text: str,
    med: dict[str, Any],
    name: str,
    rxnorm: str | None,
    seen: set[tuple[str | None, str | None, str | None]],
    history: list[dict[str, Any]],
) -> None:
    """Parse MED_ADJUD_TEXT and inject stop/restart entries into med_history.

    Mutates ``seen`` and ``history`` in-place.  Logs a debug message when
    dates are redacted (de-identified data) and cannot be parsed.
    """
    stopped_match = _ADJUD_STOPPED_RE.search(adjud_text)
    restarted_match = _ADJUD_RESTARTED_RE.search(adjud_text)

    # Restart date: prefer explicit "restarted on" text; fall back to
    # MED_DATE_LAST_MODIFIED, which the iEMR updates at the restart visit.
    restart_date_str: str | None = None
    if restarted_match:
        iso = _parse_iemr_datetime(restarted_match.group(1))
        restart_date_str = iso[:10] if iso else None
    if not restart_date_str:
        last_modified = med.get("MED_DATE_LAST_MODIFIED")
        if last_modified:
            iso = _parse_iemr_datetime(last_modified)
            restart_date_str = iso[:10] if iso else None

    if restart_date_str:
        key = (name, restart_date_str, "restart")
        if key not in seen:
            seen.add(key)
            history.append({"name": name, "rxnorm": rxnorm,
                             "date": restart_date_str, "activity": "restart"})
            logger.debug("med_history: added restart event for %s on %s", name, restart_date_str)

    # Stop date: only available in non-de-identified data.
    stop_date_str: str | None = None
    if stopped_match:
        iso = _parse_iemr_datetime(stopped_match.group(1))
        stop_date_str = iso[:10] if iso else None

    if stop_date_str:
        key = (name, stop_date_str, "stop")
        if key not in seen:
            seen.add(key)
            history.append({"name": name, "rxnorm": rxnorm,
                             "date": stop_date_str, "activity": "stop"})
            logger.debug("med_history: added stop event for %s on %s", name, stop_date_str)
    else:
        logger.debug(
            "med_history: stop date unavailable for %s restart (de-identified or missing) "
            "— stop event not added; inactive window cannot be reconstructed",
            name,
        )


def _build_simple_observation(
    loinc_code: str,
    display: str,
    value: float,
    unit: str,
    ucum_code: str,
    effective_dt: str | None,
) -> dict[str, Any]:
    """Build a single-value FHIR Observation with ``valueQuantity``.

    Used for pulse, weight, SpO2, and temperature — which are scalar values
    rather than BP panels (which use ``component`` instead).
    """
    obs: dict[str, Any] = {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [{"system": _LOINC_SYSTEM, "code": loinc_code, "display": display}]
        },
        "valueQuantity": {
            "value": value,
            "unit": unit,
            "system": _UCUM_SYSTEM,
            "code": ucum_code,
        },
    }
    if effective_dt:
        obs["effectiveDateTime"] = effective_dt
    return obs


def _build_observations(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build FHIR Observation resources from iEMR VITALS arrays.

    Every vitals entry becomes its own Observation — no deduplication.
    CRITICAL: ``effectiveDateTime`` is taken from ``VITALS_DATETIME``,
    never from ``ADMIT_DATE``.

    BP panel observations (LOINC 55284-4) use ``component`` structure.
    Scalar vitals (pulse, weight, SpO2, temperature) use ``valueQuantity``.
    BP entries missing ``SYSTOLIC_BP`` or ``DIASTOLIC_BP`` are skipped.

    Args:
        visits: All VISIT dicts for this patient.

    Returns:
        List of FHIR Observation resource dicts.
    """
    resources: list[dict[str, Any]] = []

    for visit in visits:
        for vitals in visit.get("VITALS", []):
            try:
                effective_dt = _parse_iemr_datetime(vitals.get("VITALS_DATETIME"))

                # BP panel — required fields; skip entire vitals row if absent
                systolic_raw = vitals.get("SYSTOLIC_BP")
                diastolic_raw = vitals.get("DIASTOLIC_BP")
                if systolic_raw is None or diastolic_raw is None:
                    continue

                systolic = int(systolic_raw)
                diastolic = int(diastolic_raw)

                bp_resource: dict[str, Any] = {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {
                        "coding": [
                            {
                                "system": _LOINC_SYSTEM,
                                "code": _LOINC_BP_PANEL,
                                "display": "Blood pressure systolic and diastolic",
                            }
                        ]
                    },
                    "component": [
                        {
                            "code": {
                                "coding": [
                                    {
                                        "system": _LOINC_SYSTEM,
                                        "code": _LOINC_SYSTOLIC,
                                        "display": "Systolic blood pressure",
                                    }
                                ]
                            },
                            "valueQuantity": {
                                "value": systolic,
                                "unit": "mmHg",
                                "system": _UCUM_SYSTEM,
                                "code": _UCUM_MMHG,
                            },
                        },
                        {
                            "code": {
                                "coding": [
                                    {
                                        "system": _LOINC_SYSTEM,
                                        "code": _LOINC_DIASTOLIC,
                                        "display": "Diastolic blood pressure",
                                    }
                                ]
                            },
                            "valueQuantity": {
                                "value": diastolic,
                                "unit": "mmHg",
                                "system": _UCUM_SYSTEM,
                                "code": _UCUM_MMHG,
                            },
                        },
                    ],
                }
                if effective_dt:
                    bp_resource["effectiveDateTime"] = effective_dt
                resources.append(bp_resource)

                # Pulse (LOINC 8867-4)
                pulse_raw = vitals.get("PULSE")
                if pulse_raw is not None:
                    resources.append(_build_simple_observation(
                        _LOINC_PULSE, "Heart rate", float(pulse_raw),
                        "/min", "/min", effective_dt,
                    ))

                # Weight — iEMR stores in LB; convert to kg (LOINC 29463-7)
                weight_raw = vitals.get("WEIGHT")
                if weight_raw is not None:
                    weight_kg = round(float(weight_raw) * _LB_TO_KG, 1)
                    resources.append(_build_simple_observation(
                        _LOINC_WEIGHT, "Body weight", weight_kg,
                        "kg", "kg", effective_dt,
                    ))

                # SpO2 (LOINC 59408-5)
                spo2_raw = vitals.get("PULSEOXYGEN")
                if spo2_raw is not None:
                    resources.append(_build_simple_observation(
                        _LOINC_SPO2, "Oxygen saturation by pulse oximetry",
                        float(spo2_raw), "%", "%", effective_dt,
                    ))

                # Temperature (LOINC 8310-5)
                temp_raw = vitals.get("TEMPERATURE")
                if temp_raw is not None:
                    resources.append(_build_simple_observation(
                        _LOINC_TEMP, "Body temperature", float(temp_raw),
                        "[degF]", "[degF]", effective_dt,
                    ))

            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed VITALS entry: %s", exc)

    return resources


def _build_allergy_intolerances(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deduplicated FHIR AllergyIntolerance resources from iEMR ALLERGY arrays.

    Filters:
    - ``ALLERGY_STATUS == "Active"`` only — inactive allergies must not appear
      in briefings (Fix 9).

    Captures:
    - ``ALLERGY_REACTION`` from the first ``ALLERGY_DETAIL`` entry, stored in
      ``reaction[0].manifestation[0].text`` for display in the briefing (Fix 9).

    Later visits overwrite earlier ones (most-recent wins).

    Args:
        visits: All VISIT dicts for this patient.

    Returns:
        List of FHIR AllergyIntolerance resource dicts.
    """
    seen: dict[str, dict[str, Any]] = {}

    for visit in visits:
        for allergy in visit.get("ALLERGY", []):
            try:
                # Only include allergies explicitly marked Active (Fix 9)
                if allergy.get("ALLERGY_STATUS") != "Active":
                    continue

                key = str(allergy.get("ALLERGY_CODE", allergy.get("code", "")))
                description = allergy.get("ALLERGY_DESCRIPTION") or allergy.get("value", "")

                resource: dict[str, Any] = {
                    "resourceType": "AllergyIntolerance",
                    "clinicalStatus": {
                        "coding": [
                            {
                                "system": _ALLERGY_CLINICAL_SYSTEM,
                                "code": "active",
                            }
                        ]
                    },
                    "code": {"text": description},
                }

                # Capture reaction type from ALLERGY_DETAIL (Fix 9)
                detail_list = allergy.get("ALLERGY_DETAIL", [])
                if detail_list:
                    reaction_text = detail_list[0].get("ALLERGY_REACTION", "")
                    if reaction_text:
                        resource["reaction"] = [
                            {"manifestation": [{"text": reaction_text}]}
                        ]

                seen[key] = resource
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed ALLERGY entry: %s", exc)

    return list(seen.values())


def _build_service_requests(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deduplicated FHIR ServiceRequest resources from iEMR PLAN arrays.

    Only PLAN entries with ``PLAN_NEEDS_FOLLOWUP == "YES"`` are included.
    Additionally, items that are clearly not clinical orders are excluded:
    - Physician names (text starts with ``"Dr."``)
    - Redacted vendor records (text contains ``"XXXXXXXXX"``)
    - Patient education items (text starts with ``"Instructions for"``
      or contains ``"General Advice"``)

    Later visits overwrite earlier ones (most-recent wins).

    Args:
        visits: All VISIT dicts for this patient.

    Returns:
        List of FHIR ServiceRequest resource dicts.
    """
    seen: dict[str, dict[str, Any]] = {}

    for visit in visits:
        for plan in visit.get("PLAN", []):
            try:
                # iEMR sentinel value — plans requiring follow-up
                if plan.get("PLAN_NEEDS_FOLLOWUP") != "YES":
                    continue

                key = str(plan.get("PLAN_CODE", plan.get("code", "")))
                plan_text = plan.get("value") or plan.get("PLAN_TITLE") or plan.get("PLAN_DESCRIPTION", "")

                # Skip non-clinical entries that iEMR stores alongside real orders.
                if (
                    plan_text.startswith("Dr.")
                    or "XXXXXXXXX" in plan_text
                    or plan_text.startswith("Instructions for")
                    or "General Advice" in plan_text
                ):
                    logger.debug("Skipping non-clinical PLAN entry: %r", plan_text)
                    continue

                resource: dict[str, Any] = {
                    "resourceType": "ServiceRequest",
                    "status": "active",
                    "intent": "order",
                    "code": {"text": plan_text},
                }
                seen[key] = resource
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed PLAN entry: %s", exc)

    return list(seen.values())


def _build_problem_assessments(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect per-visit physician problem assessment texts across all visits.

    Extracts ``PROBLEM_STATUS2_FLAG``, ``PROBLEM_STATUS2`` (status text), and
    ``PROBLEM_ASSESSMENT_TEXT`` (free-text physician note) for every active
    problem across every visit.  Problems without assessment text are skipped.

    Result is sorted by ``visit_date`` DESC (most recent first; None dates last)
    and deduplicated on ``(problem_code_or_name, visit_date)`` so re-running
    on the same iEMR data is idempotent.

    Stored as ``_aria_problem_assessments`` in the bundle dict and consumed by
    ``ingestion.py`` → ``clinical_context.problem_assessments`` JSONB column.
    Required by Phase 2 comorbidity-adjusted threshold (Fix 7).

    Args:
        visits: All VISIT dicts for this patient, ordered oldest-first.

    Returns:
        List of dicts::

            [{
                "problem_code":   str | None,   # ICD-10 code
                "visit_date":     str | None,   # ISO date "YYYY-MM-DD"
                "htn_flag":       bool,         # True when problem is HTN
                "status_text":    str,          # PROBLEM_STATUS2 text
                "assessment_text": str,         # PROBLEM_ASSESSMENT_TEXT (stripped)
                "status_flag":    int | None,   # 1=Red/urgent 2=Yellow 3=Green
            }, ...]
    """
    assessments: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()

    for visit in visits:
        visit_date: str | None = None
        admit = visit.get("ADMIT_DATE")
        if admit:
            parsed = _parse_iemr_datetime(admit)
            if parsed:
                visit_date = parsed[:10]

        for prob in visit.get("PROBLEM", []):
            try:
                assessment_text = (prob.get("PROBLEM_ASSESSMENT_TEXT") or "").strip()
                if not assessment_text:
                    continue

                icd10 = _extract_icd10(prob.get("code_mappings"), None)
                description = prob.get("PROBLEM_DESCRIPTION") or prob.get("value", "")
                dedup_key = (icd10 or description, visit_date)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                flag_raw = prob.get("PROBLEM_STATUS2_FLAG")
                status_flag: int | None = None
                if flag_raw is not None and str(flag_raw).strip().isdigit():
                    status_flag = int(str(flag_raw).strip())

                assessments.append({
                    "problem_code": icd10,
                    "visit_date": visit_date,
                    "htn_flag": "HYPERTENSION" in description.upper(),
                    "status_text": prob.get("PROBLEM_STATUS2") or "",
                    "assessment_text": assessment_text,
                    "status_flag": status_flag,
                })
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed PROBLEM entry in assessments: %s", exc)

    # Sort DESC by visit_date (None dates last)
    assessments.sort(
        key=lambda x: (x["visit_date"] is None, x["visit_date"] or ""),
        reverse=True,
    )
    return assessments


def _build_visit_dates(visits: list[dict[str, Any]]) -> list[str]:
    """Return sorted, deduplicated ISO date strings for all ADMIT_DATE values.

    Used to fix ``last_visit_date`` which previously only considered BP clinic
    dates (53 visits), missing 71 non-vitals visits (Fix 12).

    Args:
        visits: All VISIT dicts for this patient, ordered oldest-first.

    Returns:
        Sorted list of ISO date strings ``"YYYY-MM-DD"`` (ASC, no duplicates).
    """
    dates: set[str] = set()
    for visit in visits:
        admit = visit.get("ADMIT_DATE")
        if admit:
            parsed = _parse_iemr_datetime(admit)
            if parsed:
                dates.add(parsed[:10])
    return sorted(dates)


def _build_social_context(visits: list[dict[str, Any]]) -> str | None:
    """Join SOCIAL_HX entries from the most recent visit that has them.

    Searches visits in reverse chronological order and returns a semicolon-
    joined string of ``"DESCRIPTION: comment"`` entries from the first visit
    that has a non-empty ``SOCIAL_HX`` array (Fix 8).

    Args:
        visits: All VISIT dicts for this patient, ordered oldest-first.

    Returns:
        Semicolon-joined social history string, or ``None`` if no entries found.
    """
    for visit in reversed(visits):
        entries = visit.get("SOCIAL_HX", [])
        if not entries:
            continue
        parts: list[str] = []
        for entry in entries:
            desc = entry.get("SOCIAL_HX_DESCRIPTION", "").strip()
            comment = entry.get("SOCIAL_HX_COMMENT", "").strip().rstrip(".")
            if comment and comment != desc:
                parts.append(f"{desc}: {comment}" if desc else comment)
            elif desc:
                parts.append(desc)
        if parts:
            return "; ".join(parts)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert_iemr_to_fhir(
    iemr_data: dict[str, Any],
    patient_id: str | None = None,
    pseudonym_key: str = "",
) -> dict[str, Any]:
    """Convert an iEMR patient record to a FHIR R4 Bundle.

    The iEMR JSON format uses a literal ``"MED_REC_NO"`` key whose value is
    ``{"VISIT": [...]}`` containing one dict per clinical encounter.  The
    actual patient medical record number is not stored inside the JSON — it
    is implied by the filename (e.g. ``1091_data.json`` → patient ``"1091"``).
    Pass it explicitly via *patient_id*.

    Args:
        iemr_data: Parsed iEMR JSON dict, typically loaded from
            ``data/raw/iemr/<MED_REC_NO>_data.json``.
        patient_id: The patient's MED_REC_NO (e.g. ``"1091"``).  When
            ``None``, the first key of *iemr_data* is used as a fallback
            (useful in tests where the key IS the patient ID).
        pseudonym_key: Optional HMAC secret from ``PATIENT_PSEUDONYM_KEY``
            in config.  When non-empty, the patient ID is replaced with a
            16-char HMAC-SHA256 hex prefix before any data enters the DB.
            Activating this requires clearing the DB and re-ingesting (Fix 35).

    Returns:
        FHIR R4 Bundle dict of type ``"collection"`` containing all
        extracted resources, plus non-FHIR metadata keys consumed by
        ``ingestion.py``.

    Raises:
        ValueError: If *iemr_data* is empty or contains no visits.
    """
    if not iemr_data:
        raise ValueError("iemr_data is empty")

    # Real iEMR files use "MED_REC_NO" as the literal key for the visit list.
    # Other keys (PATIENT_STATUS, PERSON_ALLERGY, …) hold person-level data.
    first_key = next(iter(iemr_data))
    patient_record = iemr_data[first_key]

    # Resolve the patient ID: prefer the explicit argument, fall back to the
    # first key (which equals the patient ID in fixture / test data).
    resolved_id: str = patient_id if patient_id is not None else first_key

    # Apply HMAC pseudonymization if key is configured (Fix 35).
    # NOTE: activating this after initial ingestion requires clearing the DB
    # and re-running the full pipeline (patient_id "1091" → 16-char hash).
    if pseudonym_key:
        resolved_id = _pseudonymize_patient_id(resolved_id, pseudonym_key)

    visits: list[dict[str, Any]] = patient_record.get("VISIT", [])

    if not visits:
        raise ValueError(f"No VISIT entries found for patient {resolved_id!r}")

    logger.info(
        "Converting iEMR record for patient %r (%d visits)",
        resolved_id,
        len(visits),
    )

    patient_resource = _build_patient(resolved_id, visits)
    conditions = _build_conditions(visits)
    medication_requests = _build_medication_requests(visits)
    observations = _build_observations(visits)
    allergy_intolerances = _build_allergy_intolerances(visits)
    service_requests = _build_service_requests(visits)
    med_history = _build_med_history(visits)
    problem_assessments = _build_problem_assessments(visits)
    visit_dates = _build_visit_dates(visits)
    social_context = _build_social_context(visits)

    all_resources: list[dict[str, Any]] = (
        [patient_resource]
        + conditions
        + medication_requests
        + observations
        + allergy_intolerances
        + service_requests
    )

    logger.info(
        "Bundle assembled: 1 Patient, %d Condition, %d MedicationRequest, "
        "%d Observation, %d AllergyIntolerance, %d ServiceRequest",
        len(conditions),
        len(medication_requests),
        len(observations),
        len(allergy_intolerances),
        len(service_requests),
    )
    logger.info(
        "Non-FHIR metadata: med_history=%d problem_assessments=%d visit_dates=%d",
        len(med_history),
        len(problem_assessments),
        len(visit_dates),
    )

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in all_resources],
        "_aria_med_history": med_history,            # full medication timeline
        "_aria_problem_assessments": problem_assessments,  # physician assessment texts
        "_aria_visit_dates": visit_dates,            # all 124 visit dates (Fix 12)
        "_aria_social_context": social_context,      # SOCIAL_HX free text (Fix 8)
    }
