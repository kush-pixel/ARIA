# ARIA Generator Service Context

## GIT POLICY
Never git push, commit, or add.

## Purpose
Generate clinically realistic synthetic home BP readings and
medication confirmation events. source="generated" in readings table.

## SYNTHETIC DATA RULES — NON-NEGOTIABLE
SD 8-12 mmHg day-to-day (NEVER less than 5 — flat variance fails review)
Morning 5-10 mmHg HIGHER than evening — must be visible every week
NEVER exactly round numbers — add ±1-2 mmHg noise
Two readings per session differ 2-6 mmHg (reading 2 slightly lower)
Diastolic = systolic × 0.60-0.66
Heart rate 64-82 bpm, negatively correlated with systolic when beta-blocker present
Device outage = ABSENT ROWS (not null values) 1-2 episodes per 28 days
White-coat dip: 10-15 mmHg drop 3-5 days before appointment
Post-appointment return: readings rise back after dip

Patient A (patient 1091, anchor on historic_bp_systolic):
  Days 1-7:   Morning ~163 SD=8, evening 6-9 lower
  Days 8-14:  Drift to ~165, one missed evening Saturday
  Days 15-18: 164-167, outage days 16-17
  Days 19-21: Dip to 148-153 (pre-appointment white-coat)
  Days 22-28: Return 160-166, weekend misses days 25-26

Medication confirmations:
  91% adherence for Patient A demo
  confirmation_type="synthetic_demo", confidence="simulated"
  confirmed_at = scheduled_time + random(0-15 minutes)