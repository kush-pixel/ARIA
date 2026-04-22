"""Unit and integration tests for backend/app/services/fhir/adapter.py.

Unit tests use only fixture data — no real patient records.
Integration tests are marked @pytest.mark.integration and read the
actual iEMR file from data/raw/iemr/1091_data.json.

Run unit tests only (CI-safe):
    cd backend && python -m pytest tests/test_fhir_adapter.py -v -m "not integration"

Run all tests (requires real data file):
    cd backend && python -m pytest tests/test_fhir_adapter.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.fhir.adapter import (
    _build_med_history,
    _map_gender,
    _parse_iemr_datetime,
    convert_iemr_to_fhir,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATIENT_ID = "TEST001"


def _make_iemr(visits: list[dict]) -> dict:
    """Wrap visits in the iEMR top-level structure."""
    return {PATIENT_ID: {"VISIT": visits}}


def _make_visit(
    *,
    gender: str = "M",
    age: int = 65,
    problems: list[dict] | None = None,
    medications: list[dict] | None = None,
    vitals: list[dict] | None = None,
    allergies: list[dict] | None = None,
    plans: list[dict] | None = None,
    admit_date: str = "01/01/2020 09:00",
) -> dict:
    return {
        "GENDER": gender,
        "AGE": age,
        "ADMIT_DATE": admit_date,
        "PROBLEM": problems or [],
        "MEDICATIONS": medications or [],
        "VITALS": vitals or [],
        "ALLERGY": allergies or [],
        "PLAN": plans or [],
    }


def _active_problem(
    code: str = "P001",
    value: str = "Hypertension",
    icd10: str = "I10",
    classification: str = "Working Diagnosis",
) -> dict:
    return {
        "code": "internal_1",
        "PROBLEM_CODE": code,
        "value": value,
        "PROBLEM_ACTIVITY": "Active",
        "PROBLEM_CLASSIFICATION": classification,
        "code_mappings": {
            "code_mappings": [{"code": icd10, "code_type": "ICD10"}]
        },
    }


def _medication(
    med_code: str = "M001",
    name: str = "Lisinopril",
    dose: str = "10 mg",
    rxnorm: str = "314076",
    date_added: str = "01/14/2008 14:45",
) -> dict:
    return {
        "code": "internal_2",
        "MED_CODE": med_code,
        "MED_NAME": name,
        "MED_DOSE": dose,
        "MED_DATE_ADDED": date_added,
        "code_mappings": {
            "code_mappings": [{"code": rxnorm, "code_type": "RxNORM"}]
        },
    }


def _vitals(
    systolic: str = "145",
    diastolic: str = "90",
    vitals_datetime: str = "01/21/2008 10:28",
) -> dict:
    return {
        "SYSTOLIC_BP": systolic,
        "DIASTOLIC_BP": diastolic,
        "VITALS_DATETIME": vitals_datetime,
    }


def _allergy(
    allergy_code: str = "A001",
    description: str = "Penicillin",
) -> dict:
    return {
        "code": "internal_3",
        "ALLERGY_CODE": allergy_code,
        "ALLERGY_DESCRIPTION": description,
    }


def _plan(
    plan_code: str = "PL001",
    value: str = "C-Peptide, Serum",
    needs_followup: str = "YES",
) -> dict:
    return {
        "code": "internal_4",
        "PLAN_CODE": plan_code,
        "value": value,
        "PLAN_NEEDS_FOLLOWUP": needs_followup,
    }


# ---------------------------------------------------------------------------
# _parse_iemr_datetime
# ---------------------------------------------------------------------------


def test_parse_iemr_datetime_valid() -> None:
    result = _parse_iemr_datetime("01/21/2008 10:28")
    assert result == "2008-01-21T10:28:00"


def test_parse_iemr_datetime_none_returns_none() -> None:
    assert _parse_iemr_datetime(None) is None


def test_parse_iemr_datetime_empty_returns_none() -> None:
    assert _parse_iemr_datetime("") is None


def test_parse_iemr_datetime_invalid_returns_none() -> None:
    assert _parse_iemr_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# _map_gender
# ---------------------------------------------------------------------------


def test_gender_mapping_string_male() -> None:
    assert _map_gender("M") == "male"
    assert _map_gender("m") == "male"


def test_gender_mapping_string_female() -> None:
    assert _map_gender("F") == "female"
    assert _map_gender("f") == "female"


def test_gender_mapping_string_unknown() -> None:
    assert _map_gender("U") == "unknown"
    assert _map_gender(None) == "unknown"
    assert _map_gender("X") == "unknown"


def test_gender_mapping_numeric() -> None:
    assert _map_gender("1") == "male"
    assert _map_gender("2") == "female"


# ---------------------------------------------------------------------------
# Bundle structure
# ---------------------------------------------------------------------------


def test_bundle_structure() -> None:
    data = _make_iemr([_make_visit()])
    bundle = convert_iemr_to_fhir(data)
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    assert isinstance(bundle["entry"], list)
    assert len(bundle["entry"]) >= 1


def test_convert_empty_visits_raises() -> None:
    data = _make_iemr([])
    with pytest.raises(ValueError, match="No VISIT"):
        convert_iemr_to_fhir(data)


def test_convert_empty_data_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        convert_iemr_to_fhir({})


# ---------------------------------------------------------------------------
# Patient resource
# ---------------------------------------------------------------------------


def test_patient_resource_fields() -> None:
    data = _make_iemr([_make_visit(gender="F", age=72)])
    bundle = convert_iemr_to_fhir(data)
    patients = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Patient"
    ]
    assert len(patients) == 1
    patient = patients[0]
    assert patient["id"] == PATIENT_ID
    assert patient["gender"] == "female"
    assert patient["_age"] == 72


def test_patient_is_first_entry() -> None:
    data = _make_iemr([_make_visit()])
    bundle = convert_iemr_to_fhir(data)
    assert bundle["entry"][0]["resource"]["resourceType"] == "Patient"


# ---------------------------------------------------------------------------
# Condition resources
# ---------------------------------------------------------------------------


def test_condition_active_only() -> None:
    """Inactive and PMH problems must be excluded."""
    visit = _make_visit(
        problems=[
            _active_problem(code="P001", value="Hypertension"),
            {**_active_problem(code="P002", value="Old fracture"), "PROBLEM_ACTIVITY": "Inactive"},
            {**_active_problem(code="P003", value="Family history"), "PROBLEM_CLASSIFICATION": "PMH"},
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    conditions = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Condition"
    ]
    assert len(conditions) == 1
    assert conditions[0]["code"]["text"] == "Hypertension"


def test_condition_end_date_excludes() -> None:
    """Problems with a non-empty PROBLEM_END_DATE must be excluded."""
    visit = _make_visit(
        problems=[
            {
                **_active_problem(code="P001"),
                "PROBLEM_END_DATE": "06/01/2010 00:00",
            }
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    conditions = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Condition"
    ]
    assert len(conditions) == 0


def test_condition_icd10_code() -> None:
    visit = _make_visit(problems=[_active_problem(code="P001", icd10="I10")])
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    condition = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Condition"
    )
    coding = condition["code"]["coding"][0]
    assert coding["code"] == "I10"
    assert coding["system"] == "http://hl7.org/fhir/sid/icd-10"


def test_condition_clinical_status_active() -> None:
    visit = _make_visit(problems=[_active_problem()])
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    condition = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Condition"
    )
    assert condition["clinicalStatus"]["coding"][0]["code"] == "active"


# ---------------------------------------------------------------------------
# Observation resources
# ---------------------------------------------------------------------------


def test_observation_uses_vitals_datetime() -> None:
    """effectiveDateTime must come from VITALS_DATETIME, not ADMIT_DATE."""
    visit = _make_visit(
        vitals=[_vitals(vitals_datetime="03/15/2022 08:30")],
        admit_date="01/01/2022 09:00",
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    obs = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
    )
    assert obs["effectiveDateTime"] == "2022-03-15T08:30:00"
    assert obs["effectiveDateTime"] != "2022-01-01T09:00:00"


def test_observation_skips_missing_bp() -> None:
    """VITALS entries without SYSTOLIC_BP or DIASTOLIC_BP must be omitted."""
    visit = _make_visit(
        vitals=[
            {"VITALS_DATETIME": "03/15/2022 08:30"},  # missing both
            {"SYSTOLIC_BP": "140", "VITALS_DATETIME": "03/16/2022 08:30"},  # missing diastolic
            _vitals(),  # complete — should be included
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    observations = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
    ]
    assert len(observations) == 1


def test_observation_bp_values_and_loinc() -> None:
    visit = _make_visit(vitals=[_vitals(systolic="185", diastolic="72")])
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    obs = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
    )
    assert obs["code"]["coding"][0]["code"] == "55284-4"
    assert obs["code"]["coding"][0]["system"] == "http://loinc.org"
    systolic_comp = obs["component"][0]
    diastolic_comp = obs["component"][1]
    assert systolic_comp["code"]["coding"][0]["code"] == "8480-6"
    assert systolic_comp["valueQuantity"]["value"] == 185
    assert systolic_comp["valueQuantity"]["unit"] == "mmHg"
    assert diastolic_comp["code"]["coding"][0]["code"] == "8462-4"
    assert diastolic_comp["valueQuantity"]["value"] == 72


def test_observations_not_deduplicated() -> None:
    """Two identical vitals entries across two visits must produce two Observations."""
    v = _vitals()
    data = _make_iemr([_make_visit(vitals=[v]), _make_visit(vitals=[v])])
    bundle = convert_iemr_to_fhir(data)
    observations = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
    ]
    assert len(observations) == 2


# ---------------------------------------------------------------------------
# MedicationRequest resources
# ---------------------------------------------------------------------------


def test_medication_request_fields() -> None:
    visit = _make_visit(
        medications=[_medication(name="Lisinopril", dose="10 mg", rxnorm="314076")]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    med_req = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "MedicationRequest"
    )
    concept = med_req["medicationCodeableConcept"]
    assert concept["text"] == "Lisinopril 10 mg"
    assert concept["coding"][0]["code"] == "314076"
    assert concept["coding"][0]["system"] == "http://www.nlm.nih.gov/research/umls/rxnorm"
    assert med_req["authoredOn"] == "2008-01-14T14:45:00"


def test_medication_request_no_rxnorm_omits_coding() -> None:
    med = {
        "MED_CODE": "M999",
        "MED_NAME": "SomeDrug",
        "MED_DOSE": "5 mg",
        "MED_DATE_ADDED": "01/01/2020 00:00",
        "code_mappings": {"code_mappings": []},
    }
    data = _make_iemr([_make_visit(medications=[med])])
    bundle = convert_iemr_to_fhir(data)
    med_req = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "MedicationRequest"
    )
    assert "coding" not in med_req["medicationCodeableConcept"]


# ---------------------------------------------------------------------------
# AllergyIntolerance resources
# ---------------------------------------------------------------------------


def test_allergy_intolerance_fields() -> None:
    visit = _make_visit(allergies=[_allergy(description="Penicillin")])
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    allergy = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "AllergyIntolerance"
    )
    assert allergy["code"]["text"] == "Penicillin"
    assert allergy["clinicalStatus"]["coding"][0]["code"] == "active"


# ---------------------------------------------------------------------------
# ServiceRequest resources
# ---------------------------------------------------------------------------


def test_service_request_followup_filter() -> None:
    """Only PLAN_NEEDS_FOLLOWUP=YES entries must be included."""
    visit = _make_visit(
        plans=[
            _plan(plan_code="PL001", value="Lab A", needs_followup="YES"),
            _plan(plan_code="PL002", value="Lab B", needs_followup="NO"),
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    requests = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "ServiceRequest"
    ]
    assert len(requests) == 1
    assert requests[0]["code"]["text"] == "Lab A"
    assert requests[0]["status"] == "active"
    assert requests[0]["intent"] == "order"


def test_service_request_filters_physician_names() -> None:
    """PLAN entries that are physician names (starting 'Dr.') must be excluded."""
    visit = _make_visit(
        plans=[
            _plan(plan_code="PL001", value="C-Peptide, Serum", needs_followup="YES"),
            _plan(plan_code="PL002", value="Dr. Gary Rogers", needs_followup="YES"),
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    requests = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "ServiceRequest"
    ]
    texts = [r["code"]["text"] for r in requests]
    assert "C-Peptide, Serum" in texts
    assert "Dr. Gary Rogers" not in texts


def test_service_request_filters_redacted_vendors() -> None:
    """PLAN entries containing 'XXXXXXXXX' (redacted vendors) must be excluded."""
    visit = _make_visit(
        plans=[
            _plan(plan_code="PL001", value="Cardiology Consult", needs_followup="YES"),
            _plan(plan_code="PL002", value="XXXXXXXXX's Medical Surgical Supply", needs_followup="YES"),
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    requests = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "ServiceRequest"
    ]
    texts = [r["code"]["text"] for r in requests]
    assert "Cardiology Consult" in texts
    assert "XXXXXXXXX's Medical Surgical Supply" not in texts


def test_service_request_filters_patient_education() -> None:
    """PLAN entries that are patient education items must be excluded."""
    visit = _make_visit(
        plans=[
            _plan(plan_code="PL001", value="GLYCOHEMOGLOBIN (Pending)", needs_followup="YES"),
            _plan(plan_code="PL002", value="Instructions for Sliding Scale Fast Acting Insulin", needs_followup="YES"),
            _plan(plan_code="PL003", value="Hypoglycemia - General Advice on Treatment", needs_followup="YES"),
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    requests = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "ServiceRequest"
    ]
    texts = [r["code"]["text"] for r in requests]
    assert "GLYCOHEMOGLOBIN (Pending)" in texts
    assert "Instructions for Sliding Scale Fast Acting Insulin" not in texts
    assert "Hypoglycemia - General Advice on Treatment" not in texts


# ---------------------------------------------------------------------------
# Deduplication (most-recent-wins)
# ---------------------------------------------------------------------------


def test_condition_filters_z00_encounter_codes() -> None:
    """Problems with Z00.x ICD-10 codes (encounter types) must be excluded."""
    visit = _make_visit(
        problems=[
            _active_problem(code="Z001", value="PREVENTIVE CARE", icd10="Z00.00"),
            _active_problem(code="I10", value="Hypertension", icd10="I10"),
        ]
    )
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    conditions = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Condition"
    ]
    assert len(conditions) == 1
    assert conditions[0]["code"]["text"] == "Hypertension"


def test_medication_discontinued_excluded() -> None:
    """Medications with MED_ACTIVITY='Discontinue' must not appear in the bundle."""
    med_active = {**_medication(med_code="M001", name="Lisinopril", dose="10 mg"), "MED_ACTIVITY": "Refill"}
    med_stopped = {**_medication(med_code="M002", name="Simvastatin", dose="20 mg"), "MED_ACTIVITY": "Discontinue"}
    visit = _make_visit(medications=[med_active, med_stopped])
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    meds = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "MedicationRequest"
    ]
    names = [m["medicationCodeableConcept"]["text"] for m in meds]
    assert any("Lisinopril" in n for n in names)
    assert not any("Simvastatin" in n for n in names)


def test_medication_discontinued_propagates_across_med_codes() -> None:
    """A Discontinue on one MED_CODE must exclude the same drug name from a different active MED_CODE."""
    # Same drug prescribed under two different MED_CODEs across two visits;
    # the second visit records a Discontinue under a new code.
    med_visit1 = {**_medication(med_code="M001", name="Byetta", dose=""), "MED_ACTIVITY": ""}
    med_visit2 = {**_medication(med_code="M002", name="Byetta", dose=""), "MED_ACTIVITY": "Discontinue"}
    data = _make_iemr([
        _make_visit(medications=[med_visit1]),
        _make_visit(medications=[med_visit2]),
    ])
    bundle = convert_iemr_to_fhir(data)
    meds = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "MedicationRequest"
    ]
    names = [m["medicationCodeableConcept"]["text"] for m in meds]
    assert not any("Byetta" in n for n in names), (
        "Byetta should be excluded: a later visit discontinued it under a different MED_CODE"
    )


def test_medication_name_deduplication_across_med_codes() -> None:
    """Same drug under two different MED_CODEs must produce only one MedicationRequest."""
    med_a = _medication(med_code="M001", name="Namenda", dose="")
    med_b = _medication(med_code="M002", name="Namenda", dose="")  # distinct MED_CODE, same name
    visit = _make_visit(medications=[med_a, med_b])
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    meds = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "MedicationRequest"
    ]
    assert len(meds) == 1


def test_deduplication_condition_keeps_last() -> None:
    """Same PROBLEM_CODE across two visits: most recent visit must win."""
    visit_old = _make_visit(
        problems=[_active_problem(code="P001", value="Old Hypertension")]
    )
    visit_new = _make_visit(
        problems=[_active_problem(code="P001", value="Hypertension - Updated")]
    )
    data = _make_iemr([visit_old, visit_new])
    bundle = convert_iemr_to_fhir(data)
    conditions = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Condition"
    ]
    assert len(conditions) == 1
    assert conditions[0]["code"]["text"] == "Hypertension - Updated"


def test_deduplication_medication_keeps_last() -> None:
    """Same MED_CODE across two visits: most recent visit must win."""
    visit_old = _make_visit(medications=[_medication(med_code="M001", dose="5 mg")])
    visit_new = _make_visit(medications=[_medication(med_code="M001", dose="10 mg")])
    data = _make_iemr([visit_old, visit_new])
    bundle = convert_iemr_to_fhir(data)
    meds = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "MedicationRequest"
    ]
    assert len(meds) == 1
    assert "10 mg" in meds[0]["medicationCodeableConcept"]["text"]


# ---------------------------------------------------------------------------
# _build_med_history
# ---------------------------------------------------------------------------


def _medication_with_date(
    name: str = "Lisinopril",
    dose: str = "10 mg",
    date_added: str = "01/14/2008 14:45",
    activity: str = "New",
    rxnorm: str = "314076",
    med_code: str = "M001",
) -> dict:
    """Convenience builder for a medication entry with explicit date and activity."""
    return {
        "code": "internal_x",
        "MED_CODE": med_code,
        "MED_NAME": name,
        "MED_DOSE": dose,
        "MED_DATE_ADDED": date_added,
        "MED_ACTIVITY": activity,
        "code_mappings": {
            "code_mappings": [{"code": rxnorm, "code_type": "RxNORM"}]
        },
    }


class TestBuildMedHistory:
    def test_empty_visits_returns_empty_list(self) -> None:
        assert _build_med_history([]) == []

    def test_single_entry_extracted(self) -> None:
        visits = [_make_visit(medications=[_medication_with_date()])]
        history = _build_med_history(visits)
        assert len(history) == 1
        entry = history[0]
        assert entry["name"] == "Lisinopril 10 mg"
        assert entry["rxnorm"] == "314076"
        assert entry["date"] == "2008-01-14"
        assert entry["activity"] == "New"

    def test_deduplication_same_name_date_activity(self) -> None:
        """Same (name, date, activity) in two visits produces only one entry."""
        med = _medication_with_date(date_added="01/14/2008 14:45", activity="Refill")
        visits = [_make_visit(medications=[med]), _make_visit(medications=[med])]
        history = _build_med_history(visits)
        assert len(history) == 1

    def test_different_activities_not_deduplicated(self) -> None:
        """Same drug and date but different activity = two distinct events."""
        med_new = _medication_with_date(date_added="01/14/2008 14:45", activity="New")
        med_refill = _medication_with_date(date_added="01/14/2008 14:45", activity="Refill")
        visits = [_make_visit(medications=[med_new, med_refill])]
        history = _build_med_history(visits)
        assert len(history) == 2
        activities = {e["activity"] for e in history}
        assert activities == {"New", "Refill"}

    def test_sorted_chronologically(self) -> None:
        med_newer = _medication_with_date(name="Drug A", date_added="06/01/2020 09:00")
        med_older = _medication_with_date(name="Drug B", date_added="03/15/2015 09:00", med_code="M002")
        visits = [_make_visit(medications=[med_newer, med_older])]
        history = _build_med_history(visits)
        assert history[0]["date"] == "2015-03-15"
        assert history[1]["date"] == "2020-06-01"

    def test_nulls_last_in_sort(self) -> None:
        med_no_date = {
            "MED_CODE": "M_NODATE",
            "MED_NAME": "NullDrug",
            "MED_DOSE": "5 mg",
            "MED_ACTIVITY": "New",
        }
        med_with_date = _medication_with_date(date_added="01/01/2010 00:00", med_code="M_DATE")
        visits = [_make_visit(medications=[med_no_date, med_with_date])]
        history = _build_med_history(visits)
        assert history[-1]["date"] is None

    def test_med_without_name_skipped(self) -> None:
        med_no_name = {
            "MED_CODE": "M_EMPTY",
            "MED_NAME": "",
            "MED_DOSE": "",
            "MED_DATE_ADDED": "01/01/2020 00:00",
            "MED_ACTIVITY": "New",
        }
        visits = [_make_visit(medications=[med_no_name])]
        history = _build_med_history(visits)
        assert history == []


def test_bundle_contains_aria_med_history_key() -> None:
    """convert_iemr_to_fhir must attach _aria_med_history to the bundle dict."""
    visit = _make_visit(medications=[_medication_with_date()])
    data = _make_iemr([visit])
    bundle = convert_iemr_to_fhir(data)
    assert "_aria_med_history" in bundle
    assert isinstance(bundle["_aria_med_history"], list)
    assert len(bundle["_aria_med_history"]) == 1


# ---------------------------------------------------------------------------
# Integration tests (require real data file)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_run_on_1091_data() -> None:
    """End-to-end test on real iEMR data for patient 1091."""
    data_path = (
        Path(__file__).resolve().parents[2] / "data" / "raw" / "iemr" / "1091_data.json"
    )
    if not data_path.exists():
        pytest.skip(f"Real data file not found: {data_path}")

    with data_path.open(encoding="utf-8") as fh:
        iemr_data = json.load(fh)

    bundle = convert_iemr_to_fhir(iemr_data, patient_id="1091")

    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"

    resources_by_type: dict[str, list[dict]] = {}
    for entry in bundle["entry"]:
        rt = entry["resource"]["resourceType"]
        resources_by_type.setdefault(rt, []).append(entry["resource"])

    # Patient
    assert "Patient" in resources_by_type
    assert resources_by_type["Patient"][0]["id"] == "1091"

    # At least one of each expected type
    for rt in ("Condition", "MedicationRequest", "Observation", "AllergyIntolerance"):
        assert rt in resources_by_type, f"Expected at least one {rt} resource"

    # Critical: Observations must NOT have effectiveDateTime derived from ADMIT_DATE.
    # The iEMR ADMIT_DATE for this record is "10/06/2004 17:27" -> "2004-10-06T17:27:00".
    # No Observation should carry that value; each should have a VITALS_DATETIME-derived dt.
    admit_date_iso = "2004-10-06T17:27:00"
    for obs in resources_by_type.get("Observation", []):
        eff_dt = obs.get("effectiveDateTime", "")
        assert eff_dt != admit_date_iso, (
            f"Observation effectiveDateTime {eff_dt!r} matches ADMIT_DATE — "
            "must use VITALS_DATETIME instead"
        )
