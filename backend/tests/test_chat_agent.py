"""Tests for the ARIA chatbot agent utilities."""

import pytest

from app.services.chat.agent import generate_suggested_questions
from app.services.chat.formatter import ChatResponse, make_blocked_response, parse_response


# ── generate_suggested_questions ──────────────────────────────────────────────

def test_suggested_questions_inertia():
    payload = {"urgent_flags": ["Therapeutic inertia: sustained elevated readings"], "adherence_summary": "", "overdue_labs": []}
    questions = generate_suggested_questions(payload)
    assert any("treatment review" in q.lower() for q in questions)


def test_suggested_questions_deterioration():
    payload = {"urgent_flags": ["Deterioration detected: step-change"], "adherence_summary": "", "overdue_labs": []}
    questions = generate_suggested_questions(payload)
    assert any("worsening" in q.lower() for q in questions)


def test_suggested_questions_adherence():
    payload = {
        "urgent_flags": [],
        "adherence_summary": "possible adherence concern: 72% overall",
        "overdue_labs": [],
    }
    questions = generate_suggested_questions(payload)
    assert any("missed doses" in q.lower() for q in questions)


def test_suggested_questions_always_includes_baseline():
    payload = {"urgent_flags": [], "adherence_summary": "", "overdue_labs": []}
    questions = generate_suggested_questions(payload)
    assert any("baseline" in q.lower() for q in questions)


def test_suggested_questions_max_four():
    payload = {
        "urgent_flags": ["inertia", "deterioration", "gap urgent"],
        "adherence_summary": "adherence concern",
        "overdue_labs": ["HbA1c", "eGFR"],
    }
    questions = generate_suggested_questions(payload)
    assert len(questions) <= 4


def test_suggested_questions_empty_payload():
    questions = generate_suggested_questions({})
    assert isinstance(questions, list)
    assert len(questions) >= 1  # always includes baseline


# ── parse_response ─────────────────────────────────────────────────────────────

def test_parse_valid_json():
    raw = '{"answer": "BP is elevated.", "evidence": ["164 mmHg (source: readings)"], "confidence": "high", "data_gaps": []}'
    response = parse_response(raw, tools_used=["get_patient_readings"])
    assert response.answer == "BP is elevated."
    assert response.confidence == "high"
    assert len(response.evidence) == 1
    assert response.tools_used == ["get_patient_readings"]
    assert not response.blocked


def test_parse_json_in_code_block():
    raw = '```json\n{"answer": "No data.", "evidence": [], "confidence": "no_data", "data_gaps": ["no readings"]}\n```'
    response = parse_response(raw)
    assert response.answer == "No data."
    assert response.confidence == "no_data"


def test_parse_plain_text_fallback():
    raw = "The patient has elevated BP."
    response = parse_response(raw)
    assert response.answer == "The patient has elevated BP."
    assert response.confidence == "medium"
    assert response.evidence == []


def test_parse_missing_fields_use_defaults():
    raw = '{"answer": "Some answer."}'
    response = parse_response(raw)
    assert response.answer == "Some answer."
    assert response.evidence == []
    assert response.data_gaps == []


# ── make_blocked_response ─────────────────────────────────────────────────────

def test_blocked_response_structure():
    blocked = make_blocked_response("guardrail:prescribe")
    assert blocked.blocked is True
    assert blocked.block_reason == "guardrail:prescribe"
    assert "can't reliably" in blocked.answer.lower()
    assert blocked.confidence == "blocked"
