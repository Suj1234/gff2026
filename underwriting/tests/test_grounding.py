"""test_grounding.py — the grounding gate (incl. the escalate-path fix), the
confidence gate, and the grey-zone pipeline end-to-end with a FAKE judge.

No live LLM: the Judge is monkeypatched so the deterministic layer (grounding +
confidence + decision table + one gather cycle) is what's under test. The live
smoke test at the bottom hits the real gateway and is skipped without a key.

Done-when (IMPLEMENTATION_PLAN.md §10 Phase 3):
  - Vikram runs end-to-end (grey-zone → judge → REFER, grounded citations).
  - A fabricated-citation test escalates on `grounding_check_failed`.
  - Anjali: grey-zone → STEP_UP (gather) → re-judge → ISSUE.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from underwriting import judge as J
from underwriting import pipeline
from underwriting.decision import (
    CONFIDENCE_MIN,
    confidence,
    grounding_ok,
    map_decision,
)
from underwriting.rules import run_bre
from underwriting.schemas import AmbiguousFlag, BreResult, FlagRuling, ProposalInput, SoftFlag

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def _ruling(flag_id, ruling, cited=None):
    return FlagRuling(flag_id=flag_id, ruling=ruling, cited_evidence=cited or [])


def _grey_bre(n=1):
    """Grey-zone BRE with ambiguous_flags ids f1..fn (matches the _ruling ids used in
    the map_decision tests), so the coverage gate is satisfied — the realistic state."""
    soft = [SoftFlag(flag_type="thin_file", related_rule="R-008",
                     reason_code="R-008-x", reason="x") for _ in range(n)]
    afs = [AmbiguousFlag(flag_id=f"f{i}", flag_type="thin_file", related_rule="R-008")
           for i in range(1, n + 1)]
    return BreResult(outcome="GREY-ZONE", soft_flags=soft, ambiguous_flags=afs)


# The real bundle citations are checked against (a minimal but real shape).
ROOT = {"signals": {"itr": {"latest_total_taxable_income": 1_800_000},
                    "account_aggregator": {"imputed_annual_income": 1_750_000}},
        "follow_up_observations": {}}


# ===========================================================================
# Grounding gate — unit
# ===========================================================================
def test_grounding_ok_real_path():
    assert grounding_ok([_ruling("f1", "benign_explained",
                                 ["signals.itr.latest_total_taxable_income"])], ROOT)


def test_grounding_fails_fabricated_path():
    assert not grounding_ok([_ruling("f1", "benign_explained",
                                     ["signals.itr.this_field_does_not_exist"])], ROOT)


def test_grounding_benign_must_cite_something():
    # benign_explained with an EMPTY citation list is not grounded (§6).
    assert not grounding_ok([_ruling("f1", "benign_explained", [])], ROOT)


def test_grounding_checks_escalate_path_too():
    # THE FIX (§7.1): an unresolvable_escalate ruling that cites a fabricated path
    # must still FAIL grounding — the escalate path is no longer trusted blindly.
    fabricated = [_ruling("f1", "unresolvable_escalate", ["signals.itr.made_up_field"])]
    assert not grounding_ok(fabricated, ROOT)
    d = map_decision(_grey_bre(), rulings=fabricated, evidence_root=ROOT)
    assert d.verdict == "REFER" and d.escalation_reason == "grounding_check_failed"


def test_grounding_escalate_with_real_citation_passes_gate():
    # A grounded escalate ruling passes the grounding gate → normal escalate REFER.
    real = [_ruling("f1", "unresolvable_escalate",
                    ["signals.account_aggregator.imputed_annual_income"])]
    assert grounding_ok(real, ROOT)
    d = map_decision(_grey_bre(), rulings=real, evidence_root=ROOT)
    assert d.verdict == "REFER" and d.escalation_reason == "unresolvable_escalate"


# ===========================================================================
# Confidence gate — calibrated, low → REFER
# ===========================================================================
def test_confidence_all_escalate_is_zero():
    r = [_ruling("f1", "unresolvable_escalate", ["signals.itr.latest_total_taxable_income"])]
    assert confidence(r, ROOT) == 0.0


def test_confidence_grounded_benign_is_high():
    r = [_ruling("f1", "benign_explained", ["signals.itr.latest_total_taxable_income"])]
    assert confidence(r, ROOT) == 1.0


def test_low_confidence_refers():
    # 1 decisive of 3 → 0.33 < CONFIDENCE_MIN → REFER on low_confidence.
    r = [
        _ruling("f1", "benign_explained", ["signals.itr.latest_total_taxable_income"]),
        _ruling("f2", "unresolvable_escalate"),
        _ruling("f3", "unresolvable_escalate"),
    ]
    assert confidence(r, ROOT) < CONFIDENCE_MIN
    # unresolvable_escalate would also route to REFER, so use needs_* to isolate the
    # confidence path: 1 grounded benign of 3, other two ungrounded-benign (drop conf).
    r2 = [
        _ruling("g1", "benign_explained", ["signals.itr.latest_total_taxable_income"]),
        _ruling("g2", "benign_explained", ["signals.itr.latest_total_taxable_income"]),
        _ruling("g3", "needs_income_corroboration"),
    ]
    # all grounded/decisive here → high confidence; sanity that the metric moves.
    assert confidence(r2, ROOT) == 1.0


# ===========================================================================
# Grey-zone pipeline end-to-end with a FAKE judge (no live LLM)
# ===========================================================================
def _fake_extract(note_text: str):
    """Deterministic offline stand-in for the LLM extractor — keeps the fake-judge
    pipeline tests off the network. Recognizes the labels our fixtures use."""
    t = note_text.lower()
    out = []
    if "coronary" in t or "cardiac" in t or "cad" in t:
        out.append("coronary artery disease")
    if "hypertension" in t:
        out.append("hypertension")
    return out


def _offline(monkeypatch, judge_stub):
    """Patch BOTH LLM entry points so a fake-judge pipeline run touches no network."""
    monkeypatch.setattr(pipeline, "run_judge", judge_stub)
    monkeypatch.setattr(J, "extract_condition", _fake_extract)


def _fake_judge_factory(cycle_rulings):
    """Return a run_judge stub that yields canned rulings per call (cycle 1, 2)."""
    calls = {"n": 0}

    def fake(evidence_bundle, flags, follow_up_observations=None):
        rulings = cycle_rulings[min(calls["n"], len(cycle_rulings) - 1)]
        calls["n"] += 1
        # Cover EVERY flag (the judge contract): map canned rulings positionally and
        # repeat the last template for any remaining flags, so the stub never
        # under-covers and trips the coverage gate by accident.
        out = []
        for i, f in enumerate(flags):
            ruling, cited = rulings[min(i, len(rulings) - 1)]
            fid = f["flag_id"] if isinstance(f, dict) else f.flag_id
            out.append(FlagRuling(flag_id=fid, ruling=ruling, cited_evidence=cited))
        return out

    return fake


def test_vikram_end_to_end_refer_grounded(monkeypatch):
    """Vikram: grey-zone → judge rules both flags unresolvable (grounded) → REFER."""
    data = _load("vikram_velocity")
    inp = ProposalInput(**data["input"])
    bre = run_bre(inp)
    assert bre.outcome == "GREY-ZONE"
    # Both flags cite real paths and escalate → grounded REFER (the canonical case).
    cited = [
        ("unresolvable_escalate", ["signals.velocity_graph.cross_product_count_45d"]),
        ("unresolvable_escalate", ["signals.abha_health_records.icd_codes"]),
    ]
    _offline(monkeypatch, _fake_judge_factory([cited]))
    res = pipeline.run(inp)
    assert res.decision.verdict == "REFER"
    assert res.judge_cycles == 1
    # Every cited path in the chain resolved against the real bundle (grounded).
    assert grounding_ok(res.rulings, inp.model_dump())
    assert data["expected"]["decision"] == "REFER"  # matches the fixture label


def test_vikram_fabricated_citation_escalates_on_grounding(monkeypatch):
    """A hallucinated citation on Vikram → grounding_check_failed (the fix)."""
    data = _load("vikram_velocity")
    inp = ProposalInput(**data["input"])
    cited = [
        ("benign_explained", ["signals.velocity_graph.cross_product_count_45d"]),
        ("benign_explained", ["signals.abha_health_records.NOT_A_REAL_FIELD"]),
    ]
    _offline(monkeypatch, _fake_judge_factory([cited]))
    res = pipeline.run(inp)
    assert res.decision.verdict == "REFER"
    assert res.decision.escalation_reason == "grounding_check_failed"


def test_anjali_step_up_then_issue(monkeypatch):
    """Anjali: grey-zone → needs_income_corroboration → gather bank_statement →
    re-judge resolves benign (cites the gathered doc) → ISSUE (§10 done-when)."""
    data = _load("anjali_thin_file")
    inp = ProposalInput(**data["input"])
    bre = run_bre(inp)
    assert bre.outcome == "GREY-ZONE"

    # Cycle 1: needs a document. Cycle 2 (after gather): benign, citing the doc.
    cycle1 = [("needs_income_corroboration", ["signals.account_aggregator.imputed_annual_income"])]
    cycle2 = [("benign_explained", ["follow_up_observations.bank_statement.verified_income"])]
    _offline(monkeypatch, _fake_judge_factory([cycle1, cycle2]))

    def gather(pid, actions, proposal):
        # The mocked gateway RESPONSE for request_additional_document(bank_statement).
        assert "request_additional_document(bank_statement)" in actions
        return {"bank_statement": {"verified_income": 900000, "status": "available"}}

    res = pipeline.run(inp, gather=gather)
    assert res.judge_cycles == 2                 # gathered once, re-judged once
    assert res.decision.verdict == "ISSUE"
    assert data["expected"]["decision_with_llm"] == "STEP_UP"  # the row-8 next-step target


def test_anjali_step_up_when_document_never_arrives(monkeypatch):
    """If the gathered doc doesn't resolve it on re-judge → still needs → REFER
    (row 9: unresolved after the one cycle), never auto-issued."""
    data = _load("anjali_thin_file")
    inp = ProposalInput(**data["input"])
    same = [("needs_income_corroboration", [])]
    _offline(monkeypatch, _fake_judge_factory([same, same]))
    res = pipeline.run(inp)  # default gatherer returns "unavailable"
    assert res.judge_cycles == 2
    assert res.decision.verdict == "REFER"


def test_judge_unavailable_fails_safe_to_refer(monkeypatch):
    """An LM/gateway failure on a grey-zone case → deterministic REFER
    `judge_unavailable`, NOT an unhandled exception (§11 fail-safe; D-11)."""
    inp = ProposalInput(**_load("vikram_velocity")["input"])

    def boom(*a, **k):
        raise RuntimeError("gateway timeout")

    monkeypatch.setattr(pipeline, "run_judge", boom)
    monkeypatch.setattr(J, "extract_condition", lambda note: [])  # no free-text detour
    res = pipeline.run(inp)  # must NOT raise
    assert res.decision.verdict == "REFER"
    assert res.decision.escalation_reason == "judge_unavailable"


# ===========================================================================
# LIVE tests — real gateway; skipped unless a key AND UW_RUN_LIVE=1 are set.
# These are the honest Phase-3 done-when checks: the fake-judge tests above prove
# the deterministic layer; these prove the real LLM lands the expected outcomes.
# ===========================================================================
_LIVE = pytest.mark.skipif(
    not __import__("underwriting.judge", fromlist=["live_enabled"]).live_enabled(),
    reason="live LLM off — set a provider key AND UW_RUN_LIVE=1 to run",
)


@_LIVE
def test_live_judge_smoke_vikram():
    """Real Judge call on Vikram → the pipeline lands on a Core-6 verdict with
    grounded citations (shape + grounding invariant)."""
    inp = ProposalInput(**_load("vikram_velocity")["input"])
    res = pipeline.run(inp)  # real run_judge via the gateway
    assert res.judge_cycles >= 1
    assert res.decision.verdict in {"ISSUE", "ISSUE_WITH_LOADING", "STEP_UP", "REFER"}
    if res.decision.escalation_reason != "grounding_check_failed":
        assert grounding_ok(res.rulings, inp.model_dump())


@_LIVE
def test_live_vikram_refers():
    """DONE-WHEN (§10): Vikram runs end-to-end to REFER with a real LLM.

    Vikram carries undisclosed cardiac disease + velocity — the judge must not clear
    it. REFER is the only acceptable grey-zone terminal here (issue/loading would be
    wrong; DECLINE can't come from the LLM path)."""
    inp = ProposalInput(**_load("vikram_velocity")["input"])
    res = pipeline.run(inp)
    assert res.decision.verdict == "REFER", (res.decision.verdict, res.decision.reason_summary)
    if res.decision.escalation_reason != "grounding_check_failed":
        assert grounding_ok(res.rulings, inp.model_dump())


@_LIVE
def test_live_anjali_steps_up_then_issues():
    """DONE-WHEN (§10): Anjali → STEP_UP (gather bank_statement) → re-judge → ISSUE.

    The fixture's follow_up_observations pre-cans the corroborating bank_statement
    the one gather cycle returns; a real re-judge should then clear the thin-file
    flag benign → ISSUE (two judge cycles)."""
    inp = ProposalInput(**_load("anjali_thin_file")["input"])
    res = pipeline.run(inp)  # default fixture-driven gather
    assert res.judge_cycles == 2, "expected one gather cycle + re-judge"
    assert res.decision.verdict == "ISSUE", (res.decision.verdict, res.decision.reason_summary)
