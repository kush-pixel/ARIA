"""Tests for Phase 5 and Phase 7 features.

Covers:
  Phase 5 / Fix 41  — Gap explanation API (GET, POST, DELETE)
  Phase 7 / Fix 44  — BLE webhook (insert + idempotency)
  Phase 7 / Fix 45  — Off-hours tagging on _upsert_alert
  Phase 7 / Fix 42 L2 — Calibration engine + admin API
  Phase 7 / Fix 42 L3 — Outcome verification tracking + admin API
  Phase 7 / Escalation sweep — _run_escalation_sweep marks escalated=True
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.session import get_session


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.flush = AsyncMock(return_value=None)
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


def _make_gap_explanation(
    explanation_id: str = "exp-001",
    patient_id: str = "1091",
    gap_start: date = date(2026, 3, 1),
    gap_end: date = date(2026, 3, 5),
    reason: str = "device_issue",
) -> MagicMock:
    e = MagicMock()
    e.explanation_id = explanation_id
    e.patient_id = patient_id
    e.gap_start = gap_start
    e.gap_end = gap_end
    e.reason = reason
    e.notes = None
    e.reported_by = "clinician"
    e.reporter_id = None
    e.created_at = datetime(2026, 3, 10, 8, 0, tzinfo=UTC)
    return e


def _make_verification(
    verification_id: str = "v001",
    patient_id: str = "1091",
    outcome_type: str = "pending",
    prompted_at: datetime | None = None,
    clinician_response: str | None = None,
) -> MagicMock:
    v = MagicMock()
    v.verification_id = verification_id
    v.feedback_id = "fb001"
    v.alert_id = "a001"
    v.patient_id = patient_id
    v.dismissed_at = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
    v.check_after = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)
    v.outcome_type = outcome_type
    v.prompted_at = prompted_at
    v.clinician_response = clinician_response
    v.responded_at = None
    return v


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ===========================================================================
# Phase 5 / Fix 41 — Gap Explanation API
# ===========================================================================


@pytest.mark.asyncio
async def test_list_gap_explanations_returns_list(client: AsyncClient):
    explanation = _make_gap_explanation()
    session = _mock_session()
    session.execute.return_value = _scalars_result(explanation)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/gap-explanations?patient_id=1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["explanation_id"] == "exp-001"
    assert data[0]["reason"] == "device_issue"
    assert data[0]["patient_id"] == "1091"


@pytest.mark.asyncio
async def test_list_gap_explanations_empty(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalars_result()

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/gap-explanations?patient_id=9999")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_gap_explanation_success(client: AsyncClient):
    session = _mock_session()
    added: list = []
    session.add = added.append

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/gap-explanations",
        json={
            "patient_id": "1091",
            "gap_start": "2026-03-01",
            "gap_end": "2026-03-05",
            "reason": "travel",
            "notes": "Patient was abroad",
            "reported_by": "clinician",
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 201
    from app.models.gap_explanation import GapExplanation

    rows = [a for a in added if isinstance(a, GapExplanation)]
    assert len(rows) == 1
    assert rows[0].reason == "travel"
    assert rows[0].patient_id == "1091"


@pytest.mark.asyncio
async def test_create_gap_explanation_invalid_reason_rejected(client: AsyncClient):
    session = _mock_session()

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/gap-explanations",
        json={
            "patient_id": "1091",
            "gap_start": "2026-03-01",
            "gap_end": "2026-03-05",
            "reason": "forgot",
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_gap_explanation_end_before_start_rejected(client: AsyncClient):
    session = _mock_session()

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/gap-explanations",
        json={
            "patient_id": "1091",
            "gap_start": "2026-03-05",
            "gap_end": "2026-03-01",
            "reason": "illness",
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_gap_explanation_success(client: AsyncClient):
    explanation = _make_gap_explanation()
    session = _mock_session()
    session.execute.return_value = _scalar_result(explanation)
    session.delete = AsyncMock(return_value=None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.delete("/api/gap-explanations/exp-001")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert resp.json()["explanation_id"] == "exp-001"


@pytest.mark.asyncio
async def test_delete_gap_explanation_not_found(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalar_result(None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.delete("/api/gap-explanations/nonexistent")
    app.dependency_overrides.clear()

    assert resp.status_code == 404


# ===========================================================================
# Phase 7 / Fix 44 — BLE Webhook
# ===========================================================================


@pytest.mark.asyncio
async def test_ble_webhook_insert_new_reading(client: AsyncClient):
    from app.models.audit_event import AuditEvent

    session = _mock_session()
    execute_result = MagicMock()
    execute_result.rowcount = 1
    session.execute.return_value = execute_result
    added: list = []
    session.add = added.append

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/ble-webhook",
        json={
            "patient_id": "1091",
            "systolic": 158,
            "diastolic": 96,
            "heart_rate": 72,
            "measured_at": "2026-04-10T08:30:00Z",
            "session": "morning",
            "device_serial": "OMRON-001",
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 201
    data = resp.json()
    assert data["inserted"] is True
    assert data["patient_id"] == "1091"

    audit_rows = [a for a in added if isinstance(a, AuditEvent)]
    assert len(audit_rows) == 1
    assert audit_rows[0].action == "reading_ingested"
    assert "ble_auto" in audit_rows[0].details


@pytest.mark.asyncio
async def test_ble_webhook_duplicate_not_inserted(client: AsyncClient):
    session = _mock_session()
    execute_result = MagicMock()
    execute_result.rowcount = 0  # ON CONFLICT DO NOTHING — no row inserted
    session.execute.return_value = execute_result
    added: list = []
    session.add = added.append

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/ble-webhook",
        json={
            "patient_id": "1091",
            "systolic": 158,
            "diastolic": 96,
            "measured_at": "2026-04-10T08:30:00Z",
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["inserted"] is False
    from app.models.audit_event import AuditEvent

    assert not any(isinstance(a, AuditEvent) for a in added)


@pytest.mark.asyncio
async def test_ble_webhook_out_of_range_systolic_rejected(client: AsyncClient):
    session = _mock_session()
    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/ble-webhook",
        json={
            "patient_id": "1091",
            "systolic": 300,  # > 250
            "diastolic": 96,
            "measured_at": "2026-04-10T08:30:00Z",
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ble_webhook_invalid_session_rejected(client: AsyncClient):
    session = _mock_session()
    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/ble-webhook",
        json={
            "patient_id": "1091",
            "systolic": 158,
            "diastolic": 96,
            "measured_at": "2026-04-10T08:30:00Z",
            "session": "afternoon",  # not in (morning|evening|ad_hoc)
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422


# ===========================================================================
# Phase 7 / Fix 45 — Off-hours tagging
# ===========================================================================


def test_is_off_hours_evening():
    from app.utils.datetime_utils import is_off_hours as _is_off_hours

    dt = datetime(2026, 4, 14, 19, 0, tzinfo=UTC)  # Tuesday 7 PM UTC
    assert _is_off_hours(dt) is True


def test_is_off_hours_early_morning():
    from app.utils.datetime_utils import is_off_hours as _is_off_hours

    dt = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)  # Tuesday 6 AM UTC
    assert _is_off_hours(dt) is True


def test_is_not_off_hours_midday():
    from app.utils.datetime_utils import is_off_hours as _is_off_hours

    dt = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)  # Tuesday noon UTC
    assert _is_off_hours(dt) is False


def test_is_off_hours_saturday():
    from app.utils.datetime_utils import is_off_hours as _is_off_hours

    dt = datetime(2026, 4, 11, 14, 0, tzinfo=UTC)  # Saturday 2 PM (weekday=5)
    assert _is_off_hours(dt) is True


def test_is_off_hours_sunday():
    from app.utils.datetime_utils import is_off_hours as _is_off_hours

    dt = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)  # Sunday 10 AM (weekday=6)
    assert _is_off_hours(dt) is True


@pytest.mark.asyncio
async def test_upsert_alert_sets_off_hours_flag():
    from app.models.alert import Alert
    from app.services.worker.processor import _upsert_alert

    session = AsyncMock()
    session.add = MagicMock()
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = None  # no existing alert
    session.execute.return_value = existing

    # Patch datetime.now to return an off-hours time (7 PM UTC Tuesday)
    off_hours_dt = datetime(2026, 4, 14, 19, 0, tzinfo=UTC)
    with patch("app.services.worker.processor.datetime") as mock_dt:
        mock_dt.now.return_value = off_hours_dt
        mock_dt.now.side_effect = None
        mock_dt.side_effect = None
        await _upsert_alert(session, "1091", "gap_urgent", gap_days=5)

    added = [c.args[0] for c in session.add.call_args_list]
    assert len(added) == 1
    alert = added[0]
    assert isinstance(alert, Alert)
    assert alert.off_hours is True


@pytest.mark.asyncio
async def test_upsert_alert_not_off_hours_during_business_hours():
    from app.models.alert import Alert
    from app.services.worker.processor import _upsert_alert

    session = AsyncMock()
    session.add = MagicMock()
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = None
    session.execute.return_value = existing

    business_hours_dt = datetime(2026, 4, 14, 10, 0, tzinfo=UTC)  # Tuesday 10 AM
    with patch("app.services.worker.processor.datetime") as mock_dt:
        mock_dt.now.return_value = business_hours_dt
        mock_dt.now.side_effect = None
        mock_dt.side_effect = None
        await _upsert_alert(session, "1091", "inertia")

    added = [c.args[0] for c in session.add.call_args_list]
    assert len(added) == 1
    assert added[0].off_hours is False


# ===========================================================================
# Phase 7 / Fix 42 L2 — Calibration engine unit tests
# ===========================================================================


@pytest.mark.asyncio
async def test_get_calibration_recommendations_returns_pairs_above_threshold():
    from app.services.feedback.calibration_engine import get_calibration_recommendations

    session = AsyncMock()

    row = MagicMock()
    row.patient_id = "1091"
    row.detector_type = "gap"
    row.dismissal_count = 5

    rows_result = MagicMock()
    rows_result.all.return_value = [row]

    active_result = MagicMock()
    active_result.all.return_value = []

    session.execute.side_effect = [rows_result, active_result]

    recs = await get_calibration_recommendations(session)

    assert len(recs) == 1
    assert recs[0]["patient_id"] == "1091"
    assert recs[0]["detector_type"] == "gap"
    assert recs[0]["dismissal_count"] == 5
    assert recs[0]["threshold"] == 4


@pytest.mark.asyncio
async def test_get_calibration_recommendations_excludes_active_rules():
    from app.services.feedback.calibration_engine import get_calibration_recommendations

    session = AsyncMock()

    row = MagicMock()
    row.patient_id = "1091"
    row.detector_type = "gap"
    row.dismissal_count = 6

    rows_result = MagicMock()
    rows_result.all.return_value = [row]

    active_row = MagicMock()
    active_row.patient_id = "1091"
    active_row.detector_type = "gap"
    active_result = MagicMock()
    active_result.all.return_value = [active_row]

    session.execute.side_effect = [rows_result, active_result]

    recs = await get_calibration_recommendations(session)

    assert recs == []


@pytest.mark.asyncio
async def test_approve_calibration_rule_creates_active_rule():
    from app.models.calibration_rule import CalibrationRule
    from app.services.feedback.calibration_engine import approve_calibration_rule

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock(return_value=None)

    rule = await approve_calibration_rule(
        session,
        patient_id="1091",
        detector_type="inertia",
        dismissal_count=5,
        approved_by="dr.mehta",
        notes="Patient is stable on this regimen",
    )

    added = [c.args[0] for c in session.add.call_args_list]
    assert any(isinstance(a, CalibrationRule) for a in added)
    cal_rule = next(a for a in added if isinstance(a, CalibrationRule))
    assert cal_rule.patient_id == "1091"
    assert cal_rule.detector_type == "inertia"
    assert cal_rule.active is True
    assert cal_rule.approved_by == "dr.mehta"


# ===========================================================================
# Phase 7 / Fix 42 L2 — Calibration admin API routes
# ===========================================================================


@pytest.mark.asyncio
async def test_list_calibration_recommendations_api(client: AsyncClient):
    session = _mock_session()

    with patch(
        "app.api.calibration.get_calibration_recommendations",
        new_callable=AsyncMock,
        return_value=[
            {
                "patient_id": "1091",
                "detector_type": "gap",
                "dismissal_count": 5,
                "threshold": 4,
            }
        ],
    ):
        app.dependency_overrides[get_session] = lambda: session
        resp = await client.get("/api/admin/calibration-recommendations")
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["detector_type"] == "gap"


@pytest.mark.asyncio
async def test_create_calibration_rule_api(client: AsyncClient):
    from app.models.calibration_rule import CalibrationRule

    rule = CalibrationRule()
    rule.rule_id = "rule-001"
    rule.patient_id = "1091"
    rule.detector_type = "gap"
    rule.dismissal_count = 5
    rule.active = True
    rule.approved_at = datetime(2026, 4, 27, 10, 0, tzinfo=UTC)

    session = _mock_session()
    with patch(
        "app.api.calibration.approve_calibration_rule",
        new_callable=AsyncMock,
        return_value=rule,
    ):
        app.dependency_overrides[get_session] = lambda: session
        resp = await client.post(
            "/api/admin/calibration-rules",
            json={
                "patient_id": "1091",
                "detector_type": "gap",
                "dismissal_count": 5,
                "approved_by": "dr.mehta",
            },
        )
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    data = resp.json()
    assert data["rule_id"] == "rule-001"
    assert data["active"] is True


@pytest.mark.asyncio
async def test_create_calibration_rule_dismissal_count_below_threshold_rejected(
    client: AsyncClient,
):
    session = _mock_session()
    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/admin/calibration-rules",
        json={
            "patient_id": "1091",
            "detector_type": "gap",
            "dismissal_count": 2,  # < 4
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422


# ===========================================================================
# Phase 7 / Fix 42 L3 — Outcome verification unit tests
# ===========================================================================


@pytest.mark.asyncio
async def test_schedule_outcome_check_creates_pending_verification():
    from app.models.outcome_verification import OutcomeVerification
    from app.services.feedback.outcome_tracker import schedule_outcome_check

    session = AsyncMock()
    session.add = MagicMock()

    dismissed_at = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
    verification = await schedule_outcome_check(
        session,
        feedback_id="fb001",
        alert_id="a001",
        patient_id="1091",
        dismissed_at=dismissed_at,
    )

    added = [c.args[0] for c in session.add.call_args_list]
    assert any(isinstance(a, OutcomeVerification) for a in added)
    v = next(a for a in added if isinstance(a, OutcomeVerification))
    assert v.outcome_type == "pending"
    assert v.check_after == dismissed_at + timedelta(days=30)
    assert v.patient_id == "1091"


@pytest.mark.asyncio
async def test_run_outcome_checks_sets_deterioration_cluster():
    from app.models.alert import Alert
    from app.models.outcome_verification import OutcomeVerification
    from app.services.feedback.outcome_tracker import run_outcome_checks

    session = AsyncMock()
    session.flush = AsyncMock(return_value=None)

    verification = OutcomeVerification()
    verification.verification_id = "v001"
    verification.patient_id = "1091"
    verification.alert_id = "a001"
    verification.dismissed_at = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    verification.check_after = datetime(2026, 1, 31, 10, 0, tzinfo=UTC)
    verification.outcome_type = "pending"
    verification.prompted_at = None

    concerning_alert = Alert()
    concerning_alert.alert_type = "gap_urgent"

    due_result = MagicMock()
    due_result.scalars.return_value.all.return_value = [verification]
    concerning_result = MagicMock()
    concerning_result.scalar_one_or_none.return_value = concerning_alert

    session.execute.side_effect = [due_result, concerning_result]

    resolved = await run_outcome_checks(session)

    assert resolved == 1
    assert verification.outcome_type == "deterioration_cluster"
    assert verification.prompted_at is not None


@pytest.mark.asyncio
async def test_run_outcome_checks_sets_none_when_no_alerts():
    from app.models.outcome_verification import OutcomeVerification
    from app.services.feedback.outcome_tracker import run_outcome_checks

    session = AsyncMock()
    session.flush = AsyncMock(return_value=None)

    verification = OutcomeVerification()
    verification.verification_id = "v002"
    verification.patient_id = "1091"
    verification.alert_id = "a002"
    verification.dismissed_at = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    verification.check_after = datetime(2026, 1, 31, 10, 0, tzinfo=UTC)
    verification.outcome_type = "pending"
    verification.prompted_at = None

    due_result = MagicMock()
    due_result.scalars.return_value.all.return_value = [verification]
    no_concerning = MagicMock()
    no_concerning.scalar_one_or_none.return_value = None

    session.execute.side_effect = [due_result, no_concerning]

    resolved = await run_outcome_checks(session)

    assert resolved == 1
    assert verification.outcome_type == "none"


@pytest.mark.asyncio
async def test_run_outcome_checks_no_due_verifications():
    from app.services.feedback.outcome_tracker import run_outcome_checks

    session = AsyncMock()
    due_result = MagicMock()
    due_result.scalars.return_value.all.return_value = []
    session.execute.return_value = due_result

    resolved = await run_outcome_checks(session)

    assert resolved == 0
    session.flush.assert_not_called()


# ===========================================================================
# Phase 7 / Fix 42 L3 — Outcome verification admin API routes
# ===========================================================================


@pytest.mark.asyncio
async def test_list_outcome_verifications_returns_prompted(client: AsyncClient):
    v = _make_verification(
        prompted_at=datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
        clinician_response=None,
    )
    session = _mock_session()
    session.execute.return_value = _scalars_result(v)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/admin/outcome-verifications")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["verification_id"] == "v001"
    assert data[0]["clinician_response"] is None


@pytest.mark.asyncio
async def test_respond_to_outcome_verification_success(client: AsyncClient):
    v = _make_verification(
        prompted_at=datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
        clinician_response=None,
    )
    session = _mock_session()
    session.execute.return_value = _scalar_result(v)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/admin/outcome-verifications/v001/respond",
        json={"clinician_response": "relevant", "response_notes": "Pattern confirmed"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "responded"
    assert data["clinician_response"] == "relevant"


@pytest.mark.asyncio
async def test_respond_to_outcome_verification_already_responded(client: AsyncClient):
    v = _make_verification(
        prompted_at=datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
        clinician_response="not_relevant",
    )
    session = _mock_session()
    session.execute.return_value = _scalar_result(v)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/admin/outcome-verifications/v001/respond",
        json={"clinician_response": "relevant"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "already_responded"


@pytest.mark.asyncio
async def test_respond_to_outcome_verification_not_found(client: AsyncClient):
    session = _mock_session()
    session.execute.return_value = _scalar_result(None)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/admin/outcome-verifications/nonexistent/respond",
        json={"clinician_response": "unsure"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_respond_invalid_clinician_response_rejected(client: AsyncClient):
    session = _mock_session()
    app.dependency_overrides[get_session] = lambda: session
    resp = await client.post(
        "/api/admin/outcome-verifications/v001/respond",
        json={"clinician_response": "maybe"},  # not in allowed values
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422


# ===========================================================================
# Alert serialisation — off_hours + escalated fields present
# ===========================================================================


@pytest.mark.asyncio
async def test_alert_list_includes_off_hours_and_escalated_fields(client: AsyncClient):
    alert = MagicMock()
    alert.alert_id = "a001"
    alert.patient_id = "1091"
    alert.alert_type = "gap_urgent"
    alert.gap_days = 5
    alert.systolic_avg = None
    alert.triggered_at = datetime(2026, 4, 14, 19, 0, tzinfo=UTC)
    alert.delivered_at = datetime(2026, 4, 14, 19, 0, tzinfo=UTC)
    alert.acknowledged_at = None
    alert.off_hours = True
    alert.escalated = False

    session = _mock_session()
    session.execute.return_value = _scalars_result(alert)

    app.dependency_overrides[get_session] = lambda: session
    resp = await client.get("/api/alerts?patient_id=1091")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["off_hours"] is True
    assert data[0]["escalated"] is False
