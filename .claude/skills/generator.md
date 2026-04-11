# /generator — ARIA Synthetic Data Skill
Working on synthetic reading or confirmation generator.

CRITICAL RULES — clinical reviewer will catch violations:
- SD 8-12 mmHg day-to-day (NEVER less than 5)
- Morning HIGHER than evening by 5-10 mmHg — every single week
- NEVER round numbers — add +-1-2 mmHg noise to any base value
- Two readings per session differ 2-6 mmHg (reading 2 slightly lower)
- Diastolic = systolic x 0.60-0.66
- Device outage = ABSENT ROWS not nulls
- White-coat dip = 10-15 mmHg drop 3-5 days before appointment
- Anchor on real historic_bp_systolic from clinical_context
- Patient A scenario: follow CLAUDE.md days 1-28 exactly
- Adherence: 91% for Patient A, confirmation_type=synthetic_demo
