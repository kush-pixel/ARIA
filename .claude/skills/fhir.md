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
- MEDICATIONS (all visits) -> _aria_med_history (non-FHIR bundle key)
  bundle["_aria_med_history"] = list of medication timeline dicts
  Produced by adapter._build_med_history()
  Consumed by ingestion.py -> clinical_context.med_history
  Distinct from MedicationRequest resources (current list only,
  most-recent-wins by MED_CODE)
- Clinic readings: session="ad_hoc", source="clinic", submitted_by="clinic"
  Idempotency: COUNT(source="clinic") before inserting — skip if > 0
- _PATIENT_AGE_EXT = "_age" — non-standard extension key shared between
  adapter.py and ingestion.py; both define this constant independently
  (same value "_age"); import from adapter.py to avoid silent divergence
