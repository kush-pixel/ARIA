# ARIA Worker Service Context

## GIT POLICY
Never git push, commit, or add.

## Purpose
processor.py: polls processing_jobs every 30 seconds, executes jobs
scheduler.py: enqueues briefing jobs for appointment-day patients at 7:30 AM

## Worker Loop
Poll: SELECT * FROM processing_jobs WHERE status='queued'
      ORDER BY queued_at ASC LIMIT 10
Per job:
  UPDATE status='running', started_at=NOW()
  Execute job_type handler
  UPDATE status='succeeded', finished_at=NOW()
  On error: UPDATE status='failed', error_message=str(e)

Job types:
  pattern_recompute   -> run all Layer 1 detectors + Layer 2 scorer
  briefing_generation -> compose briefing JSON for patient + date
  bundle_import       -> process FHIR Bundle from payload_ref path

Idempotency: always check idempotency_key before processing.

## Scheduler
7:30 AM: find patients with next_appointment::DATE = CURRENT_DATE
          AND no briefing exists for today
          Enqueue briefing_generation for each

Demo mode: POST /api/admin/trigger-scheduler fires scheduler on demand.
This is the primary trigger during demo — do not rely on cron timing.