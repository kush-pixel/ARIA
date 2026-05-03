"""Unit tests for backend/app/services/briefing/medication_safety.py.

Run:
    cd backend && python -m pytest tests/test_medication_safety.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.briefing.medication_safety import check_interactions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ctx(
    medications: list[str] | None = None,
    problem_codes: list[str] | None = None,
) -> MagicMock:
    """Create a minimal mock ClinicalContext for interaction testing."""
    ctx = MagicMock()
    ctx.current_medications = medications or []
    ctx.problem_codes = problem_codes or []
    return ctx


# ---------------------------------------------------------------------------
# Rule 1 — NSAID + antihypertensive
# ---------------------------------------------------------------------------


class TestRule1NsaidAntihypertensive:
    """Tests for NSAID + antihypertensive interaction rule."""

    def test_nsaid_plus_ace_inhibitor_fires(self) -> None:
        ctx = _make_ctx(["ibuprofen 400mg", "lisinopril 10mg"])
        result = check_interactions(ctx)
        assert len(result) == 1
        assert result[0]["rule"] == "nsaid_antihypertensive"
        assert result[0]["severity"] == "warning"
        assert result[0]["comorbidity_amplified"] is False

    def test_nsaid_alone_does_not_fire(self) -> None:
        ctx = _make_ctx(["ibuprofen 400mg"])
        result = check_interactions(ctx)
        assert result == []

    def test_antihypertensive_alone_does_not_fire(self) -> None:
        ctx = _make_ctx(["lisinopril 10mg"])
        result = check_interactions(ctx)
        assert result == []

    def test_nsaid_plus_beta_blocker_fires(self) -> None:
        ctx = _make_ctx(["diclofenac 50mg", "atenolol 25mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "nsaid_antihypertensive" in rules


# ---------------------------------------------------------------------------
# Rule 2 — Triple whammy
# ---------------------------------------------------------------------------


class TestRule2TripleWhammy:
    """Tests for triple whammy interaction rule."""

    def test_triple_whammy_fires(self) -> None:
        ctx = _make_ctx(["ibuprofen 400mg", "lisinopril 10mg", "furosemide 40mg"])
        result = check_interactions(ctx)
        assert len(result) == 1
        assert result[0]["rule"] == "triple_whammy"
        assert result[0]["severity"] == "concern"

    def test_triple_whammy_supersedes_rule1(self) -> None:
        ctx = _make_ctx(["ibuprofen 400mg", "lisinopril 10mg", "furosemide 40mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "triple_whammy" in rules
        assert "nsaid_antihypertensive" not in rules

    def test_triple_whammy_critical_with_chf_and_ckd(self) -> None:
        ctx = _make_ctx(
            ["ibuprofen 400mg", "ramipril 5mg", "indapamide 2.5mg"],
            problem_codes=["I50.9", "N18.3"],
        )
        result = check_interactions(ctx)
        assert len(result) == 1
        assert result[0]["rule"] == "triple_whammy"
        assert result[0]["severity"] == "critical"
        assert result[0]["comorbidity_amplified"] is True

    def test_triple_whammy_concern_with_chf_only(self) -> None:
        ctx = _make_ctx(
            ["ibuprofen 400mg", "lisinopril 10mg", "furosemide 40mg"],
            problem_codes=["I50.9"],
        )
        result = check_interactions(ctx)
        assert result[0]["severity"] == "concern"
        assert result[0]["comorbidity_amplified"] is False


# ---------------------------------------------------------------------------
# Rule 3 — K-sparing diuretic + ACE/ARB
# ---------------------------------------------------------------------------


class TestRule3KSparingAceArb:
    """Tests for K-sparing diuretic + ACE inhibitor or ARB interaction rule."""

    def test_spironolactone_plus_lisinopril_fires(self) -> None:
        ctx = _make_ctx(["spironolactone 25mg", "lisinopril 10mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "k_sparing_ace_arb" in rules

    def test_k_sparing_plus_arb_fires(self) -> None:
        ctx = _make_ctx(["amiloride 5mg", "losartan 50mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "k_sparing_ace_arb" in rules

    def test_k_sparing_plus_ace_arb_warning_no_ckd(self) -> None:
        ctx = _make_ctx(["spironolactone 25mg", "lisinopril 10mg"])
        result = check_interactions(ctx)
        ix = next(r for r in result if r["rule"] == "k_sparing_ace_arb")
        assert ix["severity"] == "warning"
        assert ix["comorbidity_amplified"] is False

    def test_k_sparing_plus_ace_arb_escalates_with_ckd(self) -> None:
        ctx = _make_ctx(
            ["eplerenone 25mg", "candesartan 8mg"],
            problem_codes=["N18.4"],
        )
        result = check_interactions(ctx)
        ix = next(r for r in result if r["rule"] == "k_sparing_ace_arb")
        assert ix["severity"] == "concern"
        assert ix["comorbidity_amplified"] is True

    def test_co_amilofruse_classified_as_k_sparing(self) -> None:
        ctx = _make_ctx(["co-amilofruse 5/40mg", "ramipril 2.5mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "k_sparing_ace_arb" in rules


# ---------------------------------------------------------------------------
# Rule 4 — Beta-blocker + non-DHP CCB
# ---------------------------------------------------------------------------


class TestRule4BbNonDhpCcb:
    """Tests for beta-blocker + non-DHP CCB interaction rule."""

    def test_bisoprolol_plus_verapamil_fires(self) -> None:
        ctx = _make_ctx(["bisoprolol 5mg", "verapamil 80mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "bb_non_dhp_ccb" in rules

    def test_metoprolol_plus_diltiazem_fires(self) -> None:
        ctx = _make_ctx(["metoprolol 50mg", "diltiazem 60mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "bb_non_dhp_ccb" in rules

    def test_bb_non_dhp_ccb_always_concern(self) -> None:
        ctx = _make_ctx(["atenolol 25mg", "verapamil 80mg"])
        result = check_interactions(ctx)
        ix = next(r for r in result if r["rule"] == "bb_non_dhp_ccb")
        assert ix["severity"] == "concern"
        assert ix["comorbidity_amplified"] is False

    def test_beta_blocker_plus_dhp_ccb_does_not_fire(self) -> None:
        ctx = _make_ctx(["bisoprolol 5mg", "amlodipine 5mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "bb_non_dhp_ccb" not in rules


# ---------------------------------------------------------------------------
# Comorbidity escalation
# ---------------------------------------------------------------------------


class TestComorbidityEscalation:
    """Tests for comorbidity-driven severity escalation."""

    def test_rule1_escalates_to_concern_with_chf(self) -> None:
        ctx = _make_ctx(
            ["ibuprofen 400mg", "atenolol 25mg"],
            problem_codes=["I50.9"],
        )
        result = check_interactions(ctx)
        ix = next(r for r in result if r["rule"] == "nsaid_antihypertensive")
        assert ix["severity"] == "concern"
        assert ix["comorbidity_amplified"] is True

    def test_rule1_escalates_to_concern_with_ckd(self) -> None:
        ctx = _make_ctx(
            ["naproxen 250mg", "amlodipine 5mg"],
            problem_codes=["N18.3"],
        )
        result = check_interactions(ctx)
        ix = next(r for r in result if r["rule"] == "nsaid_antihypertensive")
        assert ix["severity"] == "concern"
        assert ix["comorbidity_amplified"] is True

    def test_rule1_stays_warning_without_comorbidity(self) -> None:
        ctx = _make_ctx(["ibuprofen 400mg", "atenolol 25mg"])
        result = check_interactions(ctx)
        ix = next(r for r in result if r["rule"] == "nsaid_antihypertensive")
        assert ix["severity"] == "warning"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and classification correctness."""

    def test_no_relevant_meds_returns_empty(self) -> None:
        ctx = _make_ctx(["atorvastatin 20mg", "metformin 500mg", "omeprazole 20mg"])
        assert check_interactions(ctx) == []

    def test_empty_medications_returns_empty(self) -> None:
        ctx = _make_ctx([])
        assert check_interactions(ctx) == []

    def test_case_insensitive_matching(self) -> None:
        ctx = _make_ctx(["IBUPROFEN 400mg", "LOSARTAN 50mg"])
        result = check_interactions(ctx)
        assert len(result) >= 1
        assert any(r["rule"] == "nsaid_antihypertensive" for r in result)

    def test_drugs_involved_contains_matched_names(self) -> None:
        ctx = _make_ctx(["ibuprofen 400mg", "lisinopril 10mg"])
        result = check_interactions(ctx)
        ix = result[0]
        assert "ibuprofen 400mg" in ix["drugs_involved"]
        assert "lisinopril 10mg" in ix["drugs_involved"]

    def test_multiple_rules_can_fire_simultaneously(self) -> None:
        # K-sparing + ACE/ARB (Rule 3) and BB + non-DHP CCB (Rule 4) can both fire
        ctx = _make_ctx(["spironolactone 25mg", "lisinopril 10mg", "bisoprolol 5mg", "verapamil 80mg"])
        result = check_interactions(ctx)
        rules = [r["rule"] for r in result]
        assert "k_sparing_ace_arb" in rules
        assert "bb_non_dhp_ccb" in rules
