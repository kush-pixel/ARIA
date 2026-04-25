# ARIA Briefing Service Context
## composer.py (Layer 1) | summarizer.py (Layer 3)

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Files in This Directory

```
composer.py   — Layer 1: deterministic pre-visit briefing JSON (no LLM)
summarizer.py — Layer 3: optional LLM readable summary (runs AFTER composer)
```

---

## Execution Order — NON-NEGOTIABLE

```
compose_briefing()      ← Layer 1 — MUST complete and persist first
  ↓
generate_llm_summary()  ← Layer 3 — ONLY after Layer 1 row is in briefings table
```

Never call `generate_llm_summary()` before `compose_briefing()` has returned a persisted Briefing row.

---

## composer.py — Public API

```python
async def compose_briefing(
    session: AsyncSession,
    patient_id: str,
    appointment_date: date,
) -> Briefing
```

- Assembles the 9-field structured briefing JSON from DB data (readings, alerts, confirmations, clinical context).
- Writes a `Briefing` ORM row to the database and commits.
- Returns: The persisted `Briefing` ORM instance.
- Raises: `ValueError` if `patient_id` not found, or if clinical context row is missing.

Queries performed internally (do not repeat these in the handler):
```
select Patient where patient_id = ?
select ClinicalContext where patient_id = ?
select Reading where patient_id = ? AND effective_datetime >= (now - 28 days) ORDER BY ASC
select Alert where patient_id = ? AND acknowledged_at IS NULL ORDER BY triggered_at DESC
select MedicationConfirmation where patient_id = ? AND scheduled_time >= (now - 28 days)
```

---

## BriefingPayload — 10 Fields (stored as `briefings.llm_response` JSONB)

```python
{
    "trend_summary":        str,    # adaptive-window home BP pattern (14-90 days based on inter-visit interval)
                                    # + 90-day trajectory from historic_bp_systolic where available
    "medication_status":    str,    # current regimen and days since last med change
    "adherence_summary":    str,    # rate per medication + Pattern A/B/C classification
    "active_problems":      list[str],   # from clinical_context.active_problems[]
    "problem_assessments":  dict[str, str],  # {problem_name: most_recent_assessment} from clinical_context.problem_assessments
    "overdue_labs":         list[str],   # from clinical_context.overdue_labs[] + recent_labs abnormal flags
    "visit_agenda":         list[str],   # 3-6 prioritised agenda items
    "urgent_flags":         list[str],   # unacknowledged alert descriptions (gap | inertia | deterioration | adherence)
    "risk_score":           float | None,  # from patients.risk_score (Layer 2)
    "data_limitations":     str,    # home monitoring availability + cold-start notice if < 14 days enrolled
}
```

Layer 3 adds a 10th key at runtime:
```python
"readable_summary": str   # 3-sentence LLM output (not present until Layer 3 runs)
```

---

## visit_agenda Priority Order (exactly 3–6 items)

```
1. Urgent alerts (unacknowledged, gap_urgent | inertia | deterioration)
2. Therapeutic inertia (elevated 28-day avg + no med change > 7 days)
3. Adherence concern (overall confirmation rate < 80%)
4. Pending follow-ups (from overdue_labs[], language: "Pending follow-up: {item}.")
5. Active problems review
6. Confirm next monitoring review date
```

Items are truncated to 6 total. Language rules at code level:
- `"Pending follow-up: {lab}."` — not "Order overdue lab: {lab}."
- `"possible adherence concern"` — not "non-adherent"
- `"treatment review warranted"` — not "medication failure"

---

## composer.py — Private Helpers

```python
def _bp_category(systolic: float) -> str
```
Returns: `"normal range"` | `"elevated range"` | `"Stage 1 hypertension range"` | `"Stage 2 hypertension range"`.

```python
def _build_trend_summary(
    readings: list[Reading],
    last_clinic_systolic: int | None,
    last_clinic_diastolic: int | None,
    monitoring_active: bool,
) -> str
```
Returns `"No home monitoring..."` if `monitoring_active=False`. Otherwise returns `"28-day home average: {avg_sys}/{avg_dia} mmHg ({category}) based on {n} reading sessions. {trend_direction}."` Trend direction compares first 7 vs last 7 sessions.

```python
def _build_medication_status(
    current_medications: list[str] | None,
    last_med_change: date | None,
) -> str
```

```python
def _build_adherence_summary(
    confirmations: list[MedicationConfirmation],
    readings: list[Reading],
    monitoring_active: bool,
) -> str
```
Uses the Pattern A/B/C classification (same thresholds as adherence_analyzer):
- Pattern A: elevated BP + low adherence → "possible adherence concern"
- Pattern B: elevated BP + high adherence → "treatment review warranted"

```python
def _compute_adherence(
    confirmations: list[MedicationConfirmation],
) -> dict[str, dict[str, int]]
```
Returns `{medication_name: {"scheduled": int, "confirmed": int}}`.

```python
def _build_urgent_flags(alerts: list[Alert]) -> list[str]
```

```python
def _build_visit_agenda(
    urgent_flags: list[str],
    readings: list[Reading],
    confirmations: list[MedicationConfirmation],
    active_problems: list[str] | None,
    overdue_labs: list[str] | None,
    last_med_change: date | None,
    monitoring_active: bool,
) -> list[str]
```
Returns 3–6 items following the priority order above.

```python
def _build_data_limitations(readings: list[Reading], monitoring_active: bool) -> str
```
Returns one of:
- `"Patient is on EHR-only pathway. No home monitoring data available."`
- `"Home monitoring active but no readings received in past 28 days."`
- `"Limited home monitoring data: {n} sessions in past 28 days. ..."`  (n < 14)
- `"Home monitoring data available: {n} sessions over 28 days."`

Clinical threshold constants:
```python
_ELEVATED_SYSTOLIC: float = 140.0   # FALLBACK ONLY — composer must consume InertiaResult from Layer 1
                                      # Do NOT re-implement inertia logic here; pass inertia_result dict in
_ADHERENCE_THRESHOLD: float = 80.0
_INERTIA_DAYS: int = 7
_TREND_MIN_READINGS: int = 7
```

IMPORTANT: `_build_visit_agenda()` must NOT re-implement the inertia threshold check.
It must consume `inertia_result["inertia_detected"]` passed from the processor.
The duplicate inline check (`avg_sys >= _ELEVATED_SYSTOLIC`) carries the same hardcoded-140 bug as the detector.

---

## summarizer.py — Public API

```python
async def generate_llm_summary(
    briefing: Briefing,
    session: AsyncSession,
) -> Briefing
```

- `briefing`: A persisted `Briefing` ORM instance (returned by `compose_briefing()`).
- Adds `"readable_summary"` key to `briefing.llm_response` JSONB.
- Populates `briefing.model_version`, `briefing.prompt_hash`, and `briefing.generated_at`.
- Commits the update.
- Returns: The updated `Briefing` instance.
- Raises:
  - `FileNotFoundError`: `prompts/briefing_summary_prompt.md` is missing.
  - `anthropic.APIError`: LLM call failed (propagates to caller).
  - Skipped entirely if `settings.anthropic_api_key` is not set.

Model: `claude-sonnet-4-20250514`
Prompt file: `prompts/briefing_summary_prompt.md` (project root, 4 levels above this file)
Prompt hash: SHA-256 hex digest of the full prompt template text (stored in `briefings.prompt_hash`).

---

## summarizer.py — Private Helpers

```python
def _load_prompt_template() -> str
```
Reads `prompts/briefing_summary_prompt.md`. Raises `FileNotFoundError` if missing.

```python
def _compute_prompt_hash(prompt: str) -> str
```
Returns 64-character SHA-256 hex digest.

```python
def _build_user_message(payload: dict[str, Any]) -> str
```
Formats the Layer 1 briefing payload as a structured text block for the LLM user message.

---

## briefings DB Row — Key Columns

```
briefing_id         UUID PK (DB-generated)
patient_id          TEXT REFERENCES patients
appointment_date    DATE NOT NULL
llm_response        JSONB NOT NULL     — 9-field BriefingPayload (+ readable_summary after Layer 3)
generated_at        TIMESTAMPTZ        — set when compose_briefing() writes the row
delivered_at        TIMESTAMPTZ        — set by scheduler/worker (currently not implemented)
read_at             TIMESTAMPTZ        — set by GET /api/briefings/{id} + audit_events row
model_version       TEXT               — "claude-sonnet-4-20250514" (populated by Layer 3)
prompt_hash         TEXT               — SHA-256 of prompt template (populated by Layer 3)
```

---

## Dependencies

- `composer.py` → called by `worker/processor.py` (_handle_briefing_generation), after pattern_recompute completes
- `summarizer.py` → called by `worker/processor.py` immediately after `compose_briefing()` returns
- Layer 3 failure does NOT fail the job — the Layer 1 briefing is already persisted and usable
- `GET /api/briefings/{patient_id}` → reads briefings table, sets `read_at`, writes audit_event

---

## DO NOT

- Do NOT call `generate_llm_summary()` before `compose_briefing()` has committed a row
- Do NOT use `"non-adherent"` anywhere — always `"possible adherence concern"`
- Do NOT use `"medication failure"` anywhere — always `"treatment review warranted"`
- Do NOT use `"Order overdue lab:"` — always `"Pending follow-up:"`
- Do NOT recommend specific medications in any generated output
- Do NOT send briefings directly to patients — output is for clinician review only
- Do NOT use `session.query()` — SQLAlchemy 2.0 async uses `select()` only
- Do NOT re-implement the inertia threshold check in composer — consume inertia_result from Layer 1
- Do NOT hardcode trend_summary as "28-day" — window is adaptive (14-90 days based on inter-visit interval)
- Do NOT omit problem_assessments from the briefing payload — it's the 10th required field
