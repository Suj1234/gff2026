"""test_decision.py — Core-6 mapper, decide_next_step, and fixture end-to-end labels.

No AI. Rows 1-6 + 9-10 of §7 exercised deterministically; the 5 fixtures assert
their expected decision label (Phase 1 done-when).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from underwriting.decision import NextStep, decide_next_step, map_decision
from underwriting.rules import run_bre
from underwriting.schemas import AmbiguousFlag, BreResult, FlagRuling, ProposalInput, SoftFlag

FIX = Path(__file__).parent / "fixtures"


def _ruling(flag_id, ruling, cited=None):
    return FlagRuling(flag_id=flag_id, ruling=ruling, cited_evidence=cited or [])


# A minimal real bundle the grounding gate checks citations against (Phase 3).
_ROOT = {"signals": {"itr": {"latest_total_taxable_income": 1_800_000}}}
_CITE = ["signals.itr.latest_total_taxable_income"]  # a path that resolves in _ROOT


def _softflag(ft="thin_file", rule="R-008"):
    return SoftFlag(flag_type=ft, related_rule=rule, reason_code=f"{rule}-x", reason="x")


def _grey_for(rulings):
    """A grey-zone BreResult whose ambiguous_flags match the ruling flag_ids, so the
    coverage gate (one ruling per raised flag) is satisfied — the realistic state a
    real run produces (rules.py always emits ambiguous_flags alongside soft_flags)."""
    afs = [AmbiguousFlag(flag_id=r.flag_id, flag_type="thin_file", related_rule="R-008")
           for r in rulings]
    return BreResult(outcome="GREY-ZONE", soft_flags=[_softflag()], ambiguous_flags=afs)


# ===========================================================================
# decide_next_step (§7.1)
# ===========================================================================
def test_next_step_escalate_on_unresolvable():
    ns = decide_next_step([_ruling("a", "benign_explained"), _ruling("b", "unresolvable_escalate")], cycle=1)
    assert ns.kind == "ESCALATE" and ns.reason == "unresolvable_ruling"


def test_next_step_gather_on_needs():
    ns = decide_next_step([_ruling("a", "benign_explained"), _ruling("b", "needs_medical_check")], cycle=1)
    assert ns.kind == "GATHER_EVIDENCE" and ns.gather == ["b"]


def test_next_step_finalize_all_benign():
    assert decide_next_step([_ruling("a", "benign_explained")], cycle=1).kind == "FINALIZE"


def test_next_step_cycle2_unresolved_escalates():
    ns = decide_next_step([_ruling("a", "needs_income_corroboration")], cycle=2)
    assert ns.kind == "ESCALATE" and ns.reason == "max_cycles_exceeded"


# ===========================================================================
# Core-6 mapper — the non-LLM rows (1-6)
# ===========================================================================
def test_row1_decline_only_from_hard_gate():
    bre = BreResult(outcome="DECLINE", hard_gate="R-003", reason_codes=["R-003-identity-fraud"])
    d = map_decision(bre)
    assert d.verdict == "DECLINE" and d.escalation_reason == "R-003"


def test_row2_3_hard_refer():
    bre = BreResult(outcome="REFER", hard_gate="R-004", reason_codes=["R-004-aml-pep-sanctions"])
    assert map_decision(bre).verdict == "REFER"


def test_row4_postpone():
    bre = BreResult(outcome="POSTPONE", reason_codes=["POSTPONE-recent-event"])
    assert map_decision(bre).verdict == "POSTPONE"


def test_row5_issue_with_loading():
    bre = BreResult(outcome="LOADING", loading_pct=25.0)
    d = map_decision(bre)
    assert d.verdict == "ISSUE_WITH_LOADING" and d.loading_pct == 25.0


def test_row6_clean_issues():
    bre = BreResult(outcome="CLEAN")
    assert map_decision(bre).verdict == "ISSUE"


def test_grey_zone_without_llm_refers():
    # Phase-1 deterministic edge: unresolved grey-zone with no rulings -> REFER (never auto-issue/decline)
    bre = BreResult(outcome="GREY-ZONE", soft_flags=[_softflag()])
    d = map_decision(bre)
    assert d.verdict == "REFER" and d.escalation_reason == "grey_zone_unresolved_no_llm"


def test_beyond_matrix_step_up():
    # grey-zone with NO soft flags = pure beyond-matrix -> STEP_UP (row 5 edge)
    bre = BreResult(outcome="GREY-ZONE", soft_flags=[])
    assert map_decision(bre).verdict == "STEP_UP"


# ===========================================================================
# Core-6 mapper — the LLM-terminal rows (7-10), fed rulings directly
# ===========================================================================
def test_row7_all_benign_issues():
    # benign_explained MUST cite a resolving fact (grounding gate, §6) → ISSUE.
    rulings = [_ruling("flg_001", "benign_explained", _CITE)]
    d = map_decision(_grey_for(rulings), rulings=rulings, evidence_root=_ROOT)
    assert d.verdict == "ISSUE"


def test_row7_uncited_benign_refers_on_grounding():
    # A benign ruling that cites nothing is not trusted → REFER grounding_check_failed.
    rulings = [_ruling("flg_001", "benign_explained")]
    d = map_decision(_grey_for(rulings), rulings=rulings, evidence_root=_ROOT)
    assert d.verdict == "REFER" and d.escalation_reason == "grounding_check_failed"


def test_row8_needs_evidence_steps_up():
    rulings = [_ruling("flg_001", "needs_income_corroboration")]
    d = map_decision(_grey_for(rulings), rulings=rulings, evidence_root=_ROOT)
    assert d.verdict == "STEP_UP" and "bank_statement" in d.next_step


def test_row10_unresolvable_refers():
    rulings = [_ruling("flg_001", "unresolvable_escalate")]
    d = map_decision(_grey_for(rulings), rulings=rulings, evidence_root=_ROOT)
    assert d.verdict == "REFER" and d.escalation_reason == "unresolvable_escalate"


def test_coverage_gate_refers_on_skipped_flag():
    # Judge ruled 1 of 2 raised flags → coverage fails → REFER (never auto-issue).
    afs = [AmbiguousFlag(flag_id=f"flg_{i:03d}", flag_type="thin_file", related_rule="R-008")
           for i in (1, 2)]
    bre = BreResult(outcome="GREY-ZONE", soft_flags=[_softflag()], ambiguous_flags=afs)
    d = map_decision(bre, rulings=[_ruling("flg_001", "benign_explained", _CITE)], evidence_root=_ROOT)
    assert d.verdict == "REFER" and d.escalation_reason == "ruling_coverage_failed"


def test_decline_never_from_llm_path():
    # No combination of rulings can produce DECLINE (only row 1 / hard gate can).
    for ruling in ["benign_explained", "needs_income_corroboration",
                   "needs_medical_check", "needs_identity_reverification", "unresolvable_escalate"]:
        rulings = [_ruling("flg_001", ruling, _CITE)]
        d = map_decision(_grey_for(rulings), rulings=rulings, evidence_root=_ROOT)
        assert d.verdict != "DECLINE"


# ===========================================================================
# End-to-end fixture labels (Phase 1 done-when)
# ===========================================================================
FIXTURES = [
    "suresh_salaried_clean",
    "rohit_self_employed",
    "anjali_thin_file",
    "vikram_velocity",
    "fraud_deepfake",
    "priya_postpone",
]


@pytest.fixture(params=FIXTURES)
def fixture_case(request):
    data = json.loads((FIX / f"{request.param}.json").read_text(encoding="utf-8"))
    return request.param, data


def test_fixture_parses_against_schema(fixture_case):
    _, data = fixture_case
    inp = ProposalInput(**data["input"])
    assert inp.proposal_id == data["input"]["proposal_id"]


def test_fixture_end_to_end_decision(fixture_case):
    name, data = fixture_case
    inp = ProposalInput(**data["input"])
    bre = run_bre(inp)
    decision = map_decision(bre)  # no rulings -> Phase-1 deterministic result
    exp = data["expected"]
    assert bre.outcome == exp["expected_bre_outcome"], f"{name}: BRE outcome"
    assert decision.verdict == exp["decision"], f"{name}: decision"


def test_fixture_expected_flags_present(fixture_case):
    name, data = fixture_case
    exp = data["expected"]
    if "expected_flag_types" not in exp:
        return
    bre = run_bre(ProposalInput(**data["input"]))
    got = {f.flag_type for f in bre.soft_flags}
    for ft in exp["expected_flag_types"]:
        assert ft in got, f"{name}: expected soft flag '{ft}' not in {got}"


def test_fraud_fixture_declines_via_r003(fixture_case):
    name, data = fixture_case
    if name != "fraud_deepfake":
        return
    bre = run_bre(ProposalInput(**data["input"]))
    assert bre.outcome == "DECLINE" and bre.hard_gate == "R-003"


def test_clean_fixture_has_no_soft_flags(fixture_case):
    name, data = fixture_case
    if name != "suresh_salaried_clean":
        return
    bre = run_bre(ProposalInput(**data["input"]))
    assert bre.soft_flags == [] and bre.outcome == "CLEAN"
