"""CLI script: ingest a FHIR R4 Bundle into the ARIA PostgreSQL database.

Usage (from project root, with the aria conda environment active):
    python scripts/run_ingestion.py
    python scripts/run_ingestion.py --bundle data/fhir/bundles/1091_bundle.json

Reads:   data/fhir/bundles/1091_bundle.json  (default)
Writes:  patients, clinical_context, readings, audit_events tables in Supabase
Needs:   DATABASE_URL set in backend/.env
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure the backend package is importable when run from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = PROJECT_ROOT / "backend"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

# Load DATABASE_URL and other settings from backend/.env before importing
# app modules that read config at import time.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_SRC / ".env")

from app.db.base import AsyncSessionLocal  # noqa: E402
from app.services.fhir.ingestion import ingest_fhir_bundle  # noqa: E402
from app.services.fhir.validator import validate_fhir_bundle  # noqa: E402


async def _run(bundle_path: Path) -> None:
    """Validate and ingest the bundle, then print a summary."""
    print(f"Loading bundle: {bundle_path}")
    try:
        bundle: dict = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: Cannot read bundle file — {exc}", file=sys.stderr)
        sys.exit(1)

    errors = validate_fhir_bundle(bundle)
    if errors:
        print("Bundle validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    print("Bundle validation passed.")

    async with AsyncSessionLocal() as session:
        summary = await ingest_fhir_bundle(bundle, session)

    print("\nIngestion complete:")
    print(f"  patient_id:                {summary['patient_id']}")
    print(f"  patients_inserted:         {summary['patients_inserted']}")
    print(f"  clinical_context_upserted: {summary['clinical_context_upserted']}")
    print(f"  readings_inserted:         {summary['readings_inserted']}")
    print(f"  audit_events_inserted:     {summary['audit_events_inserted']}")


def main() -> None:
    """Parse CLI args and run the ingestion."""
    parser = argparse.ArgumentParser(
        description="Ingest a FHIR R4 Bundle into the ARIA database."
    )
    parser.add_argument(
        "--bundle",
        default=str(PROJECT_ROOT / "data" / "fhir" / "bundles" / "1091_bundle.json"),
        help="Path to FHIR Bundle JSON file (default: data/fhir/bundles/1091_bundle.json)",
    )
    args = parser.parse_args()

    bundle_path = Path(args.bundle)
    if not bundle_path.exists():
        print(f"ERROR: Bundle file not found: {bundle_path}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(bundle_path))


if __name__ == "__main__":
    main()
