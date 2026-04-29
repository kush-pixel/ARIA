"""Structured response parser for ARIA chatbot LLM output.

The LLM is instructed to return JSON. This module parses that JSON into
a ChatResponse dataclass. Falls back gracefully if the LLM returns plain text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_BLOCKED_ANSWER = "I can't reliably answer that from the available patient data."


@dataclass
class ChatResponse:
    """Parsed chatbot response ready for the API layer."""

    answer: str
    evidence: list[str] = field(default_factory=list)
    confidence: str = "medium"  # high | medium | low | no_data | blocked
    data_gaps: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None


def make_blocked_response(reason: str) -> ChatResponse:
    """Return a safe blocked response for the clinician.

    Args:
        reason: The failed check name from the validator.

    Returns:
        ChatResponse with blocked=True and safe fallback answer.
    """
    return ChatResponse(
        answer=_BLOCKED_ANSWER,
        confidence="blocked",
        blocked=True,
        block_reason=reason,
    )


def parse_response(raw: str, tools_used: list[str] | None = None) -> ChatResponse:
    """Parse the LLM's structured JSON response into a ChatResponse.

    Attempts JSON extraction first. Falls back to treating the whole string
    as a plain-text answer with no evidence, to handle malformed LLM output.

    Args:
        raw: Raw string from the LLM (expected to be JSON).
        tools_used: List of tool names called during this agent turn.

    Returns:
        ChatResponse dataclass.
    """
    tools_used = tools_used or []

    # Try to extract JSON from the response (handle markdown code blocks)
    json_str = raw.strip()
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", json_str)
    if code_block:
        json_str = code_block.group(1).strip()

    try:
        data = json.loads(json_str)
        return ChatResponse(
            answer=str(data.get("answer", raw)),
            evidence=list(data.get("evidence", [])),
            confidence=str(data.get("confidence", "medium")),
            data_gaps=list(data.get("data_gaps", [])),
            tools_used=tools_used,
        )
    except (json.JSONDecodeError, TypeError):
        logger.warning("LLM response was not valid JSON — treating as plain text")
        return ChatResponse(
            answer=raw.strip(),
            evidence=[],
            confidence="medium",
            data_gaps=[],
            tools_used=tools_used,
        )
