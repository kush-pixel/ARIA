"""ARIA briefing services package.

Layer 1 (deterministic): composer.py — compose_briefing()
Layer 3 (optional LLM):  summarizer.py — generate_llm_summary()

Layer 3 must never run before Layer 1 is complete and verified.
"""

from app.services.briefing.composer import compose_briefing
from app.services.briefing.summarizer import generate_llm_summary

__all__ = ["compose_briefing", "generate_llm_summary"]
