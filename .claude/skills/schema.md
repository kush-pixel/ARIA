# /schema — ARIA Database Schema Skill
Working on models, database setup, or migrations.

Rules:
- 9 tables: patients (with risk_score + risk_score_computed_at), clinical_context, readings,
  medication_confirmations, alerts, alert_feedback, briefings, processing_jobs, audit_events
- SQLAlchemy 2.0 async ORM only
- gen_random_uuid() for all UUID primary keys
- TIMESTAMPTZ for all timestamps (not TIMESTAMP)
- All indexes from CLAUDE.md must exist before data is inserted (13 CREATE INDEX + 8 ALTER TABLE)
- patients.risk_score NUMERIC(5,2) — Layer 2 score 0.0-100.0
- patients.risk_score_computed_at TIMESTAMPTZ — set on every score update (Fix 61); frontend staleness badge if > 26h
- Idempotency: processing_jobs.idempotency_key UNIQUE constraint
- Parallel arrays: active_problems[n] == problem_codes[n]

clinical_context columns (full list — all must be present):
  active_problems TEXT[], problem_codes TEXT[], current_medications TEXT[], med_rxnorm_codes TEXT[]
  med_history JSONB               — full timeline {name, rxnorm, date, activity}
  problem_assessments JSONB       — {problem_code, visit_date, htn_flag, status_text, assessment_text}
  recent_labs JSONB               — {loinc_code: {value, unit, date}}
  last_med_change DATE
  allergies TEXT[], allergy_reactions TEXT[]   — parallel arrays; active allergies only
  last_visit_date DATE            — max across ALL visit dates (not just BP clinic dates)
  last_clinic_systolic SMALLINT, last_clinic_diastolic SMALLINT
  last_clinic_pulse SMALLINT, last_clinic_weight_kg NUMERIC(5,1), last_clinic_spo2 NUMERIC(4,1)
  historic_bp_systolic SMALLINT[], historic_bp_dates DATE[]
  historic_spo2 NUMERIC[]
  overdue_labs TEXT[], social_context TEXT

alerts.alert_type: gap_urgent | gap_briefing | inertia | deterioration | adherence

alert_feedback (Fix 42 L1 — clinician disposition on acknowledge):
  feedback_id UUID PK, alert_id UUID FK -> alerts, patient_id TEXT, detector_type TEXT
  disposition TEXT (agree_acting | agree_monitoring | disagree)
  reason_text TEXT, clinician_id TEXT, created_at TIMESTAMPTZ
  Indexes: (patient_id, detector_type, created_at DESC), (alert_id)

Critical unique indexes (idempotency):
  UNIQUE on readings (patient_id, effective_datetime, source)       — Fix 22 prerequisite for Fix 15
  UNIQUE on medication_confirmations (patient_id, medication_name, scheduled_time)

- scripts/setup_db.py creates all tables and indexes
- Additive schema changes use ALTER TABLE ... ADD COLUMN IF NOT EXISTS in setup_db.py. Safe to re-run.
- Pending migration: ALTER TABLE patients ADD COLUMN IF NOT EXISTS risk_score_computed_at TIMESTAMPTZ;
