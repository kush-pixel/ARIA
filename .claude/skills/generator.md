# /generator — ARIA Synthetic Data Skill
Working on synthetic reading or confirmation generator.

CRITICAL RULES — clinical reviewer will catch violations:
- SD 8-12 mmHg day-to-day (NEVER less than 5)
- Morning HIGHER than evening by 5-10 mmHg — every single week
- NEVER round numbers — add +-1-2 mmHg noise to any base value
- Two readings per session differ 2-6 mmHg (reading 2 slightly lower)
- Diastolic = systolic x 0.60-0.66
- Device outage = ABSENT ROWS not nulls (1-2 per inter-visit interval)
- White-coat dip = 10-15 mmHg drop 3-5 days before appointment
- Anchor on median(historic_bp_systolic) from clinical_context — NOT hardcoded 163
- Fallback to PATIENT_A_MORNING_MEAN=163.0 only if < 2 clinic readings exist

Scope — FULL CARE TIMELINE (not 28 days):
- generate_full_timeline_readings(): inter-visit BP for each consecutive clinic pair
- generate_full_timeline_confirmations(): confirmations per medication active in each interval
- Idempotency: ON CONFLICT DO NOTHING on (patient_id, effective_datetime, source) for readings
              ON CONFLICT DO NOTHING on (patient_id, medication_name, scheduled_time) for confirmations

Confirmations:
- Derive active medications from med_history JSONB timeline (not current_medications)
- Per-interval adherence: Beta distribution anchored near patient overall (≈91%), ±10-15% variation
- confirmation_type=synthetic_demo, confidence=simulated
- Do NOT generate before MED_DATE_ADDED for each medication
