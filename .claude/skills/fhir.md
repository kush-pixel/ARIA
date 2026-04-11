# /fhir — ARIA FHIR Adapter Skill
Working on FHIR adapter or ingestion service.

Rules:
- VITALS_DATETIME not ADMIT_DATE for BP observations
- LOINC 55284-4 (BP panel), 8480-6 (systolic), 8462-4 (diastolic)
- Ingestion is idempotent — safe to re-run on same Bundle
- clinical_context pre-computed at ingestion time
- CHF/Stroke/TIA in problem_codes -> auto-override to high tier
- Write audit_events for every bundle_import
- adapter.py: iEMR JSON -> FHIR Bundle dict
- ingestion.py: FHIR Bundle -> populate all 8 tables
