"""Optional Layer 3 LLM briefing summariser for ARIA.

Converts a deterministic Layer 1 briefing JSON payload into a 3-sentence
readable summary using the Anthropic claude-sonnet-4-20250514 model.

IMPORTANT: This module must only be called AFTER compose_briefing() has
produced and persisted a verified Layer 1 briefing.  Never run Layer 3
before Layer 1 is complete.

Every call logs model_version, prompt_hash, and generated_at to the
briefings row for audit traceability.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# TEMP: OpenAI testing override — revert to `import anthropic` when switching back
from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.briefing import Briefing
from app.services.briefing.llm_validator import validate_llm_output
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# TEMP: OpenAI gpt-4o-mini — revert to "claude-sonnet-4-20250514" when switching back
_MODEL_VERSION = "gpt-4o-mini"

# prompts/ lives at the project root — 4 levels above this file:
# briefing/ -> services/ -> app/ -> backend/ -> ARIA root
_PROMPT_PATH = Path(__file__).resolve().parents[4] / "prompts" / "briefing_summary_prompt.md"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _load_prompt_template() -> str:
    """Load the Layer 3 system prompt from prompts/briefing_summary_prompt.md.

    Returns:
        The prompt template as a string.

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Layer 3 prompt template not found at {_PROMPT_PATH}. "
            f"Ensure prompts/briefing_summary_prompt.md exists."
        )
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _compute_prompt_hash(prompt: str) -> str:
    """Return a SHA-256 hex digest of the prompt template string.

    Used to detect prompt changes across briefings for audit purposes.

    Args:
        prompt: The full prompt template text.

    Returns:
        64-character hex string (SHA-256 digest).
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _build_user_message(payload: dict[str, Any]) -> str:
    """Format the Layer 1 briefing payload as a structured LLM user message.

    Args:
        payload: The deterministic briefing JSON from composer.py.

    Returns:
        Formatted string to pass as the user message to the LLM.
    """
    active_problems = ", ".join(payload.get("active_problems", [])) or "None"
    overdue_labs = ", ".join(payload.get("overdue_labs", [])) or "None"
    urgent_flags = "; ".join(payload.get("urgent_flags", [])) or "None"
    risk_score = payload.get("risk_score")
    risk_str = f"{risk_score:.1f}/100" if risk_score is not None else "not calculated"

    return (
        f"Trend: {payload.get('trend_summary', 'N/A')}\n"
        f"Medication status: {payload.get('medication_status', 'N/A')}\n"
        f"Adherence: {payload.get('adherence_summary', 'N/A')}\n"
        f"Active problems: {active_problems}\n"
        f"Overdue labs: {overdue_labs}\n"
        f"Urgent flags: {urgent_flags}\n"
        f"Risk score: {risk_str}\n"
        f"Data limitations: {payload.get('data_limitations', 'N/A')}"
    )


# ── Public API ─────────────────────────────────────────────────────────────────

async def generate_llm_summary(
    briefing: Briefing,
    session: AsyncSession,
) -> Briefing:
    """Generate a 3-sentence LLM readable summary and update the briefing row.

    This is the optional Layer 3 step.  It must only be called after
    compose_briefing() has produced and persisted a verified Layer 1 briefing.

    Adds a 'readable_summary' key to briefing.llm_response and populates
    model_version, prompt_hash, and generated_at for the audit trail.

    Args:
        briefing: A persisted Layer 1 Briefing ORM instance.
        session: Active async SQLAlchemy session.

    Returns:
        The updated Briefing instance with Layer 3 fields populated.

    Raises:
        FileNotFoundError: If prompts/briefing_summary_prompt.md is missing.
        anthropic.APIError: If the Anthropic API call fails.
        ValueError: If the briefing has no llm_response payload.
    """
    if not briefing.llm_response:
        raise ValueError(
            f"Briefing {briefing.briefing_id} has no Layer 1 payload. "
            f"Run compose_briefing() before generate_llm_summary()."
        )

    logger.info(
        "Generating Layer 3 LLM summary for briefing=%s patient=%s",
        briefing.briefing_id,
        briefing.patient_id,
    )

    system_prompt = _load_prompt_template()
    prompt_hash = _compute_prompt_hash(system_prompt)
    user_message = _build_user_message(briefing.llm_response)

    client = OpenAI(api_key=settings.openai_api_key)

    # Attempt up to 2 times — retry once on validation failure before storing None
    summary_text: str | None = None
    for attempt in range(2):
        message = client.chat.completions.create(
            model=_MODEL_VERSION,
            max_tokens=256,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        candidate = message.choices[0].message.content.strip()

        result = await validate_llm_output(
            candidate,
            briefing.llm_response,
            str(briefing.briefing_id),
            briefing.patient_id,
            session,
        )

        if result.passed:
            summary_text = candidate
            break

        if attempt == 0:
            logger.warning(
                "Layer 3 validation failed (attempt 1) briefing=%s check=%s — retrying",
                briefing.briefing_id,
                result.failed_check,
            )
        else:
            logger.error(
                "Layer 3 validation failed (attempt 2) briefing=%s check=%s — storing None",
                briefing.briefing_id,
                result.failed_check,
            )

    # Merge summary into existing payload (summary_text is None if both attempts failed)
    updated_payload = dict(briefing.llm_response)
    updated_payload["readable_summary"] = summary_text

    briefing.llm_response = updated_payload
    briefing.model_version = _MODEL_VERSION
    briefing.prompt_hash = prompt_hash
    briefing.generated_at = datetime.now(tz=UTC)

    session.add(briefing)
    await session.commit()

    logger.info(
        "Layer 3 summary complete: briefing=%s model=%s prompt_hash=%s...",
        briefing.briefing_id,
        _MODEL_VERSION,
        prompt_hash[:8],
    )
    return briefing
