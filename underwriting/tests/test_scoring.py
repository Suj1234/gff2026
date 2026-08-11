"""test_scoring.py — risk scores + Safety Score (Phase 2).  NO AI, NO randomness.

Done-when (IMPLEMENTATION_PLAN.md Phase 2): Rohit's Safety Score computes to
~65 / High from the §4A weight table, and every score carries attribution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from underwriting import config as C, pipeline
from underwriting.pipeline import run
from underwriting.rules import run_bre
from underwriting.scoring import (
    _lab_severity,
    risk_scores,
    safety_score,
)
from underwriting.schemas import FlagRuling, ProposalInput

FIX = Path(__file__).parent / "fixtures"
FIXTURES = [
    "suresh_salaried_clean",
    "rohit_self_employed",
    "anjali_thin_file",
    "vikram_velocity",
    "fraud_deepfake",
]


def _load(name):
    data = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    return ProposalInput(**data["input"]), data


def _stub_judge_escalate(evidence_bundle, flags, follow_up_observations=None):
    """Deterministic offline judge: escalate every flag (grounded citation).

    Keeps these Phase-2 scoring tests off the network AND makes grey-zone cases
    resolve to the SAME deterministic REFER the fixture `expected.decision` holds
    (the Phase-1/2 label) — so scoring wiring is what's under test, not the LLM.
    """
    out = []
    for f in flags:
        fid = f["flag_id"] if isinstance(f, dict) else f.flag_id
        out.append(FlagRuling(flag_id=fid, ruling="unresolvable_escalate", cited_evidence=[]))
    return out


def _stub_extract(note_text):
    return []  # scoring tests don't exercise the free-text path; keep it offline


def _offline(monkeypatch):
    monkeypatch.setattr(pipeline, "run_judge", _stub_judge_escalate)
    monkeypatch.setattr(pipeline.J, "extract_condition", _stub_extract)


# ===========================================================================
# Determinism — never a random number (§5 hard line)
# ===========================================================================
@pytest.mark.parametrize("name", FIXTURES)
def test_scores_are_deterministic(name):
    inp, _ = _load(name)
    bre = run_bre(inp)
    a = risk_scores(inp, bre)
    b = risk_scores(inp, bre)
    assert a.model_dump() == b.model_dump()
    sa, ra, ta = safety_score(inp, bre)
    sb, rb, tb = safety_score(inp, bre)
    assert sa.model_dump() == sb.model_dump()
    assert ta == tb


# ===========================================================================
# Attribution present on every risk score (§5.1)
# ===========================================================================
def test_risk_scores_carry_attribution():
    inp, _ = _load("rohit_self_employed")
    rs = risk_scores(inp, run_bre(inp))
    assert rs.shap, "fraud attribution must be non-empty for a flagged case"
    # every attributed weight is a real contribution in (0,1]
    assert all(0 < v <= 1 for v in rs.shap.values())


def test_attribution_reconciles_with_score():
    """shap must be honest about the score it explains (§5.1, §11).

    heuristic-driven fraud score → shap weights sum to it. upstream-model-driven →
    shap corroborates but is NOT claimed to sum to it (score_source says which).
    """
    inp, _ = _load("rohit_self_employed")
    rs = risk_scores(inp, run_bre(inp))
    assert rs.score_source in ("heuristic", "upstream_model")
    if rs.score_source == "heuristic":
        assert abs(sum(rs.shap.values()) - rs.fraud_score) < 1e-3

    # drop the upstream ml_scores → the heuristic must drive it and reconcile
    data = json.loads((FIX / "rohit_self_employed.json").read_text(encoding="utf-8"))
    data["input"]["signals"].pop("ml_scores", None)
    inp2 = ProposalInput(**data["input"])
    rs2 = risk_scores(inp2, run_bre(inp2))
    assert rs2.score_source == "heuristic"
    assert abs(sum(rs2.shap.values()) - rs2.fraud_score) < 1e-3


def test_clean_case_has_low_scores():
    inp, _ = _load("suresh_salaried_clean")
    rs = risk_scores(inp, run_bre(inp))
    assert rs.fraud_score < C.ML_SCORE_HIGH_MIN
    assert rs.composite_band in ("low", "moderate")


# ===========================================================================
# The Phase-2 done-when: Rohit ~65 / High
# ===========================================================================
def test_rohit_safety_score_is_65_high():
    inp, _ = _load("rohit_self_employed")
    bre = run_bre(inp)
    ss, rows, total = safety_score(inp, bre)
    assert ss.band == "High Risk", (ss.value, ss.band)
    assert 60 <= ss.value <= 70, f"Rohit safety score {ss.value} not ~65"
    # from the §4A weight table exactly
    assert abs(total["sum_of_weights"] - 1.0) < 1e-9
    assert total["computed_safety_score"] == ss.value


def test_rohit_fraud_score_high_band():
    inp, _ = _load("rohit_self_employed")
    rs = risk_scores(inp, run_bre(inp))
    assert rs.fraud_score >= C.ML_SCORE_HIGH_MIN
    assert rs.composite_band == "high"
    assert "medical_misrepresentation" in rs.shap  # non-disclosure attributed


# ===========================================================================
# Breakdown integrity — contributions reconstruct the total (auditability §11)
# ===========================================================================
@pytest.mark.parametrize("name", FIXTURES)
def test_breakdown_reconstructs_total(name):
    inp, _ = _load(name)
    bre = run_bre(inp)
    ss, rows, total = safety_score(inp, bre)
    # every source group in the config weight table appears exactly once
    assert {r.source_group for r in rows} == set(C.SAFETY_SCORE_WEIGHTS)
    # Σ contribution == the reported safety score (within rounding)
    assert abs(sum(r.contribution for r in rows) - ss.value) < 0.6
    # weights match config; sub-scores are 0-100; each has a human 'why'
    for r in rows:
        assert r.weight == C.SAFETY_SCORE_WEIGHTS[r.source_group]
        assert 0 <= r.risk_sub_score <= 100
        assert r.why
        assert abs(r.weight * r.risk_sub_score - r.contribution) < 0.01


def test_band_mapping_matches_config():
    assert C.safety_band(85) == "Low Risk"
    assert C.safety_band(70) == "Moderate Risk"
    assert C.safety_band(65) == "High Risk"
    assert C.safety_band(0) == "High Risk"


# ===========================================================================
# Lab-severity judgment (the FACTS-in / judgment-out boundary §1.8)
# ===========================================================================
def test_lab_severity_ranges():
    assert _lab_severity("chol", 228, "<200") == "high"
    assert _lab_severity("chol", 180, "<200") is None
    assert _lab_severity("hb", 11.8, "13.5-17.5") == "high"   # far below band
    assert _lab_severity("glucose", 102, "70-99") == "low"    # just above band
    assert _lab_severity("hb", 15.0, "13.5-17.5") is None


# ===========================================================================
# Pipeline wiring (§9) — scoring flows through the orchestrator
# ===========================================================================
def test_pipeline_wires_scoring(monkeypatch):
    _offline(monkeypatch)  # deterministic, no network
    inp, data = _load("rohit_self_employed")
    res = run(inp)
    assert res.decision.verdict == data["expected"]["decision"]  # REFER
    assert res.safety_score.band == "High Risk"
    assert res.risk_scores.composite_band == "high"
    d = res.as_dict()
    assert d["safety_score"]["value"] == res.safety_score.value
    assert len(d["scoring_breakdown"]) == len(C.SAFETY_SCORE_WEIGHTS)


@pytest.mark.parametrize("name", FIXTURES)
def test_pipeline_runs_all_fixtures(name, monkeypatch):
    _offline(monkeypatch)  # deterministic, no network
    inp, data = _load(name)
    res = run(inp)
    assert res.decision.verdict == data["expected"]["decision"]
    assert 0 <= res.safety_score.value <= 100
    assert res.safety_score.value == res.scoring_total["computed_safety_score"]


def test_safer_case_scores_higher():
    """The Safety Score must ORDER cases by safety (higher = safer, §5.2).

    The one property that makes the score mean anything: a clean case must outscore
    a confirmed-non-disclosure one. Catches an inverted penalty sign that would still
    reconstruct its total but swap safe and risky cases.
    """
    clean, _ = _load("suresh_salaried_clean")
    risky, _ = _load("rohit_self_employed")
    clean_score = safety_score(clean, run_bre(clean))[0].value
    risky_score = safety_score(risky, run_bre(risky))[0].value
    assert clean_score > risky_score, (clean_score, risky_score)


# ===========================================================================
# Phase A — the new sub-scores stop scoring adverse facts as "clean"
# ===========================================================================
def _row(rows, group):
    return next(r for r in rows if r.source_group == group)


def test_litigation_criminal_is_not_scored_clean():
    """A1 silent-miss fix: a bundle with criminal litigation must score the
    litigation_fir group BELOW clean (was 100 = 'no adverse litigation')."""
    from underwriting.tests.test_rules import _sig
    from underwriting.schemas import ProposalInput

    def _inp(lit):
        base = ProposalInput(**{
            "proposal_id": "LIT", "application": {
                "applicant": {"name": "Test User", "dob": "1990-01-01", "age": 34,
                              "address": "1 Main St, City, 560001"},
                "product": {"type": "individual_health", "sum_assured": 2500000},
                "declared_pep": False, "health_declaration": {"conditions": []}},
        })
        base.signals = _sig(**({"litigation_fir": lit} if lit else {}))
        return base

    crim = _inp({"status": "available", "criminal_cases": 3, "firs_registered": 1,
                 "cases": [{"civil_criminal": "criminal"}, {"civil_criminal": "criminal"}]})
    clean = _inp(None)
    _, rows_c, _ = safety_score(crim, run_bre(crim))
    _, rows_z, _ = safety_score(clean, run_bre(clean))
    assert _row(rows_c, "litigation_fir").risk_sub_score < 100
    assert _row(rows_z, "litigation_fir").risk_sub_score == 100  # absent → clean, as before


def test_gst_alert_penalizes_occupation_subscore():
    """A2: a GST cancelled alert must deduct from the occupation/employer sub-score."""
    from underwriting.tests.test_rules import _clean_input, _sig

    inp = _clean_input()
    inp.signals = _sig(gst={"status": "available", "activeAlerts": [{"key": "isGstCancelled"}]})
    _, rows, _ = safety_score(inp, run_bre(inp))
    row = _row(rows, "occupation_employer")
    assert row.risk_sub_score < 100 and "GST cancelled" in row.why


def test_email_intel_feeds_fraud_subscore():
    """A3: a high inverted email fraud score must deduct from the fraud_check sub-score."""
    from underwriting.tests.test_rules import _clean_input, _sig

    inp = _clean_input()
    # inverted 0-1 (higher = riskier): 0.9 is a very risky email (vendor score ~10/100).
    inp.signals = _sig(email_intel={"status": "available", "is_disposable": True,
                                    "fraud_risk_score": 0.9})
    _, rows, _ = safety_score(inp, run_bre(inp))
    row = _row(rows, "fraud_check")
    assert row.risk_sub_score < 100
    assert "disposable" in row.why or "email fraud score" in row.why
