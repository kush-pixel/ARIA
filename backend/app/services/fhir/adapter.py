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
"""

from __future__ import annotations

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

# Non-standard extension key used to pass age from adapter to
# ingestion layer. Both files must use this same constant.
_PATIENT_AGE_EXT = "_age"

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


def _parse_iemr_datetime(date_str: str | None) -> str | None:
    """Convert an iEMR date string to ISO 8601 format.

    Args:
        date_str: Date in ``MM/DD/YYYY HH:MM`` format, or ``None``.

    Returns:
        ISO 8601 string ``YYYY-MM-DDTHH:MM:00``, or ``None`` on failure.
    """
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), _IEMR_DATE_FMT)
        return dt.strftime("%Y-%m-%dT%H:%M:00")
    except ValueError:
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

    Later visits overwrite earlier ones (most-recent wins).

    Args:
        visits: All VISIT dicts for this patient.

    Returns:
        List of FHIR MedicationRequest resource dicts.
    """
    seen: dict[str, dict[str, Any]] = {}

    for visit in visits:
        for med in visit.get("MEDICATIONS", []):
            try:
                key = str(med.get("MED_CODE", med.get("code", "")))
                med_name = med.get("MED_NAME", "")
                med_dose = med.get("MED_DOSE", "")
                med_text = f"{med_name} {med_dose}".strip()

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

    return list(seen.values())


def _build_observations(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build FHIR Observation resources from iEMR VITALS arrays.

    Every vitals entry becomes its own Observation — no deduplication.
    CRITICAL: ``effectiveDateTime`` is taken from ``VITALS_DATETIME``,
    never from ``ADMIT_DATE``.

    Entries missing ``SYSTOLIC_BP`` or ``DIASTOLIC_BP`` are skipped.

    Args:
        visits: All VISIT dicts for this patient.

    Returns:
        List of FHIR Observation resource dicts.
    """
    resources: list[dict[str, Any]] = []

    for visit in visits:
        for vitals in visit.get("VITALS", []):
            try:
                systolic_raw = vitals.get("SYSTOLIC_BP")
                diastolic_raw = vitals.get("DIASTOLIC_BP")
                if systolic_raw is None or diastolic_raw is None:
                    continue

                systolic = int(systolic_raw)
                diastolic = int(diastolic_raw)

                effective_dt = _parse_iemr_datetime(vitals.get("VITALS_DATETIME"))

                resource: dict[str, Any] = {
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
                    resource["effectiveDateTime"] = effective_dt

                resources.append(resource)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed VITALS entry: %s", exc)

    return resources


def _build_allergy_intolerances(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deduplicated FHIR AllergyIntolerance resources from iEMR ALLERGY arrays.

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
                seen[key] = resource
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed ALLERGY entry: %s", exc)

    return list(seen.values())


def _build_service_requests(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deduplicated FHIR ServiceRequest resources from iEMR PLAN arrays.

    Only PLAN entries with ``PLAN_NEEDS_FOLLOWUP == "YES"`` are included.
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert_iemr_to_fhir(
    iemr_data: dict[str, Any],
    patient_id: str | None = None,
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

    Returns:
        FHIR R4 Bundle dict of type ``"collection"`` containing all
        extracted resources.

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

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in all_resources],
    }
