# /api — ARIA API Skill
Working on FastAPI routes.

Routes:
GET  /api/patients                    patient list sorted by tier then risk_score DESC
POST /api/patients                    enrol patient with tier dropdown
POST /api/ingest                      FHIR Bundle import
POST /api/readings                    reading ingestion
GET  /api/briefings/{id}              latest briefing for patient
GET  /api/alerts                      unacknowledged alerts
POST /api/admin/trigger-scheduler     demo mode manual briefing trigger
GET  /api/shadow-mode                 ARIA vs physician agreement results (target >= 80%, achieved 94.3%)

Rules:
- Async everywhere: async def, await session.execute()
- Pydantic v2 response models for all endpoints
- Write audit_events on sensitive actions
- Error responses include request_id for correlation
