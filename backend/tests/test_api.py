"""Unit tests for all ARIA API routes.

Uses httpx.AsyncClient with FastAPI's test transport — no live DB required.
Each test mocks get_session to inject a controlled AsyncSession.

Run:
    cd backend && python -m pytest tests/test_api.py -v -m "not integration"
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.session import get_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_session() -> AsyncMock:
    """Return a fresh AsyncMock that behaves like an AsyncSession."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()  # session.add() is sync — avoid coroutine warning
    return session


def _scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_result(*items: object) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = list(items)
    result.scalars.return_value = scalars
    return result


def _all_result(*rows: object) -> MagicMock:
    result = MagicMock()
    result.all.return_value = list(rows)
    return result


def _make_patient(
    patient_id: str = "1091",
    risk_tier: str = "high",
    risk_score: float = 74.5,
    monitoring_active: bool = True,
) -> MagicMock:
    p = MagicMock()
    p.patient_id = patient_id
    p.gender = "M"
    p.age = 67
    p.risk_tier = risk_tier
    p.tier_override = "CHF in problem list"
    p.risk_score = risk_score
    p.monitoring_active = monitoring_active
    p.next_appointment = datetime(2026, 4, 20, 9, 30, tzinfo=UTC)
    p.enrolled_at = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)
    p.enrolled_by = "dr.mehta"
    return p


def _make_briefing() -> MagicMock:
    b = MagicMock()
    b.briefing_id = "b001"
    b.patient_id = "1091"
    b.appointment_date = date(2026, 4, 20)
    b.llm_response = {
        "trend_summary": "Sustained elevated readings averaging 163 mmHg.",
        "medication_status": "Metoprolol, Lisinopril, Lasix. No change in 186 days.",
        "adherence_summary": "91% adherence. Treatment review warranted.",
        "active_problems": ["CHF", "Hypertension"],
        "overdue_labs": ["HbA1c"],
        "visit_agenda": ["Review BP trend", "Address overdue labs"],
        "urgent_flags": ["Therapeutic inertia: no change in 186 days"],
        "risk_score": 74.5,
        "data_limitations": "Synthetic data for demo.",
    }
    b.generated_at = datetime(2026, 4, 20, 7, 30, tzinfo=UTC)
    b.delivered_at = datetime(2026, 4, 20, 7, 30, 5, tzinfo=UTC)
    b.read_at = None
    return b


def _make_alert(alert_id: str = "a001", alert_type: str = "inertia") -> MagicMock:
    a = MagicMock()
    a.alert_id = alert_id
    a.patient_id = "1091"
    a.alert_type = alert_type
    a.gap_days = None
    a.systolic_avg = 163.2
    a.triggered_at = datetime(2026, 4, 17, 7, 30, tzinfo=UTC)
    a.delivered_at = datetime(2026, 4, 17, 7, 30, 5, tzinfo=UTC)
    a.acknowledged_at = None
    return a


def _make_reading() -> MagicMock:
    r = MagicMock()
    r.reading_id = "r001"
    r.patient_id = "1091"
    r.systolic_1 = 164
    r.diastolic_1 = 100
    r.heart_rate_1 = 71
    r.systolic_2 = 161
    r.diastolic_2 = 97
    r.heart_rate_2 = 72
    r.systolic_avg = 162.5
    r.diastolic_avg = 98.5
    r.heart_rate_avg = 71.5
    r.effective_datetime = datetime(2026, 3, 21, 7, 15, tzinfo=UTC)
    r.session = "morning"
    r.source = "generated"
    r.submitted_by = "generator"
    r.bp_position = None
    r.bp_site = None
    r.medication_taken = None
    r.consent_version = "1.0"
    r.created_at = datetime(2026, 3, 21, 7, 15, tzinfo=UTC)
    return r


# ---------------------------------------------------------------------------
# Client fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /api/patients
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_patients_returns_sorted(client: AsyncClient):
    high = _make_patient("1091", "high", 74.5)
    medium = _make_patient("1092", "medium", 41.2)
    low = _make_patient("1093", "low", 18.7)

    session = _mock_session()
    session.execute.return_value = _scalars_result(medium, low, high)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/patients")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    # high risk must be first
    assert data[0]["risk_tier"] == "high"
    assert data[1]["risk_tier"] == "medium"
    assert data[2]["risk_tier"] == "low"


@pytest.mark.asyncio
async def test_list_patients_empty(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalars_result()

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/patients")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/patients/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_patient_found(client: AsyncClient):
    patient = _make_patient()
    session = _mock_session()
    session.execute.return_value = _scalar_result(patient)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/patients/1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["patient_id"] == "1091"
    assert data["risk_tier"] == "high"
    assert data["risk_score"] == 74.5


@pytest.mark.asyncio
async def test_get_patient_not_found(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalar_result(None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/patients/9999")
    app.dependency_overrides.clear()

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/briefings/{patient_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_briefing_success(client: AsyncClient):
    patient = _make_patient()
    briefing = _make_briefing()
    session = _mock_session()
    session.execute.side_effect = [
        _scalar_result(patient),   # patient exists check
        _scalar_result(briefing),  # fetch briefing
        MagicMock(),               # update read_at
    ]

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/briefings/1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["patient_id"] == "1091"
    assert data["briefing_id"] == "b001"
    assert "trend_summary" in data["llm_response"]
    assert data["read_at"] is not None


@pytest.mark.asyncio
async def test_get_briefing_patient_not_found(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalar_result(None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/briefings/9999")
    app.dependency_overrides.clear()

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_briefing_no_briefing_yet(client: AsyncClient):
    patient = _make_patient()
    session = _mock_session()
    session.execute.side_effect = [
        _scalar_result(patient),  # patient exists
        _scalar_result(None),     # no briefing
    ]

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/briefings/1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/alerts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_alerts(client: AsyncClient):
    alert = _make_alert()
    session = _mock_session()
    session.execute.return_value = _scalars_result(alert)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/alerts")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["alert_id"] == "a001"
    assert data[0]["alert_type"] == "inertia"
    assert data[0]["acknowledged_at"] is None


@pytest.mark.asyncio
async def test_list_alerts_empty(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalars_result()

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/alerts")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/alerts/{id}/acknowledge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acknowledge_alert_success(client: AsyncClient):
    alert = _make_alert()
    session = _mock_session()
    session.execute.side_effect = [
        _scalar_result(alert),  # fetch alert
        MagicMock(),            # update acknowledged_at
    ]

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post("/api/alerts/a001/acknowledge")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "acknowledged"
    assert "acknowledged_at" in data


@pytest.mark.asyncio
async def test_acknowledge_alert_not_found(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalar_result(None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post("/api/alerts/bad-id/acknowledge")
    app.dependency_overrides.clear()

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_acknowledge_alert_already_acknowledged(client: AsyncClient):
    alert = _make_alert()
    alert.acknowledged_at = datetime(2026, 4, 17, 8, 0, tzinfo=UTC)
    session = _mock_session()
    session.execute.return_value = _scalar_result(alert)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post("/api/alerts/a001/acknowledge")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "already_acknowledged"


# ---------------------------------------------------------------------------
# Phase 5 — alerts.py extensions (Fix 24, Fix 42 L1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_alerts_filtered_by_patient_id(client: AsyncClient):
    """Fix 24 — ?patient_id= adds Alert.patient_id filter."""
    alert = _make_alert()
    session = _mock_session()
    session.execute.return_value = _scalars_result(alert)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/alerts", params={"patient_id": "1091"})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["patient_id"] == "1091"


@pytest.mark.asyncio
async def test_acknowledge_alert_with_disposition_writes_feedback(client: AsyncClient):
    """Fix 42 L1 — disposition payload writes AlertFeedback row + audit event."""
    from app.models.alert_feedback import AlertFeedback

    alert = _make_alert()
    session = _mock_session()
    session.execute.side_effect = [
        _scalar_result(alert),  # fetch alert
        MagicMock(),            # update acknowledged_at
    ]
    added: list = []
    session.add = added.append

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/alerts/a001/acknowledge",
        json={
            "disposition": "agree_acting",
            "reason_text": "Adjusting medication today",
            "clinician_id": "dr_smith",
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "acknowledged"
    assert data["feedback_recorded"] is True

    feedback_rows = [a for a in added if isinstance(a, AlertFeedback)]
    assert len(feedback_rows) == 1
    fb = feedback_rows[0]
    assert fb.alert_id == "a001"
    assert fb.patient_id == "1091"
    assert fb.detector_type == "inertia"
    assert fb.disposition == "agree_acting"
    assert fb.reason_text == "Adjusting medication today"
    assert fb.clinician_id == "dr_smith"


@pytest.mark.asyncio
async def test_acknowledge_alert_without_disposition_no_feedback(client: AsyncClient):
    """No payload → backwards-compatible behaviour, no AlertFeedback row written."""
    from app.models.alert_feedback import AlertFeedback

    alert = _make_alert()
    session = _mock_session()
    session.execute.side_effect = [
        _scalar_result(alert),
        MagicMock(),
    ]
    added: list = []
    session.add = added.append

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post("/api/alerts/a001/acknowledge")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["feedback_recorded"] is False
    assert not any(isinstance(a, AlertFeedback) for a in added)


@pytest.mark.asyncio
async def test_acknowledge_alert_invalid_disposition_rejected(client: AsyncClient):
    """Pydantic Literal validation rejects unknown disposition values."""
    session = _mock_session()
    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/alerts/a001/acknowledge",
        json={"disposition": "ignored"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422  # Pydantic validation failure


# ---------------------------------------------------------------------------
# GET /api/readings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_readings(client: AsyncClient):
    reading = _make_reading()
    session = _mock_session()
    session.execute.return_value = _scalars_result(reading)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/readings?patient_id=1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["patient_id"] == "1091"
    assert data[0]["systolic_avg"] == 162.5


@pytest.mark.asyncio
async def test_list_readings_missing_patient_id(client: AsyncClient):
    resp = await client.get("/api/readings")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/readings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_reading_success(client: AsyncClient):
    patient = _make_patient()
    reading = _make_reading()
    reading.reading_id = "new-uuid"

    session = _mock_session()
    session.execute.return_value = _scalar_result(patient)
    session.refresh = AsyncMock()

    # After add+commit, refresh fills in the reading
    async def fake_refresh(obj: object) -> None:
        obj.reading_id = "new-uuid"  # type: ignore[attr-defined]

    session.refresh.side_effect = fake_refresh

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post("/api/readings", json={
        "patient_id": "1091",
        "systolic_1": 162,
        "diastolic_1": 99,
        "heart_rate_1": 71,
        "effective_datetime": "2026-04-20T07:15:00Z",
        "session": "morning",
    })
    app.dependency_overrides.clear()

    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_reading_patient_not_found(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalar_result(None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post("/api/readings", json={
        "patient_id": "9999",
        "systolic_1": 162,
        "diastolic_1": 99,
        "effective_datetime": "2026-04-20T07:15:00Z",
        "session": "morning",
    })
    app.dependency_overrides.clear()

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/adherence/{patient_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_adherence_success(client: AsyncClient):
    patient = _make_patient()

    row = MagicMock()
    row.medication_name = "Metoprolol Succinate 50mg"
    row.rxnorm_code = "866514"
    row.total_doses = 56
    row.confirmed_doses = 52

    session = _mock_session()
    session.execute.side_effect = [
        _scalar_result(patient),
        _all_result(row),
    ]

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/adherence/1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["medication_name"] == "Metoprolol Succinate 50mg"
    assert data[0]["adherence_pct"] == round(52 / 56 * 100, 1)
    assert data[0]["total_doses"] == 56
    assert data[0]["confirmed_doses"] == 52


@pytest.mark.asyncio
async def test_get_adherence_patient_not_found(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalar_result(None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/adherence/9999")
    app.dependency_overrides.clear()

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_adherence_no_data(client: AsyncClient):
    patient = _make_patient()
    session = _mock_session()
    session.execute.side_effect = [
        _scalar_result(patient),
        _all_result(),
    ]

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/adherence/1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/admin/trigger-scheduler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_scheduler_demo_mode(client: AsyncClient):
    with patch(
        "app.api.admin.enqueue_briefing_jobs",
        new=AsyncMock(return_value=2),
    ), patch(
        "app.api.admin.settings"
    ) as mock_settings:
        mock_settings.demo_mode = True
        resp = await client.post("/api/admin/trigger-scheduler")

    assert resp.status_code == 200
    assert resp.json()["enqueued"] == 2


@pytest.mark.asyncio
async def test_trigger_scheduler_blocked_when_not_demo(client: AsyncClient):
    with patch("app.api.admin.settings") as mock_settings:
        mock_settings.demo_mode = False
        resp = await client.post("/api/admin/trigger-scheduler")

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/ingest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_invalid_bundle(client: AsyncClient):
    resp = await client.post("/api/ingest", json={"resourceType": "Bundle"})
    # Missing Patient resource — validator should catch this
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_valid_bundle(client: AsyncClient):
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "TEST001"}},
        ],
    }
    session = _mock_session()
    with patch(
        "app.api.ingest.ingest_fhir_bundle",
        new=AsyncMock(return_value={"patient_id": "TEST001", "readings_inserted": 0}),
    ):
        app.dependency_overrides[get_session] = lambda: session
        resp = await client.post("/api/ingest", json=bundle)
        app.dependency_overrides.clear()

    assert resp.status_code == 201
