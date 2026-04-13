# /review — ARIA Clinical Review Skill
Review code for clinical accuracy, spec compliance, AI layer correctness.

Checklist:
Clinical boundary:
  [ ] No specific medication recommendations anywhere
  [ ] Language hedged throughout: possible not definitive
  [ ] Patient surfaces show no clinical data

Three-layer AI:
  [ ] Layer 1 runs before Layer 2 (risk scorer after all detectors)
  [ ] Layer 2 runs before Layer 3 (LLM after deterministic briefing)
  [ ] risk_score written to patients table
  [ ] Dashboard sorts by tier then risk_score DESC

Synthetic data:
  [ ] SD >= 8 mmHg (not flat)
  [ ] Morning higher than evening each week
  [ ] No round numbers
  [ ] Device outage = absent rows not nulls

Pattern engine:
  [ ] Runs async via worker not in HTTP path
  [ ] Inertia requires ALL 4 conditions simultaneously
  [ ] Adherence pattern A/B/C distinction made

Briefing:
  [ ] Deterministic JSON complete before LLM layer
  [ ] All 9 fields populated
  [ ] visit_agenda in clinical priority order
  [ ] risk_score included in briefing JSON

Audit:
  [ ] bundle_import -> audit_events
  [ ] reading_ingested -> audit_events
  [ ] briefing_viewed -> audit_events + read_at update
  [ ] alert_acknowledged -> audit_events

Database:
  [ ] All 12 indexes created before data inserted (11 CREATE INDEX + 1 ALTER TABLE)
  [ ] TIMESTAMPTZ not TIMESTAMP
  [ ] risk_score column exists on patients
  [ ] clinical_context.med_history JSONB column present
  [ ] _aria_med_history consumed from bundle in ingestion.py
  [ ] Clinic readings use session="ad_hoc" source="clinic"
  [ ] Index count is 12 not 11

Code:
  [ ] SQLAlchemy 2.0 async syntax throughout
  [ ] Pydantic v2 syntax throughout
  [ ] No git push or commit commands
