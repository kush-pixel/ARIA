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
  [ ] Full care timeline generated (not just 28 days)
  [ ] Baseline from median(historic_bp_systolic) not hardcoded 163

Pattern engine:
  [ ] Runs async via worker not in HTTP path
  [ ] Inertia requires ALL 5 conditions simultaneously (includes slope check)
  [ ] Inertia uses patient-adaptive threshold from threshold_utils.py (not hardcoded 140)
  [ ] Inertia reads med_history JSONB for last med change (not last_med_change column)
  [ ] Deterioration has absolute threshold gate (recent_avg >= patient_threshold)
  [ ] Deterioration has step-change sub-detector (7d rolling mean delta >= 15 mmHg)
  [ ] Adherence pattern A/B/C distinction made; Pattern B suppression applied
  [ ] Pattern A writes alert row with alert_type="adherence"

Briefing:
  [ ] Deterministic JSON complete before LLM layer
  [ ] All 10 fields populated (includes problem_assessments)
  [ ] visit_agenda in clinical priority order
  [ ] risk_score included in briefing JSON
  [ ] Inertia in agenda from inertia_result dict (not re-implemented inline)
  [ ] trend_summary uses adaptive window (not hardcoded "28-day")

Audit:
  [ ] bundle_import -> audit_events
  [ ] reading_ingested -> audit_events
  [ ] briefing_viewed -> audit_events + read_at update
  [ ] alert_acknowledged -> audit_events

Database:
  [ ] All 13 CREATE INDEX + 8 ALTER TABLE migrations in setup_db.py
  [ ] TIMESTAMPTZ not TIMESTAMP
  [ ] risk_score column exists on patients
  [ ] clinical_context: med_history, problem_assessments, recent_labs, vitals columns, allergy_reactions
  [ ] UNIQUE INDEX on readings (patient_id, effective_datetime, source)
  [ ] UNIQUE INDEX on medication_confirmations (patient_id, medication_name, scheduled_time)
  [ ] alerts.alert_type accepts "adherence"
  [ ] _aria_med_history, _aria_problem_assessments, _aria_visit_dates consumed in ingestion.py
  [ ] Clinic readings use session="ad_hoc" source="clinic"
  [ ] Ingestion idempotency: per-observation ON CONFLICT DO NOTHING (not batch COUNT)

Worker:
  [ ] Midnight pattern_recompute sweep for all monitoring_active patients
  [ ] Appointment date from patients.next_appointment (not idempotency_key parsing)
  [ ] Cold-start suppression: skip inertia/deterioration/adherence if enrolled < 14 days

Code:
  [ ] SQLAlchemy 2.0 async syntax throughout
  [ ] Pydantic v2 syntax throughout
  [ ] No git push or commit commands
