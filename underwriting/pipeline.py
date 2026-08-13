"""pipeline.py — the orchestrator (§2, §9).

Phase 3 wires the full layered flow:
    intake → rules (Layer 1) → scoring (Layer 2)
           → grey-zone? judge → decision table → ONE gather cycle → re-judge   (Layer 3)
           → grounding gate + Core-6 decision mapper (Layer 4)

The deterministic code is the orchestrator; the LLM judge is a narrow subroutine
called ONLY on grey-zone flags. Hard-gate / clean / loading / postpone cases never
reach the judge — they take the deterministic edge in `map_decision` exactly as in
Phase 1-2. The gather→re-judge cycle is bounded to ONE round (§7.1, cycle cap 2).

Grounding + confidence gates live in `map_decision` and run on EVERY ruling,
including the escalate path (§7.1 fix).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import config as C
from . import judge as J
from .decision import RULING_TO_ACTION, decide_next_step, map_decision
from .judge import PROMPT_VERSION, run_judge
from .rules import run_bre
from .scoring import risk_scores, safety_score
from .schemas import Decision, Outcome, ProposalInput


# The gather action → the evidence source it requests. The ACTION is real code
# (would call the gateway); in dev the RESPONSE is mocked (§7.1, §11) — never the step.
_ACTION_TO_SOURCE = {
    "request_additional_document(bank_statement)": "bank_statement",
    "request_abha_consent()": "abha_health_records",
    "request_medical_exam(tele_mer)": "tele_mer",  # LIFE: tele-MER gather
    "request_identity_reverification()": "identity_reverification",
}

# Type of the injectable evidence gatherer: (proposal_id, actions, inp) -> observations,
# keyed by the requested source so re-judge citations resolve under follow_up_observations.
EvidenceGatherer = Callable[[str, list[str], ProposalInput], dict]


def _fixture_gather(proposal_id: str, actions: list[str], inp: ProposalInput) -> dict:
    """Default dev gatherer: the mocked vendor RESPONSE comes from the fixture's
    `follow_up_observations` (mock the response, never the step — §11).

    For each action, look up the source it requests and return whatever the proposal
    pre-canned under `follow_up_observations[<source>]`; absent → status "unavailable".

    A real deployment swaps this for the gateway call behind the same signature. The
    REAL iAdore bank-statement gatherer ships as
    `sources.bank_statement.make_iadore_gatherer()` — inject it via `run(inp, gather=…)`
    to run the live iAdore client on the uploaded PDF instead of reading a canned dict
    (it falls back to THIS gatherer for the non-bank-statement actions).
    """
    canned = inp.follow_up_observations or {}
    out: dict = {}
    for a in actions:
        src = _ACTION_TO_SOURCE.get(a, a)
        out[src] = canned.get(src, {"status": "unavailable", "requested_action": a})
    return out


class PipelineResult:
    """The full orchestrator output: everything computed, LLM included."""

    def __init__(self, inp, bre, risk, safety, breakdown, scoring_total, decision,
                 rulings=None, follow_up=None, judge_cycles=0, run_meta=None):
        self.input: ProposalInput = inp
        self.bre = bre
        self.risk_scores = risk
        self.safety_score = safety
        self.scoring_breakdown = breakdown
        self.scoring_total = scoring_total
        self.decision = decision
        self.rulings = rulings or []       # Judge rulings (Phase 3); [] if no LLM ran
        self.follow_up = follow_up or {}   # what the one gather cycle collected
        self.judge_cycles = judge_cycles   # 0 (no LLM), 1 (judged), 2 (re-judged)
        self.run_meta = run_meta or {}     # model + prompt version + cost (§6/§11)

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.input.proposal_id,
            "bre_result": self.bre.model_dump(),
            "risk_scores": self.risk_scores.model_dump(),
            "safety_score": self.safety_score.model_dump(),
            "scoring_breakdown": [r.model_dump() for r in self.scoring_breakdown],
            "scoring_total": self.scoring_total,
            "decision": self.decision.model_dump(),
            "cited_evidence_chain": [
                {"flag_id": r.flag_id, "ruling": r.ruling, "cited_source": c}
                for r in self.rulings for c in r.cited_evidence
            ],
            "run_metadata": {
                "rules_version": C.RULES_VERSION,
                "prompt_version": PROMPT_VERSION if self.judge_cycles else None,
                "model": self.run_meta.get("model") if self.judge_cycles else None,
                "judge_cycles": self.judge_cycles,
                **{k: v for k, v in self.run_meta.items() if k != "model"},
            },
        }


def run(inp: ProposalInput, gather: Optional[EvidenceGatherer] = None) -> PipelineResult:
    """Full Phase-3 pipeline. `gather` is the (injectable) evidence action runner.

    Non-grey-zone cases (hard gate / clean / loading / postpone) never call the LLM.
    The LLM `extract_condition` is wired into the BRE ONLY for grey-zone proposals
    that carry unstructured ABHA notes — the deterministic pass runs first and cheap.
    """
    gather = gather or _fixture_gather

    # First BRE pass: structured-only (deterministic, no LLM), for triage.
    bre = run_bre(inp)                          # Layer 1
    risk = risk_scores(inp, bre)                # Layer 2
    safety, breakdown, total = safety_score(inp, bre)   # Layer 2

    # Non-grey-zone → deterministic edge, no LLM (rows 1-6).
    if bre.outcome != "GREY-ZONE" or not bre.ambiguous_flags:
        decision = map_decision(bre)
        return PipelineResult(inp, bre, risk, safety, breakdown, total, decision)

    # --- Layer 3: the ONLY AI. Capture cost baseline (eval mode) for stamping. ---
    baseline = J.history_len()
    rulings: list = []
    follow_up: dict = {}
    cycles = 0

    # An unreachable/erroring gateway on a grey-zone case must FAIL SAFE to a human
    # (§11: reason around the unavailable, never a silent wrong answer). Any LM
    # exception in this block → deterministic REFER `judge_unavailable`, not a crash.
    # (The API status code for this case is deferred — see IMPLEMENTATION_PLAN.md D-11.)
    try:
        # Re-run the BRE WITH the LLM extractor if there are free-text ABHA notes to
        # read (§4.2 messy-ABHA path). New flags this surfaces flow through the judge
        # like any other grey-zone flag. Only re-score when the extractor actually
        # changed the flag set — scoring is deterministic (no re-work otherwise).
        if _has_unstructured_notes(inp):
            before = {(f.flag_type, f.related_rule) for f in bre.soft_flags}
            bre = run_bre(inp, extractor=J.extract_condition)
            after = {(f.flag_type, f.related_rule) for f in bre.soft_flags}
            if after != before:
                risk = risk_scores(inp, bre)
                safety, breakdown, total = safety_score(inp, bre)
            if bre.outcome != "GREY-ZONE" or not bre.ambiguous_flags:
                decision = map_decision(bre)
                return PipelineResult(inp, bre, risk, safety, breakdown, total, decision,
                                      run_meta=_run_meta(baseline))

        root = inp.model_dump()
        flags = bre.ambiguous_flags

        rulings = run_judge(root, flags, {})             # Judge call 1
        ns = decide_next_step(rulings, cycle=1)
        cycles = 1

        # --- Stage 3: ONE gather cycle, then re-judge (§7.1; cap 2) ---
        if ns.kind == "GATHER_EVIDENCE":
            needs = {r.ruling for r in rulings if r.ruling != "benign_explained"}
            actions = sorted({RULING_TO_ACTION[n] for n in needs if n in RULING_TO_ACTION})
            follow_up = gather(inp.proposal_id, actions, inp)
            # Fold gathered facts into the root so re-judge citations can resolve them.
            root = {**root, "follow_up_observations": {**root.get("follow_up_observations", {}), **follow_up}}
            rulings = run_judge(root, flags, follow_up)   # Judge call 2 (re-judge)
            cycles = 2
    except Exception as exc:  # noqa: BLE001 — any LM/gateway failure → fail safe
        decision = Decision(
            verdict=Outcome.REFER.value,
            escalation_reason="judge_unavailable",
            reason_summary=f"LLM judge unavailable ({type(exc).__name__}) on a grey-zone "
                           "case → refer to a human underwriter (fail-safe).",
            reason_codes=bre.reason_codes,
        )
        return PipelineResult(inp, bre, risk, safety, breakdown, total, decision,
                              rulings=rulings, follow_up=follow_up, judge_cycles=cycles,
                              run_meta=_run_meta(baseline))

    # --- Layer 4: grounding gate (all rulings, escalate included) + Core-6 mapper.
    bre.gathered = cycles == 2  # decide_next_step inside the mapper reads this (§7.1)
    decision = map_decision(bre, rulings=rulings, evidence_root=root)

    return PipelineResult(inp, bre, risk, safety, breakdown, total, decision,
                          rulings=rulings, follow_up=follow_up, judge_cycles=cycles,
                          run_meta=_run_meta(baseline))


def _has_unstructured_notes(inp: ProposalInput) -> bool:
    a = inp.signals.abha_health_records
    return a.available and bool(getattr(a, "unstructured_notes", None))


def _run_meta(baseline: int) -> dict:
    """model + prompt version + best-effort token/cost stamp (§6/§11)."""
    return {"model": J.model_name(), "prompt_version": PROMPT_VERSION, **J.usage_since(baseline)}
