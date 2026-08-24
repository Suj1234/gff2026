"""_fakejudge.py — the ONE shared offline judge stub for the network-free tests.

Consolidates the `_RULING_BY_FLAG` map + stateful judge factory that were
previously copy-pasted verbatim into test_pipeline.py and test_eval.py (repo
deferred item L-A1). The footgun those copies carried: a NEW grey-zone flag
(e.g. Phase-A `adverse_litigation`, or a life `cross_signal_moral_hazard`) had
to be added to EVERY copy or a fixture silently mis-resolved via the escalate
default — this bit once during Phase A. One map, one place to extend.

`assert_flags_known(inp)` is the loud guard: it fails if a fixture raises a flag
the stub has no ruling for, so a new flag is a RED test, never a silent escalate.
Both test_pipeline.py AND test_eval.py call it (previously only test_pipeline.py
was guarded — the eval copy was the silent path the audit found).

test_grounding.py is intentionally NOT a client: it uses a positional per-test
factory with no shared map, so it is footgun-free by construction.
"""

from __future__ import annotations

from underwriting.rules import run_bre
from underwriting.schemas import FlagRuling, ProposalInput

# Per-flag canned rulings, keyed by flag_type → (ruling, [cited real path]).
# Grounded citations resolve against the real bundle so the grounding gate passes.
# EXTEND HERE when a new grey-zone flag type is added — one place, not three.
RULING_BY_FLAG = {
    "non_disclosure_signal": ("unresolvable_escalate", ["signals.abha_health_records.icd_codes"]),
    "moderate_ml_score": ("unresolvable_escalate", ["signals.velocity_graph.velocity_score"]),
    "ckyc_mismatch": ("unresolvable_escalate", ["signals.ckyc.address"]),
    "mobile_pan_mismatch": ("unresolvable_escalate", ["signals.mobile_intel.holder_name"]),
    "velocity_anomaly": ("unresolvable_escalate", ["signals.velocity_graph.velocity_score"]),
    "thin_file": ("needs_income_corroboration", ["signals.account_aggregator.imputed_annual_income"]),
    "income_thin_file": ("needs_income_corroboration", ["signals.account_aggregator.imputed_annual_income"]),
    "gst_alert": ("unresolvable_escalate", ["signals.gst.activeAlerts"]),
    # LIFE flags (Phase 2/3): over-insurance & cover-stacking escalate to a human;
    # the cross-signal moral-hazard flag (R-M2, Phase 3) likewise.
    "over_insurance": ("unresolvable_escalate", ["application.financial.human_life_value"]),
    "cover_stacking": ("unresolvable_escalate", ["signals.iib.life_inforce_sa"]),
    "cross_signal_moral_hazard": ("unresolvable_escalate", ["signals.mobile_intel.holder_name"]),
}


def fake_extract(note_text: str):
    """Offline `extract_condition` stub — no free-text extraction, no network."""
    return []


def make_fake_judge():
    """Stateful judge stub: cycle 1 rules by flag_type (`RULING_BY_FLAG`); on the
    re-judge (cycle 2) a gather-resolvable ruling flips to benign_explained citing
    the gathered doc — so a two-cycle case resolves gather → re-judge → ISSUE.

    Cycle-2 flips (the offline model of "the gathered evidence resolved it"):
      needs_income_corroboration → benign (bank_statement.verified_annual_income)
      needs_medical_check        → benign (tele_mer.vitals)   [life tele-MER path]

    Note: the ruling vocabulary is a FIXED Literal on FlagRuling
    (benign_explained | needs_income_corroboration | needs_medical_check |
    needs_identity_reverification | unresolvable_escalate). Life's tele-MER REUSES
    the existing `needs_medical_check` ruling — no new ruling is invented; only the
    gather ACTION it maps to (Phase 2) differs (ABHA consent vs tele-MER).
    """
    calls = {"n": 0}

    # A gather-resolvable ruling → the gathered path it cites once resolved (must
    # resolve under follow_up_observations for the grounding gate to pass).
    _RESOLVED_CITE = {
        "needs_income_corroboration": "follow_up_observations.bank_statement.verified_annual_income",
        "needs_medical_check": "follow_up_observations.tele_mer.vitals",
    }

    def fake(evidence_bundle, flags, follow_up_observations=None):
        calls["n"] += 1
        second_cycle = calls["n"] >= 2
        out = []
        for f in flags:
            fid = f["flag_id"] if isinstance(f, dict) else f.flag_id
            ftype = f.get("flag_type") if isinstance(f, dict) else f.flag_type
            ruling, cited = RULING_BY_FLAG.get(ftype, ("unresolvable_escalate", []))
            if second_cycle and ruling in _RESOLVED_CITE:
                ruling, cited = "benign_explained", [_RESOLVED_CITE[ruling]]
            out.append(FlagRuling(flag_id=fid, ruling=ruling, cited_evidence=cited))
        return out

    return fake


def assert_flags_known(inp: ProposalInput, name: str = "") -> None:
    """Loud guard: fail if a fixture raises a grey-zone flag the stub can't rule on.

    Without this, an unknown flag silently defaults to ungrounded escalate → REFER,
    which can make a test pass for the wrong reason (the Phase-A silent-miss class).
    Both test_pipeline.py and test_eval.py call this so BOTH paths are loud.
    """
    unknown = {f.flag_type for f in run_bre(inp).ambiguous_flags} - set(RULING_BY_FLAG)
    assert not unknown, (
        f"{name or 'fixture'}: fake judge has no ruling for {unknown}; "
        f"extend RULING_BY_FLAG in underwriting/tests/_fakejudge.py"
    )
