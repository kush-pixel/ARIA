# /schema — ARIA Database Schema Skill
Working on models, database setup, or migrations.

Rules:
- 8 tables: patients (with risk_score), clinical_context, readings,
  medication_confirmations, alerts, briefings, processing_jobs, audit_events
- SQLAlchemy 2.0 async ORM only
- gen_random_uuid() for all UUID primary keys
- TIMESTAMPTZ for all timestamps (not TIMESTAMP)
- All 12 indexes from CLAUDE.md must exist before data is inserted
- patients.risk_score NUMERIC(5,2) — Layer 2 score 0.0-100.0
- Idempotency: processing_jobs.idempotency_key UNIQUE constraint
- Parallel arrays: active_problems[n] == problem_codes[n]
- clinical_context.med_history JSONB — full medication timeline,
  list of {name, rxnorm, date, activity} dicts,
  sorted chronologically, deduped by (name, date, activity)
- scripts/setup_db.py creates all tables and indexes
- Additive schema changes use ALTER TABLE ... ADD COLUMN IF NOT EXISTS
  in setup_db.py (same loop as indexes). Safe to re-run.
