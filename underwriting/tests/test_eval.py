"""test_eval.py — the labeled regression harness (§10 Phase 6 done-when).

Proves the three things the eval harness must do:
  1. The whole fixture set replays green with the correct judge (baseline is CLEAN,
     triad all zero).
  2. A seeded BAD change (a judge that clears everything, and separately a bad rule)
     is CAUGHT by the harness before prod — the §10 Phase 6 done-when.
  3. The triad is measured correctly: false_benign vs over_escalation vs
     grounding_hallucination each light up on the right kind of wrong answer.

Network-free by design: the grey-zone fixtures are replayed through an injected
deterministic judge (same fake shape the other tests use). The real-LLM cached
replay is opt-in (`UW_EVAL_MODE=1`) and exercised by the live tests, not here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from underwriting import eval as E
from underwriting import judge as J
from underwriting import pipeline
from underwriting.schemas import FlagRuling, ProposalInput

FIX = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# The GOOD offline judge — now the ONE shared helper (underwriting/tests/_fakejudge.py),
# reproducing each fixture's expected terminal (rohit/vikram → REFER, anjali → 2-cycle
# ISSUE). Consolidated from the copy that used to live here (repo L-A1).
# ---------------------------------------------------------------------------
from ._fakejudge import assert_flags_known, fake_extract, make_fake_judge


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(pipeline, "run_judge", make_fake_judge())
    monkeypatch.setattr(J, "extract_condition", fake_extract)


# ---------------------------------------------------------------------------
# 1. Baseline: the whole labeled set replays CLEAN.
# ---------------------------------------------------------------------------
def test_all_fixture_flags_are_known_to_the_stub():
    """Loud guard (the hole the audit found — test_pipeline was guarded, eval was
    NOT): every grey-zone flag any fixture raises must have a ruling in the shared
    stub, else it silently defaults to escalate → REFER and the replay below can
    pass for the wrong reason. A new flag (e.g. cross_signal_moral_hazard) fails
    HERE with a clear message instead of hiding."""
    for p in sorted(FIX.glob("*.json")):
        inp = ProposalInput(**json.loads(p.read_text(encoding="utf-8"))["input"])
        assert_flags_known(inp, p.stem)


def test_baseline_replay_is_clean():
    rep = E.replay()
    assert rep.total >= 5, "the claim-master seed should hold every fixture"
    assert rep.clean, E._scoreboard(rep)
    assert rep.false_benign == 0
    assert rep.over_escalation == 0
    assert rep.grounding_hallucination == 0


# ---------------------------------------------------------------------------
# 2. Seeded regressions are CAUGHT (§10 Phase 6 done-when: the suite fails a bad
#    rule/prompt/model change BEFORE prod).
# ---------------------------------------------------------------------------
def test_seeded_bad_prompt_is_caught(monkeypatch):
    """A regressed judge that clears every flag as benign (an over-eager prompt) —
    the harness must go RED, and specifically flag false_benign on the cases that
    should NOT have cleared (rohit/vikram)."""
    def clear_everything(evidence_bundle, flags, follow_up_observations=None):
        # benign_explained WITH a grounded citation, so it isn't caught by grounding —
        # only by the labels. This is the subtle bad prompt, not a crude one.
        return [FlagRuling(
            flag_id=(f["flag_id"] if isinstance(f, dict) else f.flag_id),
            ruling="benign_explained",
            cited_evidence=["signals.account_aggregator.imputed_annual_income"],
        ) for f in flags]

    monkeypatch.setattr(pipeline, "run_judge", clear_everything)
    rep = E.replay()
    assert not rep.clean, "a clear-everything judge must fail the regression gate"
    assert rep.false_benign >= 1, "clearing a case that should REFER is a false-benign"


def test_seeded_bad_rule_is_caught(monkeypatch):
    """A regressed BRE that hard-declines a clean case — the harness must catch the
    label flip on suresh (ISSUE → DECLINE) as a mismatch."""
    from underwriting import rules
    from underwriting.schemas import BreResult

    real_run_bre = rules.run_bre

    def bad_bre(inp, extractor=None):
        res = real_run_bre(inp, extractor=extractor) if extractor else real_run_bre(inp)
        if res.outcome == "CLEAN":  # the seeded bug: clean cases now hard-decline
            return BreResult(outcome="DECLINE", hard_gate="R-003",
                             reason_codes=["R-003-seeded-bug"])
        return res

    monkeypatch.setattr(pipeline, "run_bre", bad_bre)
    rep = E.replay()
    assert not rep.clean
    suresh = next(c for c in rep.cases if c.name == "suresh_salaried_clean")
    assert not suresh.passed and suresh.actual == "DECLINE"


def test_right_verdict_wrong_reason_is_caught(monkeypatch):
    """The finer-label check earns its keep: a BRE that lands the RIGHT terminal
    verdict (REFER on vikram) but raises the WRONG flags (drops the labeled
    velocity_anomaly + non_disclosure_signal, raises only an unrelated flag) must
    still fail — a verdict-only harness would pass it. This is the 'right answer,
    wrong reason' regression the Office-Hours D1 flag/ruling labels exist to catch."""
    from underwriting import rules
    from underwriting.schemas import AmbiguousFlag, BreResult, SoftFlag

    def wrong_flags_same_verdict(inp, extractor=None):
        # Grey-zone (so it still routes to the LLM → escalate → REFER), but the flag
        # it raises is NOT what vikram is labeled with.
        sf = SoftFlag(flag_type="thin_file", related_rule="R-008",
                      reason_code="R-008-x", reason="seeded wrong flag")
        af = AmbiguousFlag(flag_id="f1", flag_type="thin_file", related_rule="R-008")
        return BreResult(outcome="GREY-ZONE", soft_flags=[sf], ambiguous_flags=[af],
                         reason_codes=["R-008-x"])

    def escalate(evidence_bundle, flags, follow_up_observations=None):
        return [FlagRuling(flag_id=(f["flag_id"] if isinstance(f, dict) else f.flag_id),
                           ruling="unresolvable_escalate",
                           cited_evidence=["signals.account_aggregator.imputed_annual_income"])
                for f in flags]

    monkeypatch.setattr(pipeline, "run_bre", wrong_flags_same_verdict)
    monkeypatch.setattr(pipeline, "run_judge", escalate)
    vikram = E.evaluate_case(_fixture("vikram_velocity"), pipeline.run)
    assert vikram.actual == "REFER"      # right terminal verdict...
    assert not vikram.passed              # ...but the case still fails...
    assert not vikram.flags_ok            # ...specifically on the flag mismatch.
    assert any("flags:" in m for m in vikram.mismatches)


# ---------------------------------------------------------------------------
# 3. The triad is measured correctly — unit-level, no fixtures.
# ---------------------------------------------------------------------------
def _fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8")) | {"_name": name}


def test_over_escalation_metric(monkeypatch):
    """A judge that escalates the clean-after-gather case (anjali) → over_escalation,
    NOT false_benign (expected ISSUE, got REFER)."""
    def always_escalate(evidence_bundle, flags, follow_up_observations=None):
        return [FlagRuling(
            flag_id=(f["flag_id"] if isinstance(f, dict) else f.flag_id),
            ruling="unresolvable_escalate",
            cited_evidence=["signals.account_aggregator.imputed_annual_income"],
        ) for f in flags]

    monkeypatch.setattr(pipeline, "run_judge", always_escalate)
    anjali = E.evaluate_case(_fixture("anjali_thin_file"), pipeline.run)
    assert anjali.actual == "REFER"
    assert anjali.over_escalation and not anjali.false_benign


def test_grounding_hallucination_is_gated_not_leaked(monkeypatch):
    """A judge citing a fabricated path is caught by the grounding gate → the
    decision becomes grounding_check_failed, so it is NOT counted as a leaked
    hallucination (the gate did its job). The metric only counts citations that
    reach a decision UNGATED — which should be impossible, hence 0."""
    def hallucinate(evidence_bundle, flags, follow_up_observations=None):
        return [FlagRuling(
            flag_id=(f["flag_id"] if isinstance(f, dict) else f.flag_id),
            ruling="benign_explained",
            cited_evidence=["signals.abha_health_records.THIS_FIELD_IS_FAKE"],
        ) for f in flags]

    monkeypatch.setattr(pipeline, "run_judge", hallucinate)
    vikram = E.evaluate_case(_fixture("vikram_velocity"), pipeline.run)
    assert vikram.actual == "REFER" and vikram.note == "grounding_check_failed"
    assert not vikram.grounding_hallucination, "the gate caught it → not a leak"
