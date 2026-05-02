# Demo Time-Shift Plan — Patient 1091

## Context

Demo day: **May 5, 2026**
Demo scenario date: **November 11, 2025** (the system is presented as if "today" is Nov 11, 2025)

Patient 1091's real data lives in the database with dates from 2008–2013. This plan shifts a specific window of that data forward by exactly **15 years (+5,479 days)** so the dashboard shows live, current-looking readings on demo day.

---

## Demo Scenario

| Role | Date | Meaning |
|------|------|---------|
| Previous clinic visit | **July 15, 2025** | Last time the patient saw their GP |
| Next appointment | **November 11, 2025** | The visit ARIA is generating the pre-visit briefing for |
| "Today" in the demo | November 11, 2025 | The GP opens ARIA to review the briefing |
| Home monitoring window | July 15 → Nov 11, 2025 | 119 days of home BP readings |
| Adaptive detection window | Last 90 days | ARIA's detectors analyse Aug 13 → Nov 11, 2025 |

---

## Why This Window

The source anchor is **Visit [27] — July 15, 2010 (clinic systolic = 162 mmHg)**.

| Option | Previous visit (shifted) | Gap | Avg BP | Adaptive window | Demo quality |
|--------|--------------------------|-----|--------|-----------------|-------------|
| Immediate predecessor | Oct 18, 2025 | 24 days | ~140 mmHg | 24 days | Weak — short, borderline BP |
| **Selected** | **Jul 15, 2025** | **119 days** | **~159 mmHg** | **90 days (maxed)** | **Strong — sustained elevation, inertia fires cleanly** |

Both visit anchors have elevated BP (start sys=162, end sys=156). The entire 90-day window sits well above the patient-adaptive inertia threshold (~138 mmHg). No medication change exists in this window in the original data, so the inertia detector fires correctly.

---

## Shift Parameters

| Parameter | Value |
|-----------|-------|
| Source window start | 2010-07-15 |
| Source window end | 2010-11-11 |
| Shift amount | +5,479 days (exactly +15 years) |
| Shifted window start | 2025-07-15 |
| Shifted window end | 2025-11-11 |

---

## Execution Steps

### Step 1 — Shift home readings (generated)
Table: `readings`
Filter: `patient_id = '1091'` AND `source = 'generated'` AND `effective_datetime` between `2010-07-15` and `2010-11-11`
Action: Add 5,479 days to `effective_datetime`

### Step 2 — Shift clinic readings in the window
Table: `readings`
Filter: `patient_id = '1091'` AND `source = 'clinic'` AND `effective_datetime` between `2010-07-15` and `2010-11-11`
Action: Add 5,479 days to `effective_datetime`

Clinic readings in this range (before shift → after shift):

| Original date | Shifted date | Systolic |
|--------------|-------------|---------|
| 2010-07-15 | 2025-07-15 | 162 |
| 2010-07-26 | 2025-07-26 | 116 |
| 2010-08-26 | 2025-08-26 | 125 / 136 |
| 2010-09-30 | 2025-09-30 | 136 |
| 2010-10-11 | 2025-10-11 | 138 |
| 2010-10-18 | 2025-10-18 | 124 |
| 2010-11-11 | 2025-11-11 | 156 |

### Step 3 — Shift medication confirmations
Table: `medication_confirmations`
Filter: `patient_id = '1091'` AND `scheduled_time` between `2010-07-15` and `2010-11-11`
Action: Add 5,479 days to `scheduled_time` and `confirmed_at` (where not NULL)

### Step 4 — Shift alerts
Table: `alerts`
Filter: `patient_id = '1091'` AND `triggered_at` between `2010-07-15` and `2010-11-11`
Action: Add 5,479 days to `triggered_at`, `delivered_at`, `acknowledged_at` (where not NULL)

### Step 5 — Shift briefings
Table: `briefings`
Filter: `patient_id = '1091'` AND `generated_at` between `2010-07-15` and `2010-11-11`
Action: Add 5,479 days to `generated_at`, `delivered_at`, `read_at` (where not NULL)

### Step 6 — Shift audit events
Table: `audit_events`
Filter: `patient_id = '1091'` AND `event_timestamp` between `2010-07-15` and `2010-11-11`
Action: Add 5,479 days to `event_timestamp`

### Step 7 — Update patient record
Table: `patients`
Filter: `patient_id = '1091'`
Action: Set `next_appointment = '2025-11-11'`

### Step 8 — Update clinical context
Table: `clinical_context`
Filter: `patient_id = '1091'`
Action:
- Set `last_visit_date = '2025-07-15'`
- Set `last_clinic_systolic = 162`
- Set `last_clinic_diastolic = 62`

### Step 9 — Trigger pattern recompute
Run a `pattern_recompute` processing job for patient 1091.
The detectors will evaluate the shifted readings and recompute:
- Gap status
- Therapeutic inertia (expected to fire — 90 days elevated, no med change)
- Deterioration
- Adherence pattern
- Risk score (Layer 2)

### Step 10 — Trigger briefing generation
Run a `briefing_generation` job for patient 1091 with `appointment_date = 2025-11-11`.
ARIA generates the pre-visit briefing the GP sees in the demo.

---

## What Does NOT Change

| Data | Reason |
|------|--------|
| All clinic readings before 2010-07-15 | Long-term BP baseline — threshold calculation depends on the full historic record |
| All generated readings outside the window | Untouched |
| The iEMR source file (`1091_data.json`) | Source of truth — never modified |
| Shadow mode ground truth | Shadow mode uses the iEMR file directly; it is unaffected |

---

## Expected ARIA Output After Shift

| Detector | Expected result |
|----------|----------------|
| Gap | None (readings present up to Nov 11) |
| Therapeutic inertia | **Fires** — 90 days avg ~159 mmHg, no med change in window |
| Deterioration | Possible — rising trend from Jul (162) → Aug dip → Oct–Nov elevation |
| Adherence | Pattern B likely — high adherence + elevated BP → treatment review case |
| Risk score | High tier (CHF override active) — score expected 65–80 range |

---

## Script

Implementation: `scripts/timeshift_demo.py`
Run with `--dry-run` to preview all rows that will be affected before committing.

```bash
# Preview (no changes made)
conda activate aria
python scripts/timeshift_demo.py --dry-run

# Execute
python scripts/timeshift_demo.py
```

---

## Reverting

To restore the original dates, run the same script with `--revert` (subtracts 5,479 days from all shifted rows).

```bash
python scripts/timeshift_demo.py --revert
```
