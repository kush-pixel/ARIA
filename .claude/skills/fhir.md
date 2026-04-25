# /fhir — ARIA FHIR Adapter Skill
Working on FHIR adapter or ingestion service.

Rules:
- VITALS_DATETIME not ADMIT_DATE for BP observations
- LOINC 55284-4 (BP panel), 8480-6 (systolic), 8462-4 (diastolic)
- Also capture: pulse 8867-4, weight 29463-7, SpO2 59408-5, temperature 8310-5
- Ingestion is idempotent — safe to re-run on same Bundle
- clinical_context pre-computed at ingestion time
- CHF/Stroke/TIA in problem_codes -> auto-override to high tier
- Write audit_events for every bundle_import
- adapter.py: iEMR JSON -> FHIR Bundle dict
- ingestion.py: FHIR Bundle -> populate all 8 tables

Non-standard bundle keys (appended to bundle root, NOT in bundle["entry"]):
- _aria_med_history: full medication timeline
  bundle["_aria_med_history"] = list of {name, rxnorm, date, activity} dicts
  -> clinical_context.med_history
- _aria_problem_assessments: per-visit problem assessment data
  bundle["_aria_problem_assessments"] = [{problem_code, visit_date, htn_flag, status_text, assessment_text}]
  -> clinical_context.problem_assessments JSONB
- _aria_visit_dates: all ADMIT_DATEs across all 124 visits
  bundle["_aria_visit_dates"] = [date_str, ...]
  -> clinical_context.last_visit_date = max(all_visit_dates)

Clinic readings idempotency:
  Per-observation ON CONFLICT DO NOTHING on UNIQUE (patient_id, effective_datetime, source)
  NOT the old batch COUNT check — new clinic visits must insert alongside existing rows
  UNIQUE INDEX required: idx_readings_patient_datetime_source

Allergies:
  Filter ALLERGY_STATUS == "Active" only — inactive must not appear
  Include ALLERGY_REACTION in manifestation text
  Store reactions in clinical_context.allergy_reactions[] parallel to allergies[]

- _PATIENT_AGE_EXT = "_age" — import from adapter.py in ingestion.py (do not redefine)
