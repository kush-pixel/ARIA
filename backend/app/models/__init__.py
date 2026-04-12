"""ARIA ORM models package.

Imports all 8 models so that:
1. ``from app.models import Patient, Reading, ...`` works throughout the codebase.
2. Every model is registered with ``Base.metadata`` before ``create_all()`` runs.
"""

from app.models.alert import Alert
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.patient import Patient
from app.models.processing_job import ProcessingJob
from app.models.reading import Reading

__all__ = [
    "Alert",
    "AuditEvent",
    "Briefing",
    "ClinicalContext",
    "MedicationConfirmation",
    "Patient",
    "ProcessingJob",
    "Reading",
]
