# ARIA Generator Service Context
## reading_generator.py | confirmation_generator.py

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Files in This Directory

```
reading_generator.py      — synthetic 28-day home BP readings for demo patients
confirmation_generator.py — synthetic 28-day medication confirmations for demo patients
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
- `scenario`: Must be `"patient_a"` (only scenario currently supported).
- Returns: List of dicts, one per reading. Each dict contains all `readings` table columns except `reading_id` and `created_at` (DB-generated). The caller must insert via `session.add_all([Reading(**r) for r in reading_dicts])`.
- Raises: `ValueError` for unknown scenario name; `SQLAlchemyError` on DB query failure.

Queries `clinical_context.historic_bp_systolic` and `current_medications` to anchor generation on real EHR data. If fewer than 2 clinic readings exist, falls back to `PATIENT_A_MORNING_MEAN=163.0`.

---

## reading_generator.py — Key Constants

```python
SCENARIO_PATIENT_A: str = "patient_a"

# Gaussian baseline (anchored on clinic data, falls back to these)
PATIENT_A_MORNING_MEAN: float = 163.0
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

GENERATION_WINDOW_DAYS: int = 28

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

## Patient A 28-Day Schedule — 47 Readings Total

```
Phase 1 (days  1-7):  Baseline Gaussian(163, SD=8). Both sessions. 14 entries.
Phase 2 (days  8-14): Inertia drift toward 165. One evening missed (random day 12 or 13). 13 entries.
Phase 3 (days 15-18): Elevation 164-167. Days 16-17 absent (device outage). Days 15 and 18 only. 4 entries.
Phase 4 (days 19-21): White-coat dip targets [158, 153, 149]. Both sessions. 6 entries.
Phase 5 (days 22-28): Return 160-166. Days 25-26 absent (weekend miss). 10 entries.
Total: 47 readings.
```

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

- Returns list of dicts, one per scheduled dose slot. Caller inserts via `session.add_all([MedicationConfirmation(**c) for c in confs])`.
- A `confirmed_at=None` represents a missed dose. Every scheduled slot produces exactly one dict — absent rows are never generated.
- Does NOT commit. Caller owns session lifecycle.

Patient 1091: **420 total scheduled doses, 377 confirmed (89.8%)** across all 14 medications over 28 days.

---

## confirmation_generator.py — Key Constants

```python
ADHERENCE_RATE_WEEKDAY: float = 0.95   # Monday–Friday
ADHERENCE_RATE_WEEKEND: float = 0.78   # Saturday (weekday()==5), Sunday (==6)

CONFIRMATION_CONFIDENCE: str = "simulated"
CONFIRMATION_TYPE: str = "synthetic_demo"

GENERATION_WINDOW_DAYS: int = 28

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
- Do NOT hardcode systolic targets without checking `PATIENT_A_*` constants
- Do NOT set `confidence="self_report"` for generated confirmations — must be `"simulated"`
- Do NOT set day-to-day systolic SD below 5 mmHg — flat variance is clinically wrong
