# /worker — ARIA Worker Skill
Working on background processor or scheduler.

Rules:
- Poll processing_jobs WHERE status=queued every 30 seconds
- Status flow: queued -> running -> succeeded | failed
- Always update finished_at on completion or failure
- Check idempotency_key before processing any job
- Job types: pattern_recompute | briefing_generation | bundle_import
- pattern_recompute runs ALL Layer 1 detectors then Layer 2 scorer
- Demo mode: POST /api/admin/trigger-scheduler fires scheduler on demand

Scheduler — two jobs:
1. 7:30 AM UTC: enqueue_briefing_jobs()
   appointment-day patients (next_appointment::DATE = today) not yet briefed
   idempotency key: "briefing_generation:{patient_id}:{YYYY-MM-DD}"
2. Midnight UTC: enqueue_pattern_recompute_sweep()
   ALL monitoring_active=TRUE patients
   idempotency key: "pattern_recompute:{patient_id}:{YYYY-MM-DD}"
   ensures gap counters, risk scores, alerts stay current for non-appointment patients

Appointment date in briefing_generation: from patients.next_appointment.date()
  NOT parsed from idempotency_key[-10:] — that records wrong date when admin trigger fires

Cold-start suppression in pattern_recompute:
  if (now - enrolled_at).days < 21: skip inertia, deterioration, adherence detectors
  set data_limitations = "Patient enrolled N days ago — minimum 21-day monitoring period required"
  gap detector still runs
  21-day (not 14) avoids cliff-edge with Fix 28 adaptive window floor of 14 days

Layer 3 failure in briefing_generation: log WARNING, do NOT fail the job.

Alert upsert (_upsert_alert): set delivered_at = datetime.now(UTC) at insert time (Fix 30).
  Alert is delivered at creation — do not leave delivered_at NULL.
