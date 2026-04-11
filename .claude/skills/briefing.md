# /briefing — ARIA Briefing Skill
Working on briefing composer or LLM summarizer.

Order — STRICT:
1. composer.py produces complete deterministic briefing JSON (no LLM)
2. Verify all 9 fields populated and clinically correct
3. summarizer.py optionally adds Layer 3 LLM readable text

All 9 briefing JSON fields required:
trend_summary, medication_status, adherence_summary,
active_problems, overdue_labs, visit_agenda, urgent_flags,
risk_score, data_limitations

visit_agenda priority: urgent alerts, inertia, adherence concern,
                       overdue labs, active problems, next appt

Language: possible may suggest — NEVER definitive
LLM layer: log model_version + prompt_hash + generated_at
Briefing view: update briefings.read_at + write audit_events
