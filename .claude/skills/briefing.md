# /briefing — ARIA Briefing Skill
Working on briefing composer or LLM summarizer.

Order — STRICT:
1. composer.py produces complete deterministic briefing JSON (no LLM)
2. Verify all 10 fields populated and clinically correct
3. summarizer.py optionally adds Layer 3 LLM readable text
4. llm_validator.py validates LLM output before readable_summary is stored
   — guardrails: forbidden language, PHI leak, prompt injection (absolute blocks)
   — faithfulness: sentence count, risk score, adherence pattern, medication names,
     BP plausibility, titration language, urgent flags, contradiction detection
   — retry once on failure, then readable_summary=None
   — always writes audit_events: action="llm_validation"

All 13 briefing JSON fields required:
trend_summary, trend_avg_systolic, medication_status, adherence_summary,
active_problems, problem_assessments, overdue_labs, drug_interactions, visit_agenda,
urgent_flags, risk_score, data_limitations, patient_context

drug_interactions: output of medication_safety.check_interactions(ctx) — deterministic, no LLM.
  Four rules: nsaid_antihypertensive, triple_whammy, k_sparing_ace_arb, bb_non_dhp_ccb.
  Severity escalates with CHF/CKD comorbidities. Triple whammy suppresses nsaid_antihypertensive.
  Critical interactions go first in visit_agenda; concern alongside urgent alerts; warning after adherence.
patient_context: clinical_context.social_context (null if absent — always include key in payload).

trend_avg_systolic: home-readings-only mean (excludes source="clinic") for the briefing window.
  Null when no home readings. Surfaced via GET /api/patients as Patient.trend_avg_systolic so the
  dashboard BP Trend shows the identical number as the briefing (no independent recomputation).

Briefing API lifecycle (GET /api/briefings/{patient_id}):
  Only returns active briefings: appointment_date IS NULL (mini) OR appointment_date >= today.
  Past appointment briefings excluded — clinician sees current state between visits.
  appointment_date may be None in response for mini-briefings.

visit_agenda priority: urgent alerts, inertia, adherence concern,
                       overdue labs, active problems, next appt

medication_status field (Fix 34): when days_since_med_change <= titration_window, append
  "— within expected titration window, full response may not yet be established"
  titration_window is drug-class-aware (TITRATION_WINDOWS):
    diuretics/beta-blockers → 14d, ACE/ARBs → 28d, amlodipine → 56d, default → 42d
  Consistent with Pattern B suppression window. Informs without making a clinical judgment.

social_context (Fix 29): when clinical_context.social_context is non-null,
  include as patient_context field in briefing payload.

Inertia in visit_agenda: consume inertia_result["inertia_detected"] from Layer 1 dict —
  do NOT re-implement inertia logic inline in composer.py (Fix 18).

trend_summary: adaptive window (14–90 days based on inter-visit interval)
  + 90-day trajectory from historic_bp_systolic where available (Fix 47).

Language: possible may suggest — NEVER definitive
LLM layer: log model_version + prompt_hash + generated_at
Briefing view: update briefings.read_at + write audit_events
