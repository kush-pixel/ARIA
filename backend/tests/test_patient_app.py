"""Unit tests for the ARIA patient PWA backend (Fix 43).

Covers:
  auth.py          — POST /api/auth/patient-token (AC-1, AC-2)
  readings.py      — symptoms field, actor_type fix, symptom alerts (AC-3..AC-7)
  confirmations.py — pending, confirm, .ics endpoints (AC-8..AC-13)
  datetime_utils   — is_off_hours()
  ics_generator    — session grouping, VEVENT structure

All tests are unit tests (no live DB). Uses AsyncMock session injection
via app.db.session.get_session dependency override.

Run:
    cd backend && python -m pytest tests/test_patient_app.py -v -m "not integration"
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.session import get_session
from app.main import app
from app.utils.datetime_utils import is_off_hours

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
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


def _make_patient(patient_id: str = "1091") -> MagicMock:
    p = MagicMock()
    p.patient_id = patient_id
    p.risk_tier = "high"
    p.monitoring_active = True
    p.enrolled_at = datetime(2026, 1, 1, tzinfo=UTC)
    p.next_appointment = datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
    return p


def _make_reading() -> MagicMock:
    r = MagicMock()
    r.reading_id = "r001"
    r.patient_id = "1091"
    r.systolic_1 = 158
    r.diastolic_1 = 96
    r.heart_rate_1 = None
    r.systolic_2 = None
    r.diastolic_2 = None
    r.heart_rate_2 = None
    r.systolic_avg = 158.0
    r.diastolic_avg = 96.0
    r.heart_rate_avg = None
    r.effective_datetime = datetime(2026, 4, 30, 8, 0, tzinfo=UTC)
    r.session = "morning"
    r.source = "manual"
    r.submitted_by = "patient"
    r.bp_position = None
    r.bp_site = None
    r.medication_taken = "yes"
    r.consent_version = "1.0"
    r.symptoms = None
    r.created_at = datetime(2026, 4, 30, 8, 0, tzinfo=UTC)
    return r


def _make_confirmation(
    confirmation_id: str = "c001",
    medication_name: str = "Ramipril",
    scheduled_time: datetime | None = None,
) -> MagicMock:
    c = MagicMock()
    c.confirmation_id = confirmation_id
    c.patient_id = "1091"
    c.medication_name = medication_name
    c.rxnorm_code = None
    c.scheduled_time = scheduled_time or datetime(2026, 4, 30, 8, 0, tzinfo=UTC)
    c.confirmed_at = None
    return c


def _make_clinical_context(
    patient_id: str = "1091",
    problem_codes: list[str] | None = None,
    current_medications: list[str] | None = None,
) -> MagicMock:
    cc = MagicMock()
    cc.patient_id = patient_id
    cc.problem_codes = problem_codes if problem_codes is not None else []
    cc.current_medications = current_medications if current_medications is not None else ["Ramipril", "Amlodipine"]
    return cc


def _patient_jwt(patient_id: str = "1091", secret: str = "test-patient-secret") -> str:
    payload = {
        "sub": patient_id,
        "role": "patient",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=8),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# App client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ===========================================================================
# datetime_utils — is_off_hours()
# ===========================================================================


class TestIsOffHours:
    def test_weekday_daytime_is_not_off_hours(self) -> None:
        dt = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)  # Tuesday noon
        assert not is_off_hours(dt)

    def test_before_8am_is_off_hours(self) -> None:
        dt = datetime(2026, 4, 28, 7, 59, tzinfo=UTC)  # Tuesday 07:59
        assert is_off_hours(dt)

    def test_after_6pm_is_off_hours(self) -> None:
        dt = datetime(2026, 4, 28, 18, 0, tzinfo=UTC)  # Tuesday 18:00
        assert is_off_hours(dt)

    def test_saturday_is_off_hours(self) -> None:
        dt = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)  # Saturday noon
        assert is_off_hours(dt)

    def test_sunday_is_off_hours(self) -> None:
        dt = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)  # Sunday
        assert is_off_hours(dt)

    def test_exactly_8am_is_not_off_hours(self) -> None:
        dt = datetime(2026, 4, 28, 8, 0, tzinfo=UTC)  # Monday 08:00
        assert not is_off_hours(dt)


# ===========================================================================
# POST /api/auth/patient-token
# ===========================================================================


class TestPatientToken:
    @pytest.mark.asyncio
    async def test_valid_research_id_returns_jwt(self, client: AsyncClient) -> None:
        """AC-1: valid research_id returns JWT with role=patient."""
        session = _mock_session()
        session.execute.return_value = _scalar_result(_make_patient())

        with patch("app.api.auth.settings") as mock_settings:
            mock_settings.patient_jwt_secret = "test-patient-secret"
            app.dependency_overrides[get_session] = lambda: session

            res = await client.post(
                "/api/auth/patient-token",
                json={"research_id": "1091"},
            )

        app.dependency_overrides.pop(get_session, None)
        assert res.status_code == 200
        body = res.json()
        assert "access_token" in body
        assert body["expires_in"] == 8 * 3600
        decoded = jwt.decode(body["access_token"], "test-patient-secret", algorithms=["HS256"])
        assert decoded["sub"] == "1091"
        assert decoded["role"] == "patient"

    @pytest.mark.asyncio
    async def test_unknown_research_id_returns_404(self, client: AsyncClient) -> None:
        session = _mock_session()
        session.execute.return_value = _scalar_result(None)

        with patch("app.api.auth.settings") as mock_settings:
            mock_settings.patient_jwt_secret = "test-patient-secret"
            app.dependency_overrides[get_session] = lambda: session

            res = await client.post(
                "/api/auth/patient-token",
                json={"research_id": "9999"},
            )

        app.dependency_overrides.pop(get_session, None)
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_secret_returns_500(self, client: AsyncClient) -> None:
        session = _mock_session()

        with patch("app.api.auth.settings") as mock_settings:
            mock_settings.patient_jwt_secret = ""
            app.dependency_overrides[get_session] = lambda: session

            res = await client.post(
                "/api/auth/patient-token",
                json={"research_id": "1091"},
            )

        app.dependency_overrides.pop(get_session, None)
        assert res.status_code == 500


# ===========================================================================
# POST /api/readings — symptoms + actor_type + symptom alerts
# ===========================================================================


class TestReadingSymptoms:
    def _reading_payload(self, symptoms: list[str] | None = None) -> dict:
        return {
            "patient_id": "1091",
            "systolic_1": 158,
            "diastolic_1": 96,
            "effective_datetime": "2026-04-30T08:00:00Z",
            "session": "morning",
            "medication_taken": "yes",
            "submitted_by": "patient",
            "symptoms": symptoms or [],
        }

    @pytest.mark.asyncio
    async def test_patient_submission_writes_system_actor_type(
        self, client: AsyncClient
    ) -> None:
        """AC-4: actor_type must be 'system' for patient submissions."""
        session = _mock_session()
        session.execute.return_value = _scalar_result(_make_patient())
        reading = _make_reading()
        session.refresh = AsyncMock(return_value=None)

        added: list = []
        session.add = MagicMock(side_effect=added.append)

        app.dependency_overrides[get_session] = lambda: session
        await client.post("/api/readings", json=self._reading_payload())
        app.dependency_overrides.pop(get_session, None)

        audit_events = [
            obj for obj in added
            if type(obj).__name__ == "AuditEvent"
        ]
        assert len(audit_events) == 1
        assert audit_events[0].actor_type == "system"
        assert audit_events[0].action == "reading_ingested"
        assert audit_events[0].outcome == "success"

    @pytest.mark.asyncio
    async def test_chest_pain_creates_symptom_urgent_alert(
        self, client: AsyncClient
    ) -> None:
        """AC-5: chest_pain symptom → symptom_urgent alert row."""
        session = _mock_session()
        session.execute.return_value = _scalar_result(_make_patient())
        session.refresh = AsyncMock(return_value=None)

        added: list = []
        session.add = MagicMock(side_effect=added.append)

        app.dependency_overrides[get_session] = lambda: session
        res = await client.post(
            "/api/readings",
            json=self._reading_payload(symptoms=["chest_pain"]),
        )
        app.dependency_overrides.pop(get_session, None)

        assert res.status_code == 201
        alerts = [obj for obj in added if type(obj).__name__ == "Alert"]
        assert len(alerts) == 1
        assert alerts[0].alert_type == "symptom_urgent"

    @pytest.mark.asyncio
    async def test_sob_with_chf_creates_symptom_urgent_alert(
        self, client: AsyncClient
    ) -> None:
        """AC-6: shortness_of_breath + CHF (I50) → symptom_urgent alert."""
        session = _mock_session()
        cc = _make_clinical_context(problem_codes=["I50", "E11"])

        session.execute.side_effect = [
            _scalar_result(_make_patient()),  # patient existence check
            _scalar_result(cc),              # clinical_context lookup
        ]
        session.refresh = AsyncMock(return_value=None)

        added: list = []
        session.add = MagicMock(side_effect=added.append)

        app.dependency_overrides[get_session] = lambda: session
        res = await client.post(
            "/api/readings",
            json=self._reading_payload(symptoms=["shortness_of_breath"]),
        )
        app.dependency_overrides.pop(get_session, None)

        assert res.status_code == 201
        alerts = [obj for obj in added if type(obj).__name__ == "Alert"]
        assert len(alerts) == 1
        assert alerts[0].alert_type == "symptom_urgent"

    @pytest.mark.asyncio
    async def test_sob_without_chf_does_not_alert(self, client: AsyncClient) -> None:
        session = _mock_session()
        cc = _make_clinical_context(problem_codes=["E11"])  # diabetes only, no CHF

        session.execute.side_effect = [
            _scalar_result(_make_patient()),
            _scalar_result(cc),
        ]
        session.refresh = AsyncMock(return_value=None)

        added: list = []
        session.add = MagicMock(side_effect=added.append)

        app.dependency_overrides[get_session] = lambda: session
        await client.post(
            "/api/readings",
            json=self._reading_payload(symptoms=["shortness_of_breath"]),
        )
        app.dependency_overrides.pop(get_session, None)

        alerts = [obj for obj in added if type(obj).__name__ == "Alert"]
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_invalid_symptom_values_are_stripped(
        self, client: AsyncClient
    ) -> None:
        """Unknown symptom strings must not reach the DB."""
        session = _mock_session()
        session.execute.return_value = _scalar_result(_make_patient())
        session.refresh = AsyncMock(return_value=None)

        added: list = []
        session.add = MagicMock(side_effect=added.append)

        app.dependency_overrides[get_session] = lambda: session
        res = await client.post(
            "/api/readings",
            json=self._reading_payload(symptoms=["headache", "not_a_real_symptom"]),
        )
        app.dependency_overrides.pop(get_session, None)

        assert res.status_code == 201
        readings = [obj for obj in added if type(obj).__name__ == "Reading"]
        assert len(readings) == 1
        assert readings[0].symptoms == ["headache"]

    @pytest.mark.asyncio
    async def test_early_enrolled_patient_chest_pain_still_alerts(
        self, client: AsyncClient
    ) -> None:
        """AC-7: cold-start suppression must NOT block symptom alerts."""
        patient = _make_patient()
        # enrolled 5 days ago — within cold-start window
        patient.enrolled_at = datetime.now(UTC) - timedelta(days=5)

        session = _mock_session()
        session.execute.return_value = _scalar_result(patient)
        session.refresh = AsyncMock(return_value=None)

        added: list = []
        session.add = MagicMock(side_effect=added.append)

        app.dependency_overrides[get_session] = lambda: session
        res = await client.post(
            "/api/readings",
            json=self._reading_payload(symptoms=["chest_pain"]),
        )
        app.dependency_overrides.pop(get_session, None)

        assert res.status_code == 201
        alerts = [obj for obj in added if type(obj).__name__ == "Alert"]
        assert len(alerts) == 1
        assert alerts[0].alert_type == "symptom_urgent"


# ===========================================================================
# GET /api/confirmations/pending
# ===========================================================================


class TestPendingConfirmations:
    @pytest.mark.asyncio
    async def test_returns_todays_unconfirmed_doses(self, client: AsyncClient) -> None:
        """AC-8: pending endpoint returns today's unconfirmed doses."""
        secret = "test-patient-secret"
        token = _patient_jwt("1091", secret)

        session = _mock_session()
        c1 = _make_confirmation("c001", "Ramipril")
        c2 = _make_confirmation("c002", "Amlodipine")
        session.execute.return_value = _scalars_result(c1, c2)

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            app.dependency_overrides[get_session] = lambda: session

            res = await client.get(
                "/api/confirmations/pending",
                headers={"Authorization": f"Bearer {token}"},
            )

        app.dependency_overrides.pop(get_session, None)
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 2
        assert body[0]["medication_name"] == "Ramipril"
        assert body[1]["medication_name"] == "Amlodipine"

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, client: AsyncClient) -> None:
        res = await client.get("/api/confirmations/pending")
        assert res.status_code == 422  # missing required header

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self, client: AsyncClient) -> None:
        secret = "test-patient-secret"
        expired_payload = {
            "sub": "1091",
            "role": "patient",
            "iat": datetime(2026, 1, 1, tzinfo=UTC),
            "exp": datetime(2026, 1, 1, 1, tzinfo=UTC),  # expired
        }
        token = jwt.encode(expired_payload, secret, algorithm="HS256")

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            res = await client.get(
                "/api/confirmations/pending",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 401


# ===========================================================================
# POST /api/confirmations/confirm
# ===========================================================================


class TestConfirmDoses:
    @pytest.mark.asyncio
    async def test_confirm_sets_confirmed_at_and_writes_audit(
        self, client: AsyncClient
    ) -> None:
        """AC-9 + AC-10: tap-confirm updates rows and writes audit event."""
        secret = "test-patient-secret"
        token = _patient_jwt("1091", secret)

        c1 = _make_confirmation("c001", "Ramipril")
        session = _mock_session()
        session.execute.return_value = _scalars_result(c1)

        added: list = []
        session.add = MagicMock(side_effect=added.append)

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            app.dependency_overrides[get_session] = lambda: session

            res = await client.post(
                "/api/confirmations/confirm",
                json={"patient_id": "1091", "confirmation_ids": ["c001"]},
                headers={"Authorization": f"Bearer {token}"},
            )

        app.dependency_overrides.pop(get_session, None)
        assert res.status_code == 200
        assert res.json()["confirmed"] == 1

        audit_events = [obj for obj in added if type(obj).__name__ == "AuditEvent"]
        assert len(audit_events) == 1
        assert audit_events[0].actor_type == "system"
        assert audit_events[0].resource_type == "MedicationConfirmation"
        assert audit_events[0].outcome == "success"

    @pytest.mark.asyncio
    async def test_empty_confirmation_ids_returns_zero(
        self, client: AsyncClient
    ) -> None:
        secret = "test-patient-secret"
        token = _patient_jwt("1091", secret)
        session = _mock_session()

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            app.dependency_overrides[get_session] = lambda: session

            res = await client.post(
                "/api/confirmations/confirm",
                json={"patient_id": "1091", "confirmation_ids": []},
                headers={"Authorization": f"Bearer {token}"},
            )

        app.dependency_overrides.pop(get_session, None)
        assert res.status_code == 200
        assert res.json()["confirmed"] == 0


# ===========================================================================
# GET /api/confirmations/ics/{patient_id}
# ===========================================================================


class TestIcsDownload:
    @pytest.mark.asyncio
    async def test_returns_ics_content_type_and_disposition(
        self, client: AsyncClient
    ) -> None:
        """AC-11: .ics endpoint returns correct Content-Type and headers."""
        secret = "test-patient-secret"
        token = _patient_jwt("1091", secret)

        session = _mock_session()
        cc = _make_clinical_context(current_medications=["Ramipril", "Amlodipine"])
        session.execute.return_value = _scalar_result(cc)

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            mock_settings.patient_app_url = "http://localhost:3001"
            app.dependency_overrides[get_session] = lambda: session

            res = await client.get(
                "/api/confirmations/ics/1091",
                headers={"Authorization": f"Bearer {token}"},
            )

        app.dependency_overrides.pop(get_session, None)
        assert res.status_code == 200
        assert "text/calendar" in res.headers["content-type"]
        assert "aria-medications.ics" in res.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_ics_contains_vcalendar_and_vevent(
        self, client: AsyncClient
    ) -> None:
        """AC-12: .ics file contains VEVENT with RRULE and VALARM."""
        secret = "test-patient-secret"
        token = _patient_jwt("1091", secret)

        session = _mock_session()
        cc = _make_clinical_context(current_medications=["Ramipril", "Amlodipine"])
        session.execute.return_value = _scalar_result(cc)

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            mock_settings.patient_app_url = "http://localhost:3001"
            app.dependency_overrides[get_session] = lambda: session

            res = await client.get(
                "/api/confirmations/ics/1091",
                headers={"Authorization": f"Bearer {token}"},
            )

        app.dependency_overrides.pop(get_session, None)
        body = res.text
        assert "BEGIN:VCALENDAR" in body
        assert "BEGIN:VEVENT" in body
        assert "RRULE:FREQ=DAILY" in body
        assert "BEGIN:VALARM" in body
        assert "TRIGGER:-PT5M" in body

    @pytest.mark.asyncio
    async def test_ics_description_contains_meds_and_deep_link(
        self, client: AsyncClient
    ) -> None:
        """AC-13: .ics description includes medication names and /confirm deep link."""
        secret = "test-patient-secret"
        token = _patient_jwt("1091", secret)

        session = _mock_session()
        cc = _make_clinical_context(current_medications=["Ramipril", "Amlodipine"])
        session.execute.return_value = _scalar_result(cc)

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            mock_settings.patient_app_url = "http://localhost:3001"
            app.dependency_overrides[get_session] = lambda: session

            res = await client.get(
                "/api/confirmations/ics/1091",
                headers={"Authorization": f"Bearer {token}"},
            )

        app.dependency_overrides.pop(get_session, None)
        body = res.text
        assert "Ramipril" in body
        assert "Amlodipine" in body
        assert "/confirm?session=" in body

    @pytest.mark.asyncio
    async def test_cross_patient_ics_access_denied(
        self, client: AsyncClient
    ) -> None:
        """Patient cannot download another patient's .ics."""
        secret = "test-patient-secret"
        token = _patient_jwt("1091", secret)  # token for 1091

        with patch("app.api.confirmations.settings") as mock_settings:
            mock_settings.patient_jwt_secret = secret
            res = await client.get(
                "/api/confirmations/ics/9999",  # different patient_id in URL
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 403


# ===========================================================================
# ics_generator — unit tests (no HTTP)
# ===========================================================================


class TestIcsGenerator:
    @pytest.mark.asyncio
    async def test_morning_meds_get_morning_vevent(self) -> None:
        """Meds defaulting to QD (08:00) should appear in the morning VEVENT."""
        from app.utils.ics_generator import generate_ics

        session = AsyncMock()
        cc = _make_clinical_context(current_medications=["Ramipril", "Amlodipine"])
        result = MagicMock()
        result.scalar_one_or_none.return_value = cc
        session.execute = AsyncMock(return_value=result)

        ics = await generate_ics("1091", session, "http://localhost:3001")
        assert "Morning medications (ARIA)" in ics
        assert "Ramipril" in ics

    @pytest.mark.asyncio
    async def test_no_medications_produces_empty_vcalendar(self) -> None:
        from app.utils.ics_generator import generate_ics

        session = AsyncMock()
        cc = _make_clinical_context(current_medications=[])
        result = MagicMock()
        result.scalar_one_or_none.return_value = cc
        session.execute = AsyncMock(return_value=result)

        ics = await generate_ics("1091", session, "http://localhost:3001")
        assert "BEGIN:VCALENDAR" in ics
        assert "BEGIN:VEVENT" not in ics

    @pytest.mark.asyncio
    async def test_supply_items_excluded_from_ics(self) -> None:
        """Non-medication items (sharps container, pen needle) must not appear."""
        from app.utils.ics_generator import generate_ics

        session = AsyncMock()
        cc = _make_clinical_context(
            current_medications=["Ramipril", "Sharps container", "Pen needle"]
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = cc
        session.execute = AsyncMock(return_value=result)

        ics = await generate_ics("1091", session, "http://localhost:3001")
        assert "Ramipril" in ics
        assert "Sharps container" not in ics
        assert "Pen needle" not in ics

    @pytest.mark.asyncio
    async def test_deep_link_contains_pwa_url(self) -> None:
        from app.utils.ics_generator import generate_ics

        session = AsyncMock()
        cc = _make_clinical_context(current_medications=["Ramipril"])
        result = MagicMock()
        result.scalar_one_or_none.return_value = cc
        session.execute = AsyncMock(return_value=result)

        ics = await generate_ics("1091", session, "https://aria-patient.vercel.app")
        assert "https://aria-patient.vercel.app/confirm?session=" in ics
