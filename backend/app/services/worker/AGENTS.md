# ARIA Worker Service Context
## processor.py | scheduler.py

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Files in This Directory

```
processor.py  — WorkerProcessor: polls processing_jobs and dispatches job handlers
scheduler.py  — enqueue_briefing_jobs(): finds appointment-day patients, inserts jobs
```

---

## processor.py — WorkerProcessor Class

```python
class WorkerProcessor:
    def __init__(
        self,
        poll_interval: int = 30,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None

    async def run(self) -> None      # blocking loop; handle asyncio.CancelledError for SIGINT
    def stop(self) -> None           # signals loop to stop after current batch
    async def _process_batch(self) -> int  # claims and dispatches up to _BATCH_SIZE=10 jobs
```

`session_factory` is injectable for unit tests — pass a mock factory to avoid real DB connections.
Default `poll_interval=30` seconds. When queue is empty, sleeps; when jobs exist, drains without sleeping.

---

## processor.py — Job Status Transitions

```
queued → running   : claimed via conditional UPDATE WHERE status='queued'
                     (rowcount guard prevents double-processing by concurrent workers)
running → succeeded : handler returned without raising
running → failed    : handler raised any exception

All transitions set started_at / finished_at.
failed sets error_message with the exception repr.
```

---

## processor.py — 3 Job Types

| job_type            | Handler                      | What it does                                                    |
|---------------------|------------------------------|-----------------------------------------------------------------|
| bundle_import       | _handle_bundle_import        | Loads JSON from payload_ref, validates, calls ingest_fhir_bundle() |
| pattern_recompute   | _handle_pattern_recompute    | Runs Layer 1 (4 detectors) then Layer 2 (risk scorer)          |
| briefing_generation | _handle_briefing_generation  | compose_briefing() (Layer 1) then generate_llm_summary() (Layer 3) |

---

## processor.py — Module-level Job Handler Signatures

```python
async def _handle_bundle_import(job: ProcessingJob, session: AsyncSession) -> None
```
- `job.payload_ref` must point to a valid FHIR Bundle JSON file path on disk.
- Calls `validate_fhir_bundle()` first, then `ingest_fhir_bundle()`.
- Raises: `ValueError` (missing payload_ref or invalid bundle), `FileNotFoundError`, `json.JSONDecodeError`.

```python
async def _handle_pattern_recompute(job: ProcessingJob, session: AsyncSession) -> None
```
- `job.patient_id` must be set.
- Execution order is strict — do NOT parallelize:
  1. `run_gap_detector(session, patient_id)`
  2. `run_inertia_detector(session, patient_id)`
  3. `run_adherence_analyzer(session, patient_id)`
  4. `run_deterioration_detector(session, patient_id)`
  5. `compute_risk_score(patient_id, session)`
- Raises: `ValueError` if patient_id is missing.

```python
async def _handle_briefing_generation(job: ProcessingJob, session: AsyncSession) -> None
```
- `job.patient_id` and `job.idempotency_key` must be set.
- Appointment date sourced from `patients.next_appointment.date()` — NOT parsed from idempotency_key.
  Falls back to today if next_appointment is None (preserves demo-mode behavior).
  (Previously used `idempotency_key[-10:]` — this records wrong date when admin trigger fires off-day)
- Cold-start suppression: if `enrolled_at > now - 14 days`, skip inertia/deterioration/adherence
  detectors, set data_limitations = "Patient enrolled N days ago — minimum 14-day monitoring period required"
- Layer 3 failure is caught, logged as a WARNING, and does NOT fail the job — the Layer 1 briefing is already persisted.
- Raises: `ValueError` if patient_id or idempotency_key is missing or malformed.

---

## processor.py — Key Constants

```python
_POLL_INTERVAL_SECONDS: int = 30
_BATCH_SIZE: int = 10
```

Handler registry (add new job types here):
```python
_HANDLERS: dict[str, _JobHandler] = {
    "bundle_import": _handle_bundle_import,
    "pattern_recompute": _handle_pattern_recompute,
    "briefing_generation": _handle_briefing_generation,
}
```

---

## scheduler.py — Public API

```python
async def enqueue_briefing_jobs(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    target_date: date | None = None,
) -> int
```

- Finds patients with `monitoring_active=True` and `next_appointment::DATE = today` who do NOT already have a briefing for today.
- Inserts one `briefing_generation` job per qualifying patient using `ON CONFLICT DO NOTHING` on `idempotency_key`.
- Returns: Number of jobs inserted. Re-runs that hit conflict return 0 for those patients.
- `target_date` defaults to `date.today()` UTC — override in tests.

```python
async def enqueue_pattern_recompute_sweep(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    target_date: date | None = None,
) -> int
```

- Finds ALL `monitoring_active=True` patients (not just appointment-day patients).
- Inserts one `pattern_recompute` job per patient using `ON CONFLICT DO NOTHING`.
- Idempotency key: `"pattern_recompute:{patient_id}:{YYYY-MM-DD}"`
- Called by APScheduler at midnight UTC daily.
- Ensures gap counters, risk scores, inertia/deterioration flags stay current for non-appointment patients.
- Returns: Number of new jobs inserted.

Briefing idempotency key format: `"briefing_generation:{patient_id}:{YYYY-MM-DD}"`
Pattern recompute key format:    `"pattern_recompute:{patient_id}:{YYYY-MM-DD}"`

### Three Execution Paths

1. **7:30 AM UTC:** APScheduler cron calls `enqueue_briefing_jobs()`.
2. **Midnight UTC:** APScheduler cron calls `enqueue_pattern_recompute_sweep()` for all active patients.
3. **Demo mode (on demand):** `POST /api/admin/trigger-scheduler` calls `enqueue_briefing_jobs()` directly. Guarded by `DEMO_MODE=true` in config.

---

## scheduler.py — Private Helper

```python
def _briefing_idempotency_key(patient_id: str, appointment_date: date) -> str
```
Returns `f"briefing_generation:{patient_id}:{appointment_date.isoformat()}"`.

---

## processing_jobs DB Column Shape

```
job_id              UUID PK (DB-generated)
job_type            TEXT           "pattern_recompute" | "briefing_generation" | "bundle_import"
patient_id          TEXT REFERENCES patients (may be NULL for bundle_import)
idempotency_key     TEXT NOT NULL UNIQUE
status              TEXT           "queued" | "running" | "succeeded" | "failed"
payload_ref         TEXT           file path for bundle_import jobs (NULL for others)
error_message       TEXT           set on failure
queued_at           TIMESTAMPTZ    DEFAULT NOW()
started_at          TIMESTAMPTZ    set when job is claimed
finished_at         TIMESTAMPTZ    set when job completes or fails
created_by          TEXT           "system" | "scheduler" | "admin"
```

---

## Dependencies

- `processor.py` → imports handlers from `fhir/ingestion.py`, `fhir/validator.py`, all 4 pattern engine detectors, `pattern_engine/risk_scorer.py`, `briefing/composer.py`, `briefing/summarizer.py` (all deferred to avoid circular imports)
- `scheduler.py` → called by `app/api/admin.py` (POST /api/admin/trigger-scheduler) and `scripts/run_worker.py`
- Started at server launch via FastAPI `lifespan` context in `app/main.py`

---

## DO NOT

- Do NOT parallelise the 5 calls in `_handle_pattern_recompute` — order is strict (Layer 1 before Layer 2)
- Do NOT call `generate_llm_summary()` before `compose_briefing()` completes
- Do NOT fail a briefing_generation job because Layer 3 failed — Layer 3 failure must be logged and swallowed
- Do NOT use `session.query()` — SQLAlchemy 2.0 async uses `select()` only
- Do NOT expose `POST /api/admin/trigger-scheduler` without `DEMO_MODE=true` guard
- Do NOT use bare `except:` — catch specific exception types or `Exception` with explicit logging
- Do NOT parse appointment date from idempotency_key — query patients.next_appointment instead
- Do NOT skip the midnight pattern_recompute sweep — risk scores and alert flags go stale without it
- Do NOT run inertia/deterioration/adherence detectors for patients enrolled < 14 days (cold-start suppression)
