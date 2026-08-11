import json
import os
from pathlib import Path

import pytest

import agent
from agent import FlagRuling, decide_next_step, deterministic_final_gate

FIX = Path(__file__).parent / "fixtures"


def _r(flag_id, ruling):
    return FlagRuling(flag_id=flag_id, ruling=ruling)


# --- deterministic: decision table (Agent Build Spec §5), no LLM needed ---
def test_escalate_on_unresolvable():
    ns = decide_next_step([_r("a", "benign_explained"), _r("b", "unresolvable_escalate")], cycle=1)
    assert ns.kind == "ESCALATE" and ns.reason == "unresolvable_ruling"


def test_gather_on_needs():
    ns = decide_next_step([_r("a", "benign_explained"), _r("b", "needs_medical_check")], cycle=1)
    assert ns.kind == "GATHER_EVIDENCE" and ns.gather == ["b"]


def test_finalize_all_benign():
    assert decide_next_step([_r("a", "benign_explained")], cycle=1).kind == "FINALIZE"


def test_cycle2_unresolved_escalates():
    ns = decide_next_step([_r("a", "needs_income_corroboration")], cycle=2)
    assert ns.kind == "ESCALATE" and ns.reason == "max_cycles_exceeded"


# --- deterministic: grounding gate (Agent Build Spec §6.4) ---
def test_grounding_rejects_fake_citation():
    eb = {"identity": {"pan": "X"}}
    rulings = [FlagRuling(flag_id="a", ruling="benign_explained", cited_evidence=["identity.not_real"])]
    res = deterministic_final_gate("P", rulings, decide_next_step(rulings, 1), eb, [], {})
    assert res.outcome == "escalated" and res.final_verdict["escalation_reason"] == "grounding_check_failed"


def test_grounding_accepts_real_citation():
    eb = {"identity": {"ckyc": {"match": True}}}
    rulings = [FlagRuling(flag_id="a", ruling="benign_explained", cited_evidence=["identity.ckyc.match"])]
    res = deterministic_final_gate("P", rulings, decide_next_step(rulings, 1), eb, [], {})
    assert res.outcome == "resolved" and res.final_verdict["verdict"] == "STEP-UP"


def test_resolve_indexed_path():
    root = {"ambiguous_flags": [{"context": {"items": [10, 20]}}]}
    assert agent._resolve("ambiguous_flags[0].context.items[1]", root) is True
    assert agent._resolve("ambiguous_flags[0].context.items[9]", root) is False


# --- live smoke: proves the AI wiring, NOT a graded ruling. Opt-in only. ---
# Requires UW_RUN_LIVE=1 in addition to a key, so a stray key in .env never hangs
# the suite against an unreachable gateway (matches underwriting/tests policy).
@pytest.mark.skipif(
    not (agent.has_api_key() and os.environ.get("UW_RUN_LIVE", "").strip().lower() in ("1", "true", "yes")),
    reason="live LLM off — set a key AND UW_RUN_LIVE=1 to run",
)
def test_vikram_smoke(capsys):
    fx = json.loads((FIX / "vikram_mehta.json").read_text())
    res = agent.run_pipeline(fx["proposal_id"], fx["evidence_bundle"], fx["ambiguous_flags"], fx.get("mock_observations"))
    assert res.outcome in ("resolved", "escalated")
    assert len(res.rulings) == len(fx["ambiguous_flags"])
    with capsys.disabled():
        print(f"\nVIKRAM -> {res.outcome}  {res.final_verdict}")
        for r in res.rulings:
            print(f"  {r.flag_id}: {r.ruling} | {r.reasoning[:90]}")
