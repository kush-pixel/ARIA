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
    return {
        "resourceType": "Observation",
        "status": "final",
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
    clinic_reading_count: int = 0,
) -> MagicMock:
    """Build a mock AsyncSession with pre-configured execute side effects.

    Call order that ingestion.py follows:
      0. SELECT Patient (existence check)
      1. INSERT Patient ON CONFLICT DO NOTHING
      2. INSERT/UPSERT ClinicalContext ON CONFLICT DO UPDATE
      3. SELECT COUNT(*) FROM readings WHERE source='clinic'
    """
    # Result for SELECT Patient
    existing_patient = MagicMock()
    existing_patient.scalar_one_or_none.return_value = (
        MagicMock() if patient_exists else None
    )

    # Result for INSERT Patient
    insert_patient_result = MagicMock()

    # Result for UPSERT ClinicalContext
    upsert_cc_result = MagicMock()

    # Result for SELECT COUNT(*) readings
    count_result = MagicMock()
    count_result.scalar.return_value = clinic_reading_count

    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            existing_patient,
            insert_patient_result,
            upsert_cc_result,
            count_result,
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
        tier, override = _determine_risk_tier(["I50.0"])
        assert tier == "high"
        assert override == "CHF in problem list"

    def test_chf_code_prefix_i50_variants(self) -> None:
        for code in ["I50", "I50.1", "I50.9"]:
            tier, override = _determine_risk_tier([code])
            assert tier == "high", f"Expected high for {code}"

    def test_stroke_i63_returns_high(self) -> None:
        tier, override = _determine_risk_tier(["I63.0"])
        assert tier == "high"
        assert override == "Stroke history"

    def test_stroke_i64_returns_high(self) -> None:
        tier, override = _determine_risk_tier(["I64"])
        assert tier == "high"
        assert override == "Stroke history"

    def test_tia_returns_high(self) -> None:
        tier, override = _determine_risk_tier(["G45.9"])
        assert tier == "high"
        assert override == "TIA history"

    def test_no_override_returns_medium(self) -> None:
        tier, override = _determine_risk_tier(["I10", "E11.9", "Z00.00"])
        assert tier == "medium"
        assert override is None

    def test_empty_list_returns_medium(self) -> None:
        tier, override = _determine_risk_tier([])
        assert tier == "medium"
        assert override is None

    def test_first_match_wins(self) -> None:
        # CHF before stroke — CHF override should win
        tier, override = _determine_risk_tier(["I50.0", "I63.0"])
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
    """One Observation in fixture → one Reading added to session."""
    session = _make_mock_session(clinic_reading_count=0)
    summary = await ingest_fhir_bundle(full_bundle, session)
    assert summary["readings_inserted"] == 1

    added = [c.args[0] for c in session.add.call_args_list]
    readings = [obj for obj in added if isinstance(obj, Reading)]
    assert len(readings) == 1
    assert readings[0].systolic_1 == 185
    assert readings[0].diastolic_1 == 72
    assert readings[0].source == "clinic"
    assert readings[0].session == "ad_hoc"
    assert readings[0].submitted_by == "clinic"


@pytest.mark.asyncio
async def test_ingest_idempotent_skips_readings_when_exist(full_bundle: dict) -> None:
    """When clinic readings already exist, no new readings are inserted."""
    session = _make_mock_session(clinic_reading_count=3)
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
    # The ClinicalContext upsert is execute call index 2 (0=SELECT Patient,
    # 1=INSERT Patient, 2=UPSERT ClinicalContext, 3=SELECT COUNT readings).
    upsert_call = session.execute.call_args_list[2]
    stmt = upsert_call.args[0]
    # Compile without literal_binds (JSONB cannot be rendered as literal).
    # The column name "med_history" must appear in the compiled SQL and its
    # bound parameter value must match the list from _aria_med_history.
    compiled = stmt.compile()
    assert "med_history" in str(compiled)
    assert compiled.params.get("med_history") == full_bundle["_aria_med_history"]


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
