# /schema — ARIA Database Schema Skill
Working on models, database setup, or migrations.

Rules:
- 12 tables: patients (with risk_score + risk_score_computed_at), clinical_context, readings,
  medication_confirmations, alerts (with off_hours + escalated), alert_feedback,
  briefings, processing_jobs, audit_events, gap_explanations, calibration_rules, outcome_verifications
- SQLAlchemy 2.0 async ORM only
- gen_random_uuid() for all UUID primary keys
- TIMESTAMPTZ for all timestamps (not TIMESTAMP)
- All indexes from CLAUDE.md must exist before data is inserted (19 CREATE INDEX + 10 ALTER TABLE)
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

gap_explanations (Fix 41 — clinician-recorded gap reasons):
  explanation_id UUID PK, patient_id TEXT, gap_start DATE, gap_end DATE
  reason TEXT (device_issue | travel | illness | unknown | non_compliance)
  notes TEXT, reported_by TEXT (clinician | patient | system), reporter_id TEXT, created_at TIMESTAMPTZ
  Index: (patient_id, gap_start DESC)

calibration_rules (Fix 42 L2 — clinician-approved detector calibration):
  rule_id UUID PK, patient_id TEXT, detector_type TEXT, dismissal_count INTEGER
  approved_by TEXT, approved_at TIMESTAMPTZ, notes TEXT, active BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ
  Index: (patient_id, detector_type, active)

outcome_verifications (Fix 42 L3 — 30-day retrospective outcomes):
  verification_id UUID PK, feedback_id UUID FK -> alert_feedback, alert_id UUID FK -> alerts
  patient_id TEXT, dismissed_at TIMESTAMPTZ, check_after TIMESTAMPTZ (dismissed_at + 30d)
  outcome_type TEXT DEFAULT 'pending' (pending | deterioration_cluster | none)
  prompted_at TIMESTAMPTZ, clinician_response TEXT (relevant | not_relevant | unsure)
  responded_at TIMESTAMPTZ, response_notes TEXT, created_at TIMESTAMPTZ
  Indexes: (outcome_type, check_after) WHERE outcome_type='pending', (patient_id, prompted_at DESC)

alerts table additions (Fix 45):
  off_hours BOOLEAN DEFAULT FALSE    — tagged at insert time via _is_off_hours(triggered_at)
  escalated BOOLEAN DEFAULT FALSE    — set TRUE after 24h unacknowledged (gap_urgent, deterioration)

Critical unique indexes (idempotency):
  UNIQUE on readings (patient_id, effective_datetime, source)       — Fix 22 prerequisite for Fix 15
  UNIQUE on medication_confirmations (patient_id, medication_name, scheduled_time)

- scripts/setup_db.py creates all tables and indexes
- Additive schema changes use ALTER TABLE ... ADD COLUMN IF NOT EXISTS in setup_db.py. Safe to re-run.
