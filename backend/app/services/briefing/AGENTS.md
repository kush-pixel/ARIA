# ARIA Briefing Service Context

## GIT POLICY
Never git push, commit, or add.

## Purpose
composer.py: deterministic briefing JSON from Layer 1 + Layer 2 outputs
summarizer.py: optional LLM readable summary (Layer 3)

## Order (strict)
1. composer.py produces complete deterministic briefing JSON
2. Verify all 8 fields populated correctly
3. summarizer.py optionally adds LLM readable text on top

NEVER run summarizer before composer is complete and verified.

## Briefing JSON Fields (all required)
trend_summary, medication_status, adherence_summary,
active_problems, overdue_labs, visit_agenda, urgent_flags,
risk_score, data_limitations

visit_agenda priority:
  1. Urgent alerts
  2. Inertia flag
  3. Adherence concern
  4. Overdue labs
  5. Active problems
  6. Next appointment recommendation

## Clinical Language (always hedged)
"possible adherence concern" not "non-adherent"
"treatment review warranted" not "medication failure"
"suggest reviewing" not "must change"

## Layer 3 LLM (summarizer.py)
Model: claude-sonnet-4-20250514
Prompt: prompts/briefing_summary_prompt.md
Log: model_version, prompt_hash, generated_at in briefing row

## Audit
briefing_generation -> audit_events row
briefing_viewed -> update briefings.read_at + audit_events row
