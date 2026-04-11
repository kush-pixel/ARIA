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

## Ingestion Rules
Idempotent — safe to re-run on same Bundle.
clinical_context pre-computed at ingestion (one row per patient).
Parallel arrays: active_problems[n] == problem_codes[n].
historic_bp_systolic stores ALL clinic readings for generator anchor.

## Risk Tier Auto-Overrides (apply at ingestion)
CHF in problem_codes -> force risk_tier="high"
Stroke in problem_codes -> force risk_tier="high"
TIA in problem_codes -> force risk_tier="high"

## Audit
Every bundle_import creates audit_events row.
action="bundle_import", resource_type="Bundle", outcome="success"|"failure"