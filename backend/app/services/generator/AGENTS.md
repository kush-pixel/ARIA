# ARIA Generator Service Context
## reading_generator.py | confirmation_generator.py

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Files in This Directory

```
reading_generator.py      — synthetic full-timeline home BP readings (inter-visit interpolation, parametric baseline)
confirmation_generator.py — synthetic full-timeline medication confirmations (Beta-distributed adherence per interval)
```

---

## reading_generator.py — Public API

```python
async def generate_readings(
    patient_id: str,
    session: AsyncSession,
    scenario: str = SCENARIO_PATIENT_A,
) -> list[dict[str, Any]]
```

- `patient_id`: ARIA identifier (e.g. `"1091"`).
- `session`: Active async SQLAlchemy session. Caller owns lifecycle; this function does NOT commit.
- `scenario`: Must be `"patient_a"` (only scenario currently supported; future: parametric from patient data).
- Returns: List of dicts, one per reading. Each dict contains all `readings` table columns except `reading_id` and `created_at` (DB-generated). The caller inserts via `session.add_all([Reading(**r) for r in reading_dicts])`.
- Raises: `ValueError` for unknown scenario name; `SQLAlchemyError` on DB query failure.
- Idempotency: inserts use `ON CONFLICT DO NOTHING` on `(patient_id, effective_datetime, source)` — safe to re-run.

```python
async def generate_full_timeline_readings(
    patient_id: str,
    clinic_readings: list[Any],
    session: AsyncSession,
) -> list[dict[str, Any]]
```

- Generates synthetic readings spanning the entire care timeline (not just 28 days).
- For each consecutive pair of clinic readings, linearly interpolates baseline systolic between the two anchors with Gaussian noise (SD 8-12 mmHg), morning/evening offsets, device outages (1-2 episodes of 2-4 days per interval), and a white-coat dip in the 3 days before each clinic visit.
- Baseline computed as `median(historic_bp_systolic)` — NOT hardcoded 163.
- Falls back to `PATIENT_A_MORNING_MEAN=163.0` only when fewer than 2 clinic readings exist.
- Skips intervals where generated readings already exist (idempotency via unique index).
- Queries `clinical_context.historic_bp_systolic`, `historic_bp_dates`, and `current_medications`.

---

## reading_generator.py — Key Constants

```python
SCENARIO_PATIENT_A: str = "patient_a"

# Gaussian baseline — FALLBACK ONLY (fewer than 2 clinic readings)
# Primary baseline is median(historic_bp_systolic) from clinical_context
PATIENT_A_MORNING_MEAN: float = 163.0   # fallback; real patient 1091 median ≈ 134 mmHg
PATIENT_A_MORNING_SD: float = 8.0

# Systolic clip bounds for morning draw
MORNING_SYSTOLIC_CLIP_LOW: int = 145
MORNING_SYSTOLIC_CLIP_HIGH: int = 185

# Session UTC hours
MORNING_HOUR_UTC: int = 7
EVENING_HOUR_UTC: int = 21

# Session jitter
SESSION_JITTER_MINUTES_LOW: int = -15
SESSION_JITTER_MINUTES_HIGH: int = 15

# Morning/evening offset
MORNING_OFFSET_LOW: float = 0.0
MORNING_OFFSET_HIGH: float = 3.0
EVENING_OFFSET_LOW: float = 6.0
EVENING_OFFSET_HIGH: float = 7.0

# Anti-rounding noise (prevents .0 endings)
ANTI_ROUND_LOW: float = -1.5
ANTI_ROUND_HIGH: float = 1.5

# Second reading drop (ESH two-reading protocol)
READING2_DROP_LOW: float = 2.0
READING2_DROP_HIGH: float = 6.0

# Diastolic as fraction of systolic
DIASTOLIC_RATIO_LOW: float = 0.60
DIASTOLIC_RATIO_HIGH: float = 0.66

# Heart rate range and beta-blocker correction
HR_BASE_LOW: float = 64.0
HR_BASE_HIGH: float = 82.0
HR_CLAMP_LOW: int = 52
HR_CLAMP_HIGH: int = 95
HR_BETA_BLOCKER_COEFF: float = 0.3
HR_BETA_BLOCKER_THRESHOLD: int = 140
METOPROLOL_KEYWORD: str = "metoprolol"

# Phase targets
PHASE2_TARGET: float = 165.0
PHASE3_MEAN_LOW: float = 164.0
PHASE3_MEAN_HIGH: float = 167.0
PHASE4_TARGETS: list[float] = [158.0, 153.0, 149.0]   # days 19, 20, 21
PHASE5_MEAN_LOW: float = 160.0
PHASE5_MEAN_HIGH: float = 166.0

GENERATION_WINDOW_DAYS: int = 28  # used for legacy 28-day window only; full-timeline uses inter-visit intervals

# Fixed column values
GENERATED_SOURCE: str = "generated"
GENERATED_SUBMITTED_BY: str = "generator"
GENERATED_SESSION_MORNING: str = "morning"
GENERATED_SESSION_EVENING: str = "evening"
GENERATED_BP_POSITION: str = "seated"
GENERATED_BP_SITE: str = "left_arm"
GENERATED_CONSENT_VERSION: str = "1.0"
GENERATED_MEDICATION_TAKEN: str = "yes"
```

---

## Full-Timeline Generation — Patient 1091

Patient 1091 has 65 clinic readings spanning 2008-01-21 to 2013-09-26 (mean 133.8 mmHg, SD 16.2).
For each consecutive pair of clinic readings an inter-visit interval is generated.

Per-interval rules (apply to every interval):
- Baseline: linear interpolation between the two clinic BP anchors
- Daily noise: Gaussian SD 8-12 mmHg (NEVER less than 5)
- Morning: 5-10 mmHg higher than evening — every week without exception
- Device outage: 1-2 episodes of 2-4 days per interval — absent rows only
- White-coat dip: systolic drops 10-15 mmHg in 3-5 days before next clinic visit
- Post-clinic return: readings drift back to elevated baseline after dip

Demo briefing window (the visible 28-day period):
- Reflects the 2011-2013 elevated period (~158 mmHg avg)
- Inertia fires because avg > patient_threshold, no med change since 2013

Legacy 28-day scenario constants (PHASE1-5 targets) are kept for backward compatibility
with `generate_readings(scenario="patient_a")` but `generate_full_timeline_readings()`
derives targets from clinic BP pairs — NOT from hardcoded phase targets.

Device outages and missed sessions are represented as **absent rows** — null values are never generated.

---

## reading_generator.py — Private Helpers

```python
def _compute_baseline(historic_bp: list[int]) -> tuple[float, float]
```
Returns (mean, sd). Falls back to `(PATIENT_A_MORNING_MEAN, PATIENT_A_MORNING_SD)` when fewer than 2 clinic readings exist.

```python
def _anti_round(value: float) -> float
```
Adds ±1.5 mmHg noise and rounds to 1 decimal. Guarantees result never ends in `.0`.

```python
def _make_datetime(day_date: date, hour: int) -> datetime
```
Returns timezone-aware UTC datetime with ±15-minute jitter.

```python
def _build_reading(
    patient_id: str,
    scenario_sys: float,
    session_name: str,
    day_date: date,
    medications: list[str],
) -> dict[str, Any]
```
Pure function — no DB access. Applies ESH two-reading protocol, anti-rounding, diastolic ratio, and beta-blocker HR correction (if "metoprolol" in medications).

```python
def _patient_a_schedule() -> list[tuple[int, str, float]]
```
Returns `[(day_num, session_name, sys_target), ...]` for all 47 planned readings.

---

## confirmation_generator.py — Public API

```python
async def generate_confirmations(
    patient_id: str,
    session: AsyncSession,
) -> list[dict[str, Any]]
```

- Legacy 28-day window confirmation generator (kept for backward compatibility).
- Returns list of dicts, one per scheduled dose slot. Caller inserts via `session.add_all([MedicationConfirmation(**c) for c in confs])`.
- A `confirmed_at=None` represents a missed dose. Every scheduled slot produces exactly one dict.
- Does NOT commit. Caller owns session lifecycle.
- Patient 1091: **420 total scheduled doses, 377 confirmed (89.8%)** across all 14 medications over 28 days.

```python
async def generate_full_timeline_confirmations(
    patient_id: str,
    clinic_readings: list[Any],
    med_history: list[dict[str, Any]],
    session: AsyncSession,
) -> list[dict[str, Any]]
```

- Generates confirmations spanning the entire care timeline for every medication active during each inter-visit interval.
- `med_history`: from `clinical_context.med_history` — determines which medications were active at each point in time. Only generates confirmations for a medication from the date it was added, not retroactively.
- Per-interval adherence drawn from Beta distribution anchored near patient's overall adherence (≈91% for patient 1091) with ±10-15 percentage point interval-to-interval variation.
- Idempotency: uses `ON CONFLICT DO NOTHING` on `(patient_id, medication_name, scheduled_time)` — safe to re-run.
- Does NOT commit. Caller owns session lifecycle.

---

## confirmation_generator.py — Key Constants

```python
ADHERENCE_RATE_WEEKDAY: float = 0.95   # Monday–Friday
ADHERENCE_RATE_WEEKEND: float = 0.78   # Saturday (weekday()==5), Sunday (==6)

CONFIRMATION_CONFIDENCE: str = "simulated"
CONFIRMATION_TYPE: str = "synthetic_demo"

GENERATION_WINDOW_DAYS: int = 28  # used for legacy 28-day window only; full-timeline uses inter-visit intervals

# Jitter around scheduled time
JITTER_LOW: int = -15
JITTER_HIGH: int = 15

# Delay between scheduled_time and confirmed_at for taken doses (minutes)
CONFIRM_DELAY_LOW: int = 0
CONFIRM_DELAY_HIGH: int = 15

# Dosing frequency UTC hours
QD_HOURS: list[int] = [8]
BID_HOURS: list[int] = [8, 20]
TID_HOURS: list[int] = [8, 14, 20]
QID_HOURS: list[int] = [7, 12, 17, 22]
```

---

## confirmation_generator.py — Private Helpers

```python
def _determine_hours(med_name: str) -> list[int]
```
Matches frequency keywords case-insensitively ("qid", "four", "tid", "three", "bid", "twice"). Falls back to `QD_HOURS = [8]` if no match.

```python
def _make_scheduled_time(day_date: date, hour: int) -> datetime
```
Timezone-aware UTC scheduled time with ±15-minute jitter. Mirrors `_make_datetime` in `reading_generator`.

```python
def _build_confirmation(
    patient_id: str,
    med_name: str,
    rxnorm_code: str | None,
    scheduled_time: datetime,
) -> dict[str, Any]
```
Applies the adherence rate roll. Confirmed doses set `confirmed_at` and `minutes_from_schedule`; missed doses leave both as `None`.

---

## DB Column Shape — readings (generated rows)

```
reading_id          UUID PK (DB-generated)
patient_id          TEXT
systolic_1          SMALLINT         first reading
diastolic_1         SMALLINT
heart_rate_1        SMALLINT
systolic_2          SMALLINT         second reading (always present for generated)
diastolic_2         SMALLINT
heart_rate_2        SMALLINT
systolic_avg        NUMERIC(5,1)     anti-rounded average
diastolic_avg       NUMERIC(5,1)
heart_rate_avg      NUMERIC(5,1)
effective_datetime  TIMESTAMPTZ      UTC with ±15-min jitter
session             TEXT             "morning" | "evening"
source              TEXT             "generated"
submitted_by        TEXT             "generator"
bp_position         TEXT             "seated"
bp_site             TEXT             "left_arm"
consent_version     TEXT             "1.0"
medication_taken    TEXT             "yes"
created_at          TIMESTAMPTZ (DB-generated)
```

## DB Column Shape — medication_confirmations (generated rows)

```
confirmation_id     UUID PK (DB-generated)
patient_id          TEXT
medication_name     TEXT
rxnorm_code         TEXT | NULL
scheduled_time      TIMESTAMPTZ
confirmed_at        TIMESTAMPTZ | NULL   NULL = missed dose
confirmation_type   TEXT | NULL          "synthetic_demo" | NULL (missed)
confidence          TEXT                 "simulated"
minutes_from_schedule SMALLINT | NULL
created_at          TIMESTAMPTZ
```

---

## Dependencies

- `reading_generator.py` → called by `scripts/run_generator.py` and directly from admin scripts
- `confirmation_generator.py` → called by `scripts/run_generator.py`
- Both query `clinical_context` (patient_id FK must exist in patients before calling)

---

## DO NOT

- Do NOT generate null values for device outage days — use absent rows (no dict in the list)
- Do NOT generate exactly-round readings (e.g. 160.0, 140.0) — `_anti_round` prevents this
- Do NOT set `systolic_2 >= systolic_1` — second reading must be strictly lower
- Do NOT use `session.query()` — SQLAlchemy 2.0 async uses `select()` only
- Do NOT use hardcoded 163 mmHg as primary baseline — use `median(historic_bp_systolic)` from clinical_context
- Do NOT generate only 28 days of data — full-timeline generation is required; 28-day is legacy
- Do NOT generate confirmations for a medication before its MED_DATE_ADDED in med_history
- Do NOT set `confidence="self_report"` for generated confirmations — must be `"simulated"`
- Do NOT set day-to-day systolic SD below 5 mmHg — flat variance is clinically wrong
- Do NOT use batch-level idempotency (COUNT check) — use per-row ON CONFLICT DO NOTHING
