# ARIA FHIR Service Context

## GIT POLICY
Never git push, commit, or add.

## Purpose
Layer 1 of data pipeline. Converts iEMR JSON to FHIR R4 Bundle (adapter.py)
then ingests the Bundle into all 8 PostgreSQL tables (ingestion.py).

## iEMR -> FHIR Mapping
PROBLEM -> Condition (ICD-10 in code.coding[0].code)
MEDICATIONS -> MedicationRequest (RxNorm from code_mappings)
VITALS -> Observation (LOINC 55284-4, effectiveDateTime=VITALS_DATETIME)
ALLERGY -> AllergyIntolerance
PLAN where PLAN_NEEDS_FOLLOWUP=YES -> ServiceRequest

CRITICAL: Use VITALS_DATETIME not ADMIT_DATE for BP observations.

## Non-FHIR Bundle Keys
_aria_med_history: list of medication history dicts produced by
  adapter._build_med_history(). Not a FHIR resource — prefixed _aria_
  to distinguish from FHIR resources. Consumed by ingestion.py and
  stored in clinical_context.med_history. Each entry:
  {name: str, rxnorm: str|null, date: str|null, activity: str|null}
  Sorted chronologically ascending. Deduped by (name, date, activity).

## Ingestion Rules
Idempotent — safe to re-run on same Bundle.
clinical_context pre-computed at ingestion (one row per patient).
Parallel arrays: active_problems[n] == problem_codes[n].
historic_bp_systolic stores ALL clinic readings for generator anchor.
med_history: full medication timeline from _aria_med_history bundle key.
  Stored as JSONB. Null if bundle has no _aria_med_history key.
Clinic readings use session="ad_hoc", source="clinic", submitted_by="clinic".
  The idempotency COUNT check filters on source="clinic".

## Risk Tier Auto-Overrides (apply at ingestion)
CHF in problem_codes -> force risk_tier="high"
Stroke in problem_codes -> force risk_tier="high"
TIA in problem_codes -> force risk_tier="high"

## Audit
Every bundle_import creates audit_events row.
action="bundle_import", resource_type="Bundle", outcome="success"|"failure"