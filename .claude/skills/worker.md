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
- Scheduler: appointment-day patients not yet briefed today
