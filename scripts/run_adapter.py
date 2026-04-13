"""CLI script: convert an iEMR JSON file to a FHIR R4 Bundle.

Usage (from project root, with the aria conda environment active):
    python scripts/run_adapter.py --patient data/raw/iemr/1091_data.json
    python scripts/run_adapter.py --patient data/raw/iemr/some_patient.json --patient-id P002

Reads:  --patient  (path to iEMR JSON file)
Writes: data/fhir/bundles/<patient_id>_bundle.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

# Ensure the backend package is importable when run from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = PROJECT_ROOT / "backend"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from app.services.fhir.adapter import convert_iemr_to_fhir  # noqa: E402


def main() -> None:
    """Read iEMR JSON, convert to FHIR Bundle, write to disk, print summary."""
    parser = argparse.ArgumentParser(
        description="Convert an iEMR JSON file to a FHIR R4 Bundle."
    )
    parser.add_argument("--patient", required=True, help="Path to iEMR JSON file")
    parser.add_argument(
        "--patient-id",
        default=None,
        help="Patient ID (default: derived from filename, e.g. '1091' from '1091_data.json')",
    )
    args = parser.parse_args()

    input_path = Path(args.patient)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path

    patient_id: str = args.patient_id or input_path.stem.split("_")[0]

    output_dir = PROJECT_ROOT / "data" / "fhir" / "bundles"
    output_path = output_dir / f"{patient_id}_bundle.json"

    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {input_path} ...")
    with input_path.open(encoding="utf-8") as fh:
        iemr_data = json.load(fh)

    bundle = convert_iemr_to_fhir(iemr_data, patient_id=patient_id)

    os.makedirs(output_dir, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2)
    print(f"Bundle written to {output_path}")

    # Count resources by type
    counts: Counter[str] = Counter()
    for entry in bundle.get("entry", []):
        resource_type = entry.get("resource", {}).get("resourceType", "Unknown")
        counts[resource_type] += 1

    print("\nResource counts:")
    print(f"  {'ResourceType':<25} {'Count':>5}")
    print(f"  {'-'*25} {'-----':>5}")
    for resource_type, count in sorted(counts.items()):
        print(f"  {resource_type:<25} {count:>5}")
    print(f"  {'TOTAL':<25} {sum(counts.values()):>5}")


if __name__ == "__main__":
    main()
