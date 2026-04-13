"""FHIR R4 Bundle structure validator for the ARIA ingestion pipeline.

All checks are non-raising — validation errors are returned as a list of
strings so callers can print them and exit gracefully rather than handling
unexpected exceptions.
"""

from __future__ import annotations

from typing import Any


def validate_fhir_bundle(bundle: dict[str, Any]) -> list[str]:
    """Validate that a dict is a well-formed FHIR R4 Bundle for ingestion.

    Performs three structural checks:
    1. ``bundle["resourceType"]`` must equal ``"Bundle"``.
    2. At least one Patient resource must appear in ``bundle["entry"]``.
    3. The Patient resource must have a non-empty ``"id"`` field.

    Args:
        bundle: Parsed FHIR Bundle dict.

    Returns:
        List of error strings describing every violation found.
        An empty list means the bundle passed all checks.
        Never raises.
    """
    errors: list[str] = []

    if not isinstance(bundle, dict):
        errors.append("bundle must be a dict")
        return errors

    if bundle.get("resourceType") != "Bundle":
        errors.append(
            f"bundle.resourceType must be 'Bundle', got {bundle.get('resourceType')!r}"
        )

    entries = bundle.get("entry", [])
    patient_resources = [
        e.get("resource", {})
        for e in entries
        if isinstance(e, dict)
        and isinstance(e.get("resource"), dict)
        and e["resource"].get("resourceType") == "Patient"
    ]

    if not patient_resources:
        errors.append("bundle must contain at least one Patient resource in entry")
    elif not patient_resources[0].get("id"):
        errors.append("Patient resource must have a non-empty 'id' field")

    return errors
