"""Unit and integration tests for FHIR bundle ingestion.

Unit tests use only fixture data — no real database connection.
Integration tests are marked @pytest.mark.integration and require
DATABASE_URL set in backend/.env and a live Supabase connection.

Run unit tests only (CI-safe):
    cd backend && python -m pytest tests/test_ingestion.py -v -m "not integration"

Run all tests (requires DB):
    cd backend && python -m pytest tests/test_ingestion.py -v
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.models.audit_event import AuditEvent
from app.models.reading import Reading
from app.services.fhir.ingestion import (
    _determine_risk_tier,
    _extract_obs_components,
    _get_obs_loinc,
    _get_obs_scalar_value,
    _group_entries,
    _parse_authored_on,
    ingest_fhir_bundle,
)
from app.services.fhir.validator import validate_fhir_bundle

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATIENT_ID = "TEST001"


def _patient_resource(patient_id: str = PATIENT_ID, gender: str = "male", age: int = 65) -> dict:
    return {"resourceType": "Patient", "id": patient_id, "gender": gender, "_age": age}


def _condition_resource(text: str, code: str) -> dict:
    return {
        "resourceType": "Condition",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {
            "coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": code}],
            "text": text,
        },
    }


def _medication_resource(text: str, rxnorm: str = "315431", authored_on: str = "2020-01-01") -> dict:
    return {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {
            "text": text,
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": rxnorm}],
        },
        "authoredOn": authored_on,
    }


def _observation_resource(
    systolic: int = 150,
    diastolic: int = 90,
    effective_dt: str = "2020-06-15T09:00:00",
) -> dict:
    # Top-level LOINC 55284-4 (BP panel) is required so _get_obs_loinc() can
    # identify this as a BP observation (matching what adapter.py produces).
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "55284-4"}]},
        "component": [
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
                "valueQuantity": {"value": systolic, "unit": "mmHg"},
            },
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8462-4"}]},
                "valueQuantity": {"value": diastolic, "unit": "mmHg"},
            },
        ],
        "effectiveDateTime": effective_dt,
    }


def _allergy_resource(text: str) -> dict:
    return {
        "resourceType": "AllergyIntolerance",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {"text": text},
    }


def _service_request_resource(text: str) -> dict:
    return {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "code": {"text": text},
    }


def _make_bundle(resources: list[dict]) -> dict:
    """Wrap resource dicts in FHIR Bundle entry structure."""
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in resources],
    }


@pytest.fixture
def minimal_bundle() -> dict:
    """Bundle with only a Patient resource — no observations."""
    return _make_bundle([_patient_resource()])


@pytest.fixture
def full_bundle() -> dict:
    """Bundle with all resource types and one clinic BP observation."""
    bundle = _make_bundle(
        [
            _patient_resource(),
            _condition_resource("HYPERTENSION", "I10"),
            _medication_resource("LISINOPRIL 10MG", "29046", "2020-03-15"),
            _observation_resource(185, 72, "2020-06-15T09:00:00"),
            _allergy_resource("SULFA"),
            _service_request_resource("HbA1c"),
        ]
    )
    bundle["_aria_med_history"] = [
        {"name": "LISINOPRIL 10MG", "rxnorm": "29046", "date": "2020-03-15", "activity": "New"}
    ]
    return bundle


@pytest.fixture
def chf_bundle() -> dict:
    """Bundle where patient has CHF — should trigger high-tier override."""
    return _make_bundle(
        [
            _patient_resource(),
            _condition_resource("CHF", "I50.0"),
            _observation_resource(160, 95),
        ]
    )


def _make_mock_session(
    patient_exists: bool = False,
    reading_rowcount: int = 1,
) -> MagicMock:
    """Build a mock AsyncSession with pre-configured execute side effects.

    Call order that ingestion.py follows (per-observation ON CONFLICT path):
      0. SELECT Patient (existence check)
      1. INSERT Patient ON CONFLICT DO UPDATE (demographics only)
      2. UPDATE Patient tier columns (conditional — Step B)
      3. INSERT/UPSERT ClinicalContext ON CONFLICT DO UPDATE
      4..N. INSERT Reading ON CONFLICT DO NOTHING (one per Observation)
    """
    # Result for SELECT Patient
    existing_patient = MagicMock()
    existing_patient.scalar_one_or_none.return_value = (
        MagicMock() if patient_exists else None
    )

    # Result for INSERT Patient (Step A — demographics upsert)
    insert_patient_result = MagicMock()

    # Result for UPDATE Patient tier columns (Step B — conditional)
    tier_update_result = MagicMock()

    # Result for UPSERT ClinicalContext
    upsert_cc_result = MagicMock()

    # Result for INSERT Reading ON CONFLICT DO NOTHING
    # rowcount=1 means inserted, rowcount=0 means conflict/skipped
    reading_insert_result = MagicMock()
    reading_insert_result.rowcount = reading_rowcount

    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            existing_patient,
            insert_patient_result,
            tier_update_result,
            upsert_cc_result,
            reading_insert_result,  # one per Observation in the bundle
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# validate_fhir_bundle — pure function tests
# ---------------------------------------------------------------------------


class TestValidateFhirBundle:
    def test_valid_bundle_returns_empty_list(self, full_bundle: dict) -> None:
        errors = validate_fhir_bundle(full_bundle)
        assert errors == []

    def test_wrong_resource_type_returns_error(self) -> None:
        errors = validate_fhir_bundle({"resourceType": "Patient", "entry": []})
        assert any("resourceType" in e for e in errors)

    def test_missing_patient_resource_returns_error(self) -> None:
        bundle = _make_bundle([_condition_resource("HTN", "I10")])
        errors = validate_fhir_bundle(bundle)
        assert any("Patient" in e for e in errors)

    def test_patient_missing_id_returns_error(self) -> None:
        bundle = _make_bundle([{"resourceType": "Patient", "gender": "male"}])
        errors = validate_fhir_bundle(bundle)
        assert any("id" in e for e in errors)

    def test_not_a_dict_returns_error(self) -> None:
        errors = validate_fhir_bundle("not a dict")  # type: ignore[arg-type]
        assert errors != []

    def test_minimal_valid_bundle(self, minimal_bundle: dict) -> None:
        assert validate_fhir_bundle(minimal_bundle) == []


# ---------------------------------------------------------------------------
# _determine_risk_tier — pure function tests
# ---------------------------------------------------------------------------


class TestDetermineRiskTier:
    def test_chf_code_returns_high(self) -> None:
        tier, override, source = _determine_risk_tier(["I50.0"])
        assert tier == "high"
        assert override == "CHF in problem list"
        assert source == "system"

    def test_chf_code_prefix_i50_variants(self) -> None:
        for code in ["I50", "I50.1", "I50.9"]:
            tier, override, source = _determine_risk_tier([code])
            assert tier == "high", f"Expected high for {code}"
            assert source == "system"

    def test_stroke_i63_returns_high(self) -> None:
        tier, override, source = _determine_risk_tier(["I63.0"])
        assert tier == "high"
        assert override == "Stroke history"
        assert source == "system"

    def test_stroke_i64_returns_high(self) -> None:
        tier, override, source = _determine_risk_tier(["I64"])
        assert tier == "high"
        assert override == "Stroke history"
        assert source == "system"

    def test_tia_returns_high(self) -> None:
        tier, override, source = _determine_risk_tier(["G45.9"])
        assert tier == "high"
        assert override == "TIA history"
        assert source == "system"

    def test_haemorrhagic_stroke_i61_returns_high(self) -> None:
        tier, override, source = _determine_risk_tier(["I61.0"])
        assert tier == "high"
        assert override == "Haemorrhagic stroke history"
        assert source == "system"

    def test_i61_variants(self) -> None:
        for code in ["I61", "I61.0", "I61.9"]:
            tier, override, source = _determine_risk_tier([code])
            assert tier == "high", f"Expected high for {code}"
            assert source == "system"

    def test_i61_before_i63_in_priority(self) -> None:
        # I61 should match before I63 — both high but different override text
        tier, override, source = _determine_risk_tier(["I61.0", "I63.0"])
        assert override == "Haemorrhagic stroke history"

    def test_no_override_returns_medium(self) -> None:
        tier, override, source = _determine_risk_tier(["I10", "E11.9", "Z00.00"])
        assert tier == "medium"
        assert override is None
        assert source is None

    def test_empty_list_returns_medium(self) -> None:
        tier, override, source = _determine_risk_tier([])
        assert tier == "medium"
        assert override is None
        assert source is None

    def test_first_match_wins(self) -> None:
        # CHF before stroke — CHF override should win
        tier, override, source = _determine_risk_tier(["I50.0", "I63.0"])
        assert override == "CHF in problem list"


# ---------------------------------------------------------------------------
# _extract_obs_components — pure function tests
# ---------------------------------------------------------------------------


class TestExtractObsComponents:
    def test_valid_observation_returns_values(self) -> None:
        obs = _observation_resource(185, 72)
        systolic, diastolic = _extract_obs_components(obs)
        assert systolic == 185
        assert diastolic == 72

    def test_missing_components_returns_none(self) -> None:
        obs = {"resourceType": "Observation", "component": []}
        systolic, diastolic = _extract_obs_components(obs)
        assert systolic is None
        assert diastolic is None

    def test_missing_component_key_returns_none(self) -> None:
        obs = {"resourceType": "Observation"}
        systolic, diastolic = _extract_obs_components(obs)
        assert systolic is None
        assert diastolic is None

    def test_only_systolic_present(self) -> None:
        obs = {
            "resourceType": "Observation",
            "component": [
                {
                    "code": {"coding": [{"code": "8480-6"}]},
                    "valueQuantity": {"value": 140},
                }
            ],
        }
        systolic, diastolic = _extract_obs_components(obs)
        assert systolic == 140
        assert diastolic is None


# ---------------------------------------------------------------------------
# _parse_authored_on — pure function tests
# ---------------------------------------------------------------------------


class TestParseAuthoredOn:
    def test_iso_datetime_string(self) -> None:
        result = _parse_authored_on("2008-01-14T14:39:00")
        from datetime import date
        assert result == date(2008, 1, 14)

    def test_iso_date_string(self) -> None:
        result = _parse_authored_on("2020-06-15")
        from datetime import date
        assert result == date(2020, 6, 15)

    def test_none_input(self) -> None:
        assert _parse_authored_on(None) is None

    def test_empty_string(self) -> None:
        assert _parse_authored_on("") is None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_authored_on("not-a-date") is None


# ---------------------------------------------------------------------------
# _group_entries — pure function tests
# ---------------------------------------------------------------------------


class TestGroupEntries:
    def test_groups_by_resource_type(self, full_bundle: dict) -> None:
        groups = _group_entries(full_bundle)
        assert "Patient" in groups
        assert "Condition" in groups
        assert "Observation" in groups

    def test_empty_bundle_returns_empty_dict(self) -> None:
        assert _group_entries({"resourceType": "Bundle", "entry": []}) == {}

    def test_skips_non_dict_entries(self) -> None:
        bundle = {
            "entry": [
                "not a dict",
                {"resource": {"resourceType": "Patient", "id": "1"}},
            ]
        }
        groups = _group_entries(bundle)
        assert len(groups.get("Patient", [])) == 1


# ---------------------------------------------------------------------------
# ingest_fhir_bundle — async unit tests (mocked session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_patient_inserted(full_bundle: dict) -> None:
    session = _make_mock_session(patient_exists=False)
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert summary["patients_inserted"] == 1
    assert summary["patient_id"] == PATIENT_ID


@pytest.mark.asyncio
async def test_ingest_patient_already_exists_not_counted(full_bundle: dict) -> None:
    session = _make_mock_session(patient_exists=True)
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert summary["patients_inserted"] == 0


@pytest.mark.asyncio
async def test_ingest_clinical_context_upserted(full_bundle: dict) -> None:
    session = _make_mock_session()
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert summary["clinical_context_upserted"] == 1


@pytest.mark.asyncio
async def test_ingest_reading_inserted(full_bundle: dict) -> None:
    """One Observation in fixture → one Reading inserted via ON CONFLICT DO NOTHING.

    Per-observation inserts use session.execute() with pg_insert, not session.add().
    rowcount=1 from the mock means the row was inserted (no conflict).
    """
    session = _make_mock_session(reading_rowcount=1)
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert summary["readings_inserted"] == 1

    # Readings go via session.execute (ON CONFLICT path), not session.add.
    added = [c.args[0] for c in session.add.call_args_list]
    readings = [obj for obj in added if isinstance(obj, Reading)]
    assert len(readings) == 0  # readings now go via session.execute, not session.add

    # The fourth execute call (index 3) should be the reading INSERT statement.
    assert session.execute.call_count >= 4


@pytest.mark.asyncio
async def test_ingest_idempotent_skips_readings_when_conflict(full_bundle: dict) -> None:
    """ON CONFLICT DO NOTHING returns rowcount=0 when a reading already exists.

    The per-observation approach counts only actually-inserted rows via rowcount.
    """
    session = _make_mock_session(reading_rowcount=0)
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert summary["readings_inserted"] == 0

    added = [c.args[0] for c in session.add.call_args_list]
    readings = [obj for obj in added if isinstance(obj, Reading)]
    assert len(readings) == 0


@pytest.mark.asyncio
async def test_ingest_audit_event_written_on_success(full_bundle: dict) -> None:
    session = _make_mock_session()
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert summary["audit_events_inserted"] == 1

    added = [c.args[0] for c in session.add.call_args_list]
    audit_events = [obj for obj in added if isinstance(obj, AuditEvent)]
    assert len(audit_events) == 1
    assert audit_events[0].action == "bundle_import"
    assert audit_events[0].resource_type == "Bundle"
    assert audit_events[0].outcome == "success"
    assert audit_events[0].patient_id == PATIENT_ID
    assert audit_events[0].actor_type == "system"


@pytest.mark.asyncio
async def test_ingest_audit_event_written_on_failure() -> None:
    """Audit event with outcome='failure' is written even when ingestion raises."""
    bad_bundle = _make_bundle([])  # no Patient resource — will raise ValueError

    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    with pytest.raises(ValueError, match="no Patient resource"):
        await ingest_fhir_bundle(bad_bundle, session)

    added = [c.args[0] for c in session.add.call_args_list]
    audit_events = [obj for obj in added if isinstance(obj, AuditEvent)]
    assert len(audit_events) == 1
    assert audit_events[0].outcome == "failure"


@pytest.mark.asyncio
async def test_ingest_chf_risk_tier(chf_bundle: dict) -> None:
    """Patient with CHF condition should be inserted with high risk tier."""
    session = _make_mock_session(patient_exists=False)
    summary = await ingest_fhir_bundle(chf_bundle, session)
    assert summary["patients_inserted"] == 1

    # The INSERT Patient call is index 1 in execute call_args_list.
    # Inspect the compiled statement's bind parameters for risk_tier.
    insert_call = session.execute.call_args_list[1]
    stmt = insert_call.args[0]
    # Access the VALUES dict from the INSERT statement
    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    assert "high" in str(compiled)


@pytest.mark.asyncio
async def test_ingest_returns_full_summary_structure(full_bundle: dict) -> None:
    session = _make_mock_session()
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert set(summary.keys()) == {
        "patient_id",
        "patients_inserted",
        "clinical_context_upserted",
        "readings_inserted",
        "audit_events_inserted",
    }


@pytest.mark.asyncio
async def test_ingest_commit_called_twice(full_bundle: dict) -> None:
    """Session.commit() is called once for the main transaction and once for the audit."""
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)
    assert session.commit.call_count == 2


@pytest.mark.asyncio
async def test_ingest_med_history_stored(full_bundle: dict) -> None:
    """med_history from _aria_med_history is written into the ClinicalContext upsert."""
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)
    # The ClinicalContext upsert is execute call index 3 (0=SELECT Patient,
    # 1=INSERT Patient Step A, 2=UPDATE tier Step B, 3=UPSERT ClinicalContext, 4+=Readings).
    upsert_call = session.execute.call_args_list[3]
    stmt = upsert_call.args[0]
    # Compile without literal_binds (JSONB cannot be rendered as literal).
    # The column name "med_history" must appear in the compiled SQL and its
    # bound parameter value must match the list from _aria_med_history.
    compiled = stmt.compile()
    assert "med_history" in str(compiled)
    assert compiled.params.get("med_history") == full_bundle["_aria_med_history"]


# ---------------------------------------------------------------------------
# New ingestion tests — Phase 1 Fixes 6, 7, 8, 9, 12, 16
# ---------------------------------------------------------------------------


def _scalar_obs_resource(loinc_code: str, value: float, unit: str, effective_dt: str = "2020-06-15T09:00:00") -> dict:
    """Build a FHIR Observation with valueQuantity (pulse, weight, SpO2, temp)."""
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": loinc_code}]},
        "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit},
        "effectiveDateTime": effective_dt,
    }


def _allergy_resource_with_reaction(text: str, reaction: str) -> dict:
    """AllergyIntolerance resource with reaction/manifestation."""
    return {
        "resourceType": "AllergyIntolerance",
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
        "code": {"text": text},
        "reaction": [{"manifestation": [{"text": reaction}]}],
    }


def _make_extended_mock_session(reading_rowcount: int = 1, extra_results: int = 0) -> MagicMock:
    """Mock session with extra execute side effects for bundles with many obs."""
    existing_patient = MagicMock()
    existing_patient.scalar_one_or_none.return_value = None
    insert_patient_result = MagicMock()
    tier_update_result = MagicMock()
    upsert_cc_result = MagicMock()
    reading_insert_result = MagicMock()
    reading_insert_result.rowcount = reading_rowcount

    side_effects = [
        existing_patient,
        insert_patient_result,
        tier_update_result,
        upsert_cc_result,
        reading_insert_result,
    ] + [MagicMock() for _ in range(extra_results)]

    session = MagicMock()
    session.execute = AsyncMock(side_effect=side_effects)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# --- _get_obs_loinc / _get_obs_scalar_value helpers ---


def test_get_obs_loinc_extracts_code() -> None:
    obs = {"code": {"coding": [{"code": "8867-4"}]}}
    assert _get_obs_loinc(obs) == "8867-4"


def test_get_obs_loinc_missing_returns_empty() -> None:
    assert _get_obs_loinc({}) == ""


def test_get_obs_scalar_value_extracts_float() -> None:
    obs = {"valueQuantity": {"value": 63.0}}
    assert _get_obs_scalar_value(obs) == 63.0


def test_get_obs_scalar_value_none_when_absent() -> None:
    assert _get_obs_scalar_value({}) is None


# --- Fix 12: last_visit_date from _aria_visit_dates ---


@pytest.mark.asyncio
async def test_last_visit_date_from_aria_visit_dates(full_bundle: dict) -> None:
    """last_visit_date must be max(_aria_visit_dates) when the key is present."""
    from datetime import date

    full_bundle["_aria_visit_dates"] = ["2020-06-15", "2021-03-01", "2019-12-31"]
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    assert compiled.params.get("last_visit_date") == date(2021, 3, 1)


@pytest.mark.asyncio
async def test_last_visit_date_fallback_to_obs_when_no_visit_dates(full_bundle: dict) -> None:
    """Without _aria_visit_dates, last_visit_date falls back to last BP observation."""
    from datetime import date

    full_bundle.pop("_aria_visit_dates", None)
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    assert compiled.params.get("last_visit_date") == date(2020, 6, 15)


# --- Fix 7: problem_assessments from _aria_problem_assessments ---


@pytest.mark.asyncio
async def test_problem_assessments_stored_in_cc_values(full_bundle: dict) -> None:
    """problem_assessments from _aria_problem_assessments is persisted to ClinicalContext."""
    assessments = [
        {"problem_code": "I10", "visit_date": "2020-06-15", "htn_flag": True,
         "status_text": "Under Evaluation", "assessment_text": "HTN high today.", "status_flag": 2}
    ]
    full_bundle["_aria_problem_assessments"] = assessments
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    assert "problem_assessments" in str(compiled)
    assert compiled.params.get("problem_assessments") == assessments


# --- Fix 8: social_context from _aria_social_context ---


@pytest.mark.asyncio
async def test_social_context_stored_in_cc_values(full_bundle: dict) -> None:
    """social_context from _aria_social_context is persisted to ClinicalContext."""
    full_bundle["_aria_social_context"] = "SMOKING: Never smoked"
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    assert compiled.params.get("social_context") == "SMOKING: Never smoked"


# --- Fix 9: allergy_reactions parallel array ---


@pytest.mark.asyncio
async def test_allergy_reactions_stored_parallel_to_allergies() -> None:
    """allergy_reactions list mirrors allergies list with reaction text or empty string."""
    bundle = _make_bundle([
        _patient_resource(),
        _observation_resource(145, 90, "2020-06-15T09:00:00"),
        _allergy_resource("SULFA"),                        # no reaction
        _allergy_resource_with_reaction("PENICILLIN", "Anaphylaxis"),
    ])
    bundle["_aria_med_history"] = []
    session = _make_mock_session()
    await ingest_fhir_bundle(bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    reactions = compiled.params.get("allergy_reactions")
    assert reactions is not None
    assert len(reactions) == 2
    # Order matches the allergy resources order in the bundle
    assert "" in reactions          # SULFA has no reaction
    assert "Anaphylaxis" in reactions


# --- Fix 6: last_clinic_pulse, last_clinic_spo2, historic_spo2 ---


@pytest.mark.asyncio
async def test_last_clinic_pulse_stored() -> None:
    """last_clinic_pulse is populated from LOINC 8867-4 Observation."""
    pulse_obs = _scalar_obs_resource("8867-4", 68.0, "/min")
    bundle = _make_bundle([
        _patient_resource(),
        _observation_resource(145, 90, "2020-06-15T09:00:00"),
        pulse_obs,
    ])
    bundle["_aria_med_history"] = []
    session = _make_mock_session()
    await ingest_fhir_bundle(bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    assert compiled.params.get("last_clinic_pulse") == 68


@pytest.mark.asyncio
async def test_last_clinic_spo2_stored_and_historic_populated() -> None:
    """last_clinic_spo2 and historic_spo2 are populated from LOINC 59408-5 Observations."""
    spo2_obs_1 = _scalar_obs_resource("59408-5", 93.0, "%", "2020-01-01T09:00:00")
    spo2_obs_2 = _scalar_obs_resource("59408-5", 91.0, "%", "2020-06-15T09:00:00")
    bundle = _make_bundle([
        _patient_resource(),
        _observation_resource(145, 90, "2020-06-15T09:00:00"),
        spo2_obs_1,
        spo2_obs_2,
    ])
    bundle["_aria_med_history"] = []
    session = _make_mock_session()
    await ingest_fhir_bundle(bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    # last_clinic_spo2 is the last (most recent) SpO2 value
    assert compiled.params.get("last_clinic_spo2") == 91.0
    historic = compiled.params.get("historic_spo2")
    assert historic is not None
    assert len(historic) == 2
    assert 93.0 in historic
    assert 91.0 in historic


# --- Fix 6: non-BP observations must NOT become readings rows ---


@pytest.mark.asyncio
async def test_non_bp_observations_not_inserted_as_readings() -> None:
    """Pulse/SpO2/Weight observations must NOT be inserted into the readings table."""
    pulse_obs = _scalar_obs_resource("8867-4", 68.0, "/min")
    spo2_obs = _scalar_obs_resource("59408-5", 93.0, "%")
    # Only 1 BP observation — readings_inserted must be 1, not 3
    bundle = _make_bundle([
        _patient_resource(),
        _observation_resource(145, 90, "2020-06-15T09:00:00"),  # BP panel
        pulse_obs,
        spo2_obs,
    ])
    bundle["_aria_med_history"] = []

    # Provide extra side effects so the mock doesn't raise StopIteration if
    # ingestion accidentally tries to insert non-BP obs as readings
    session = _make_extended_mock_session(reading_rowcount=1, extra_results=2)
    summary = await ingest_fhir_bundle(bundle, session)

    assert summary["readings_inserted"] == 1
    # Calls: 0=SELECT, 1=INSERT Patient (Step A), 2=UPDATE tier (Step B),
    #        3=UPSERT CC, 4=INSERT 1 BP reading
    # If pulse/spo2 were mistakenly inserted, call_count would be 7+ (plus audit commit)
    assert session.execute.call_count == 5  # exactly 5 execute calls


# --- Fix 16: recent_labs skeleton ---


@pytest.mark.asyncio
async def test_recent_labs_none_when_no_aria_recent_labs(full_bundle: dict) -> None:
    """Without _aria_recent_labs key, recent_labs must be None in cc_values."""
    full_bundle.pop("_aria_recent_labs", None)
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    assert compiled.params.get("recent_labs") is None


@pytest.mark.asyncio
async def test_recent_labs_stored_when_present(full_bundle: dict) -> None:
    """When _aria_recent_labs is present, recent_labs is built from it."""
    full_bundle["_aria_recent_labs"] = [
        {"loinc_code": "2160-0", "value": 1.1, "unit": "mg/dL", "date": "2020-05-01"},
    ]
    session = _make_mock_session()
    await ingest_fhir_bundle(full_bundle, session)

    upsert_call = session.execute.call_args_list[3]
    compiled = upsert_call.args[0].compile()
    labs = compiled.params.get("recent_labs")
    assert labs is not None
    assert "2160-0" in labs
    assert labs["2160-0"]["value"] == 1.1


# ---------------------------------------------------------------------------
# Integration test — requires live Supabase DB
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_real_bundle() -> None:
    """End-to-end: ingest 1091_bundle.json into the real database.

    Idempotent: safe to run multiple times.
    """
    import os

    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(project_root / "backend" / ".env")

    bundle_path = project_root / "data" / "fhir" / "bundles" / "1091_bundle.json"
    if not bundle_path.exists():
        pytest.skip("1091_bundle.json not found — run scripts/run_adapter.py first")

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    errors = validate_fhir_bundle(bundle)
    assert errors == [], f"Bundle validation failed: {errors}"

    from app.db.base import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        summary = await ingest_fhir_bundle(bundle, session)

    assert summary["patient_id"] == "1091"
    assert summary["patients_inserted"] in (0, 1)  # idempotent
    assert summary["clinical_context_upserted"] == 1
    assert summary["readings_inserted"] in range(0, 66)  # 65 observations max; 0 on re-run
    assert summary["audit_events_inserted"] == 1
