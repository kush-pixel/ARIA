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
  [ ] Inertia reads med_history JSONB activity field (not last_med_change column)
  [ ] Comorbidity threshold adjustment covers severe-single (CHF/Stroke/TIA) + cardio+metabolic cases
  [ ] threshold_adjustment_mode logged (full vs degraded depending on Fix 7 status)
  [ ] Deterioration has absolute threshold gate (recent_avg >= patient_threshold)
  [ ] Deterioration has step-change sub-detector (7d rolling mean delta >= 15 mmHg)
  [ ] Adherence pattern A/B/C distinction made; Pattern B suppression applied
  [ ] Pattern B suppression uses drug-class-aware titration_window (TITRATION_WINDOWS, NOT blanket 42d)
        diuretics/beta-blockers → 14d, ACE/ARBs → 28d, amlodipine → 56d, default → 42d
  [ ] Pattern B suppression not applied when no recent med change exists
  [ ] Pattern A writes alert row with alert_type="adherence"
  [ ] Adaptive window null-safe: falls back to 28d when next_appointment or last_visit_date is None
  [ ] White-coat exclusion uses 5-day window (matches synthetic 3-5d dip rule)

Briefing:
  [ ] Deterministic JSON complete before LLM layer
  [ ] All 13 fields populated (includes problem_assessments, trend_avg_systolic, drug_interactions, patient_context)
  [ ] trend_avg_systolic computed from home readings only (excludes source="clinic")
  [ ] drug_interactions from medication_safety.check_interactions() — not re-implemented inline
  [ ] Triple whammy suppresses nsaid_antihypertensive (no duplicate flags)
  [ ] Critical interactions first in visit_agenda; concern alongside urgent; warning after adherence
  [ ] visit_agenda in clinical priority order
  [ ] risk_score included in briefing JSON
  [ ] Inertia in agenda from inertia_result dict (not re-implemented inline)
  [ ] trend_summary uses adaptive window (not hardcoded "28-day")
  [ ] GET /api/briefings only returns active briefings (appointment_date >= today OR IS NULL)
  [ ] llm_validator.py called after summarizer.py, before storing readable_summary
  [ ] Guardrails check: forbidden language, PHI leak, prompt injection
  [ ] Faithfulness check: sentence count=3, risk score, adherence, medications, BP, contradictions
  [ ] Retry once on validation failure, then readable_summary=None
  [ ] audit_events written for every llm_validation call (success and failure)

Audit:
  [ ] bundle_import -> audit_events
  [ ] reading_ingested -> audit_events
  [ ] briefing_viewed -> audit_events + read_at update
  [ ] alert_acknowledged -> audit_events

Database:
  [ ] All 19 CREATE INDEX + 10 ALTER TABLE migrations in setup_db.py
  [ ] TIMESTAMPTZ not TIMESTAMP
  [ ] risk_score column exists on patients
  [ ] risk_score_computed_at column exists on patients (Fix 61)
  [ ] clinical_context: med_history, problem_assessments, recent_labs, vitals columns, allergy_reactions
  [ ] UNIQUE INDEX on readings (patient_id, effective_datetime, source)
  [ ] UNIQUE INDEX on medication_confirmations (patient_id, medication_name, scheduled_time)
  [ ] alerts.alert_type accepts "adherence"
  [ ] alerts.off_hours BOOLEAN (Fix 45); alerts.escalated BOOLEAN (Fix 45)
  [ ] alert_feedback table exists with FK to alerts (Fix 42 L1)
  [ ] gap_explanations table exists (Fix 41)
  [ ] calibration_rules table exists (Fix 42 L2)
  [ ] outcome_verifications table exists with FK to alert_feedback (Fix 42 L3)
  [ ] _aria_med_history, _aria_problem_assessments, _aria_visit_dates consumed in ingestion.py
  [ ] Clinic readings use session="ad_hoc" source="clinic"
  [ ] Ingestion idempotency: per-observation ON CONFLICT DO NOTHING (not batch COUNT)

Worker:
  [ ] Midnight pattern_recompute sweep for all monitoring_active patients
  [ ] Appointment date from patients.next_appointment (not idempotency_key parsing)
  [ ] Cold-start suppression: skip inertia/deterioration/adherence if enrolled < 21 days (NOT 14)
  [ ] delivered_at set on alert insert (not left NULL)
  [ ] off_hours tagged at alert insert via _is_off_hours(triggered_at) (Fix 45)
  [ ] _run_escalation_sweep() called every poll cycle (Fix 45)
  [ ] run_outcome_checks() called every poll cycle (Fix 42 L3)
  [ ] risk_score_computed_at written on every risk score update

Code:
  [ ] SQLAlchemy 2.0 async syntax throughout
  [ ] Pydantic v2 syntax throughout
  [ ] No git push or commit commands
