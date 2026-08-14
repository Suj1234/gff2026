"""test_pipeline.py — Phase 4 done-when: every fixture runs end-to-end through
the pipeline + report assembly and returns a full report validating against the
`ReportOutput` schema, plus the §11 robustness invariants (version stamps,
append-only audit log, partial-data handling, idempotency).

Every fixture under fixtures/ is covered (auto-discovered — see CASES — so a new
fixture like priya_postpone is picked up without editing this file).

Offline: the grey-zone fixtures (rohit/vikram/anjali) would call the LLM, so the
Judge is faked exactly as in test_grounding.py — no network. suresh/fraud/priya
never reach the LLM. The point here is the REPORT + robustness, not re-proving the
Phase-3 grey-zone resolution (test_grounding.py owns that).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from underwriting import judge as J
from underwriting import pipeline
from underwriting.api import _status, run_and_report
from underwriting.report import build_report
from underwriting.schemas import FlagRuling, ProposalInput, ReportOutput

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Offline judge stub — now the ONE shared helper (underwriting/tests/_fakejudge.py),
# consolidated from the copy that used to live here (repo L-A1). Grey-zone flags
# get a grounded ruling so the deterministic decision path runs without a network
# call. A new flag type is added in _fakejudge.py once, not in three copies.
# ---------------------------------------------------------------------------
from ._fakejudge import RULING_BY_FLAG, assert_flags_known, fake_extract, make_fake_judge


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Keep every pipeline run in this module off the network."""
    monkeypatch.setattr(pipeline, "run_judge", make_fake_judge())
    monkeypatch.setattr(J, "extract_condition", fake_extract)


# ---------------------------------------------------------------------------
# EVERY fixture, discovered from disk so a new fixture is covered automatically
# (Phase 4 done-when is "one call on EACH fixture" — a hardcoded list goes stale
# the moment someone adds a case, as happened with priya_postpone). The terminal
# the pipeline must reach is the fixture's `decision_with_llm_after_gather` when
# present (anjali: 2-cycle → ISSUE), else its `decision`. The offline fake judge
# is engineered to reproduce exactly that terminal.
# ---------------------------------------------------------------------------
def _expected_terminal(fixture_name: str) -> str:
    exp = _load(fixture_name)["expected"]
    return exp.get("decision_with_llm_after_gather") or exp["decision"]


CASES = [(p.stem, _expected_terminal(p.stem)) for p in sorted(FIX.glob("*.json"))]


@pytest.mark.parametrize("name,expected_verdict", CASES)
def test_fixture_end_to_end_full_report(name, expected_verdict):
    """DONE-WHEN (§10 Phase 4): one call per fixture → complete report validating
    against ReportOutput, with the right decision."""
    inp = ProposalInput(**_load(name)["input"])

    # Guard: if a (future) grey-zone fixture raises a flag the offline fake judge
    # doesn't know, it silently defaults to ungrounded escalate → REFER, which
    # would make this test lie. Fail loudly instead so the stub gets extended.
    assert_flags_known(inp, name)

    report = run_and_report(inp)

    # 1. Validates against the schema (round-trip through model_dump).
    assert isinstance(report, ReportOutput)
    ReportOutput(**report.model_dump())

    # 2. Lands on the expected Core-6 decision.
    assert report.decision.verdict == expected_verdict, (name, report.decision.reason_summary)

    # 3. Every top-level §8 block is present and populated.
    assert report.report_meta["application_no"] == inp.proposal_id
    assert report.safety_score is not None and 0 <= report.safety_score.value <= 100
    # sum_of_weights is now the ASSESSED weight (renormalization divisor): >0 and ≤1,
    # and equal to the sum of weights over rows marked assessed (absent-source fix).
    aw = report.scoring_total["sum_of_weights"]
    assert report.scoring_breakdown and 0 < aw <= 1.0 + 1e-9
    assert abs(aw - sum(r.weight for r in report.scoring_breakdown if r.assessed)) < 1e-6
    assert report.sections, "sections must be present"
    assert report.risk_scores is not None
    assert report.bre_result is not None
    assert report.decision.reason_summary, "every decision carries a human reason (§11)"

    # Band-vs-decision banner (L-A2): a REFER/DECLINE/STEP_UP/POSTPONE whose Safety band
    # still reads "Low Risk" must carry the advisory note so the narrative doesn't
    # contradict the verdict; a clean ISSUE at Low Risk must NOT carry it.
    _note = report.risk_and_fraud_verdict.get("band_vs_decision_note")
    if report.decision.verdict in {"REFER", "DECLINE", "STEP_UP", "POSTPONE"} \
            and report.safety_score.band == "Low Risk":
        assert _note, f"{name}: adverse verdict at Low band must carry band_vs_decision_note"
    if report.decision.verdict == "ISSUE":
        assert not _note, f"{name}: a clean ISSUE must not carry the band-mismatch note"

    # 4. Version stamps on every output (§11).
    m = report.run_metadata
    assert m.rules_version == "v1"


def test_step_up_returns_pending_status(monkeypatch):
    """API contract: a STEP_UP decision surfaces as `pending` with what it waits on.

    Drive a genuine row-8 STEP_UP: the judge asks for a document, and the gather
    cycle returns nothing usable that re-judge can clear — but here we intercept
    BEFORE the pipeline's re-judge by having decide_next_step land on gather on the
    first cycle. The pipeline always completes both cycles, so a *terminal* STEP_UP
    report is produced by map_decision directly (row 8) — assemble that through the
    real report builder and assert the API surfaces it as pending."""
    from underwriting.decision import map_decision
    from underwriting.report import build_report
    from underwriting.rules import run_bre
    from underwriting.scoring import risk_scores, safety_score

    inp = ProposalInput(**_load("anjali_thin_file")["input"])
    bre = run_bre(inp)
    rulings = [FlagRuling(flag_id=bre.ambiguous_flags[0].flag_id,
                          ruling="needs_income_corroboration",
                          cited_evidence=["signals.account_aggregator.imputed_annual_income"])]
    # cycle=1, not yet gathered → row 8 STEP_UP.
    decision = map_decision(bre, rulings=rulings, evidence_root=inp.model_dump())
    assert decision.verdict == "STEP_UP", decision.reason_summary

    risk = risk_scores(inp, bre)
    safety, breakdown, total = safety_score(inp, bre)
    res = pipeline.PipelineResult(inp, bre, risk, safety, breakdown, total, decision,
                                  rulings=rulings, judge_cycles=1)
    report = build_report(res)
    assert _status(report) == "pending"
    assert report.decision.next_step, "pending must say what it is waiting on"
    assert report.decision.verdict == "STEP_UP"


def test_complete_status_for_terminal_decisions():
    for name in ("suresh_salaried_clean", "fraud_deepfake", "rohit_self_employed"):
        report = run_and_report(ProposalInput(**_load(name)["input"]))
        assert _status(report) == "complete"


# ---------------------------------------------------------------------------
# The actual FastAPI route (not just the run_and_report core) — proves the HTTP
# shell: body validation at the boundary, the {status, report} envelope, 422.
# ---------------------------------------------------------------------------
def _client():
    from fastapi.testclient import TestClient

    from underwriting.api import app
    return TestClient(app)


def test_http_underwrite_route_returns_report_envelope():
    """DONE-WHEN read strictly ('one call on each fixture'): the HTTP POST route
    returns the full report envelope, not just the internal core."""
    client = _client()
    for name, expected in (("suresh_salaried_clean", "ISSUE"), ("vikram_velocity", "REFER")):
        resp = client.post("/underwrite", json=_load(name)["input"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "complete"
        assert body["report"]["decision"]["verdict"] == expected
        # the returned report is a full, schema-valid report
        ReportOutput(**body["report"])


def test_http_invalid_body_is_rejected_at_the_boundary():
    """A malformed bundle (missing `application`) → 422, never a 500 / silent pass."""
    resp = _client().post("/underwrite", json={"proposal_id": "X"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# §11 robustness
# ---------------------------------------------------------------------------
def test_idempotency_same_input_same_decision():
    """§11 idempotency is on the DECISION: same input → same verdict + reasons on
    retry. (The report may differ in LLM cost/token fields between two *real* LLM
    runs; that is not a decision change. Here the faked judge is deterministic so
    the whole report also matches — asserted separately below.)"""
    inp = ProposalInput(**_load("rohit_self_employed")["input"])
    first = run_and_report(inp).decision
    second = run_and_report(inp).decision
    assert first.model_dump() == second.model_dump()


def test_report_assembly_is_pure():
    """Report assembly (build_report) is a pure function of the pipeline result —
    no wall-clock, no randomness. Assembling the SAME PipelineResult twice yields
    byte-identical output. This is the part §11 idempotency actually owns at the
    report layer; the LLM cost/token stamp lives outside it (in run_meta)."""
    from underwriting.decision import map_decision
    from underwriting.rules import run_bre
    from underwriting.scoring import risk_scores, safety_score

    inp = ProposalInput(**_load("suresh_salaried_clean")["input"])
    bre = run_bre(inp)
    risk = risk_scores(inp, bre)
    safety, breakdown, total = safety_score(inp, bre)
    decision = map_decision(bre)
    res = pipeline.PipelineResult(inp, bre, risk, safety, breakdown, total, decision)
    assert build_report(res).model_dump_json() == build_report(res).model_dump_json()


def test_version_stamps_present_on_llm_and_non_llm_paths():
    """Rules version always stamped; prompt/model stamped only when the LLM ran."""
    clean = run_and_report(ProposalInput(**_load("suresh_salaried_clean")["input"]))
    assert clean.run_metadata.rules_version == "v1"
    assert clean.run_metadata.model is None  # no LLM on the clean path
    assert clean.run_metadata.prompt_version is None

    grey = run_and_report(ProposalInput(**_load("vikram_velocity")["input"]))
    assert grey.run_metadata.rules_version == "v1"
    assert grey.run_metadata.prompt_version == "v1"  # LLM ran → prompt version stamped
    assert grey.run_metadata.model  # model stamped


def test_audit_log_is_ordered_and_covers_every_stage():
    """Append-only audit trail (§11): bre first, decision last, a judge entry per cycle."""
    report = run_and_report(ProposalInput(**_load("vikram_velocity")["input"]))
    steps = [e.step for e in report.audit_log]
    assert steps[0] == "bre" and steps[-1] == "decision"
    assert any(s.startswith("judge_cycle_") for s in steps)
    assert all(e.detail for e in report.audit_log), "every audit entry has a detail"


def test_partial_data_is_reasoned_around_not_a_crash():
    """A bundle stripped down to the minimum (most sources absent) must still
    produce a valid report — partial data is the normal case (§11)."""
    minimal = {
        "proposal_id": "PARTIAL-001",
        "meta": {"received_at": "2026-06-03T09:00:00Z"},
        "application": {
            "applicant": {"name": "Minimal Applicant", "age": 30},
            "product": {"type": "individual_health", "sum_assured": 500000},
            "health_declaration": {"conditions": []},
        },
    }
    inp = ProposalInput(**minimal)
    report = run_and_report(inp)  # must not raise
    ReportOutput(**report.model_dump())
    assert report.decision is not None
    assert report.sections, "sections still produced from whatever facts exist"
    # No source facts → no LLM stamp, but rules version is always there.
    assert report.run_metadata.rules_version == "v1"


def test_report_meta_survives_missing_optional_fields():
    """report_meta must not crash when meta/premium/occupation are absent."""
    inp = ProposalInput(**{
        "proposal_id": "NOMETA-001",
        "application": {
            "applicant": {"name": "No Meta", "age": 40},
            "product": {"type": "individual_health", "sum_assured": 1000000},
        },
    })
    report = run_and_report(inp)
    assert report.report_meta["application_no"] == "NOMETA-001"
    assert report.report_meta["report_date"] is None  # no received_at → None, not a crash
