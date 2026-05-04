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
        listen_url: str | None = None,   # raw asyncpg URL for LISTEN/NOTIFY (Fix 60)
    ) -> None

    async def run(self) -> None      # blocking loop; handle asyncio.CancelledError for SIGINT
    def stop(self) -> None           # signals loop to stop after current batch
    async def _process_batch(self) -> int  # claims and dispatches up to _BATCH_SIZE=10 jobs
    async def _start_listener(self) -> None  # opens asyncpg LISTEN on 'aria_jobs' channel (Fix 60)
```

`session_factory` is injectable for unit tests — pass a mock factory to avoid real DB connections.
`listen_url` is optional. When provided (production), opens a raw asyncpg connection and listens on
the `aria_jobs` channel. When omitted (unit tests / no DB), the `_wake_event` never fires externally
and the loop falls back to `_FALLBACK_POLL_SECONDS=60` timeout automatically — no listener started.
When queue is empty, waits on `_wake_event` (woken immediately by NOTIFY or after 60s fallback).

---

## processor.py — Job Status Transitions

```
queued  → running   : claimed via conditional UPDATE WHERE status='queued'
                      (rowcount guard prevents double-processing by concurrent workers)
running → succeeded : handler returned without raising
running → queued    : handler failed; retry_count < 3 — backoff and re-queue
                      retry_after set to now + backoff (30s / 120s / 480s)
running → dead      : handler failed; retry_count >= 3 — all retries exhausted

All transitions set started_at / finished_at.
queued (re-queue) and dead both set error_message with the exception repr.
```

`_process_batch()` skips jobs with `retry_after > now()` so backoff windows are respected.
`dead` jobs are never re-queued automatically — inspect via `GET /api/admin/dead-jobs`.

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
_POLL_INTERVAL_SECONDS: int = 30     # default constructor poll_interval; kept for compat
_FALLBACK_POLL_SECONDS: int = 60     # idle timeout when LISTEN/NOTIFY active (Fix 60)
_BATCH_SIZE: int = 10
_MAX_RETRIES: int = 3
_RETRY_BACKOFF_SECONDS: list[int] = [30, 120, 480]  # backoff for retry attempt 1, 2, 3
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
2. **Midnight UTC:** APScheduler cron calls `enqueue_pattern_recompute_sweep()` for all active patients. (Fix 10)
3. **Demo mode (on demand):** `POST /api/admin/trigger-scheduler` calls `enqueue_briefing_jobs()` directly. Guarded by `DEMO_MODE=true` in config.

Both functions send `SELECT pg_notify('aria_jobs', '')` inside their INSERT transaction before `commit()` (Fix 60). The worker's `_start_listener()` wakes immediately on notification instead of waiting up to 60 seconds.

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
status              TEXT           "queued" | "running" | "succeeded" | "failed" | "dead"
payload_ref         TEXT           file path for bundle_import jobs (NULL for others)
error_message       TEXT           set on failure / retry
retry_count         SMALLINT       NOT NULL DEFAULT 0 — incremented on each retry (Fix 40)
retry_after         TIMESTAMPTZ    NULL = pick up immediately; set during backoff window (Fix 40)
queued_at           TIMESTAMPTZ    DEFAULT NOW()
started_at          TIMESTAMPTZ    set when job is claimed
finished_at         TIMESTAMPTZ    set when job completes, fails, or is re-queued for retry
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
- Do NOT skip the midnight pattern_recompute sweep — risk scores and alert flags go stale without it (Fix 10)
- Do NOT run inertia/deterioration/adherence detectors for patients enrolled < 21 days (cold-start suppression)
- Do NOT re-queue a job after it has reached `status="dead"` — it requires manual inspection
- Do NOT call `compose_mini_briefing()` for inertia-only alerts — only gap_urgent and deterioration qualify (Fix 46)
- Do NOT trigger mini-briefing from `compose_briefing()` — only from `_handle_pattern_recompute()` (Fix 46)
- Do NOT place the `pg_notify` call after `commit()` — it must be inside the same transaction as the INSERTs (Fix 60)
