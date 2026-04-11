# ARIA v4.3 — Global Codex Context

## FIRST ACTION — READ STATUS.md
Before starting any task, read STATUS.md in the project root.
It contains what is already built, schema changes from the spec,
plan changes, and known issues discovered during implementation.
Do not assume the spec is current — STATUS.md overrides it.

## Adaptive Real-time Intelligence Architecture | IIT CS 595 | Spring 2026

## What ARIA Does
Between-visit clinical intelligence platform for hypertension.
Delivers a structured pre-visit briefing to GPs at 7:30 AM
based on 28-day home BP monitoring and longitudinal EHR context.

## GIT POLICY — ABSOLUTE
Never git push. Never git commit. Never git add.
Tell the user what files changed. User runs all git commands.

## Three-Layer AI Architecture
Layer 1: Deterministic rules (gap, inertia, adherence, deterioration)
Layer 2: Weighted risk scoring (0.0-100.0 priority score per patient)
Layer 3: LLM readable summary (optional, on top of Layer 1 output)

## Tech Stack
Python 3.11, FastAPI, SQLAlchemy 2.0 async, Pydantic v2
PostgreSQL via Supabase (asyncpg driver)
Next.js 14, TypeScript strict, Tailwind, recharts
Anthropic claude-sonnet-4-20250514 (Layer 3 only)

## Database: 8 Tables
patients (includes risk_score column for Layer 2)
clinical_context, readings, medication_confirmations
alerts, briefings, processing_jobs, audit_events

## Service Architecture
fhir/           iEMR->FHIR adapter + Bundle ingestion
generator/      synthetic home BP + medication confirmations
pattern_engine/ Layer 1 detectors + Layer 2 risk_scorer
briefing/       Layer 1 deterministic JSON + Layer 3 LLM summary
worker/         processing_jobs polling loop + scheduler

## Clinical Boundary
Never recommend specific medications.
Language: "possible adherence concern" not "non-adherent".

## Code Standards
SQLAlchemy 2.0 async only (no session.query())
Pydantic v2 only (model_config = SettingsConfigDict)
Type hints required. Docstrings required. ruff must pass.
