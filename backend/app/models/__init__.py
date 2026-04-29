"""ARIA ORM models package.

Imports all models so that:
1. ``from app.models import Patient, Reading, ...`` works throughout the codebase.
2. Every model is registered with ``Base.metadata`` before ``create_all()`` runs.
"""

from app.models.alert import Alert
from app.models.alert_feedback import AlertFeedback
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.calibration_rule import CalibrationRule
from app.models.chat_session import ChatSession
from app.models.clinical_context import ClinicalContext
from app.models.clinician import Clinician
from app.models.gap_explanation import GapExplanation
from app.models.medication_confirmation import MedicationConfirmation
from app.models.outcome_verification import OutcomeVerification
from app.models.patient import Patient
from app.models.processing_job import ProcessingJob
from app.models.reading import Reading

__all__ = [
    "Alert",
    "AlertFeedback",
    "AuditEvent",
    "Briefing",
    "CalibrationRule",
    "ChatSession",
    "ClinicalContext",
    "Clinician",
    "GapExplanation",
    "MedicationConfirmation",
    "OutcomeVerification",
    "Patient",
    "ProcessingJob",
    "Reading",
]
