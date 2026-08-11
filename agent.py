# agent.py — grey-zone underwriting agent, staged pipeline.
# Spec: Agent_Build_Specification.md §5 (decision table), §6 (pipeline + grounding gate).
# This is the agent's brain only: schemas -> Judge (AI) -> decision table -> gather-once -> grounding gate.
import os
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel
import dspy


# --- tiny .env loader (no python-dotenv dependency) ---
def _load_env():
    p = Path(__file__).parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


# --- schemas (Agent Build Spec §2/§3) ---
class AmbiguousFlag(BaseModel):
    flag_id: str
    flag_type: str
    related_rule: str
    context: dict


class FlagRuling(BaseModel):
    flag_id: str
    ruling: Literal[
        "benign_explained",
        "needs_income_corroboration",
        "needs_medical_check",
        "needs_identity_reverification",
        "unresolvable_escalate",
    ]
    cited_evidence: list[str] = []
    reasoning: str = ""


class AgentResult(BaseModel):
    proposal_id: str
    outcome: Literal["resolved", "escalated"]
    rulings: list[FlagRuling]
    final_verdict: Optional[dict] = None


# --- Stage 1: the one AI call (Agent Build Spec §6.1) ---
class GreyZoneJudge(dspy.Signature):
    """Rule on EACH ambiguous flag from this grey-zone insurance proposal.
    One ruling per flag, in the SAME call -- do not skip any flag.

    Use ONLY the evidence in evidence_bundle, the specific context given per
    flag, and -- if this is a re-judge call -- follow_up_observations gathered
    in the one permitted evidence-gathering cycle for this case. Never infer a
    fact not present in these inputs.

    A flag is benign_explained only if you can cite a SPECIFIC piece of evidence
    that resolves it -- not an absence of concern. If nothing in the inputs
    resolves a flag and no evidence-gathering category applies, rule
    unresolvable_escalate rather than guessing benign."""

    evidence_bundle: dict = dspy.InputField()
    ambiguous_flags: list[dict] = dspy.InputField()
    follow_up_observations: dict = dspy.InputField(desc="empty dict on cycle 1")
    rulings: list[FlagRuling] = dspy.OutputField()


def _lm_ready() -> bool:
    model = os.environ.get("LLM_MODEL")
    if not model:
        return False
    # Company gateway is OpenAI-compatible -> pass its base URL so LiteLLM
    # routes there instead of the real OpenAI API. No URL set = direct provider.
    api_base = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    kwargs = {"api_base": api_base} if api_base else {}
    dspy.configure(lm=dspy.LM(model, **kwargs))
    return True


_LM_READY = _lm_ready()
_judge = dspy.ChainOfThought(GreyZoneJudge)


def has_api_key() -> bool:
    return _LM_READY and any(
        os.environ.get(k) for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
    )


def run_judge(evidence_bundle: dict, flags: list[dict], follow_up_observations: dict) -> list[FlagRuling]:
    if not _LM_READY:
        raise RuntimeError("No LLM configured. Set LLM_MODEL and your provider key in .env")
    return _judge(
        evidence_bundle=evidence_bundle,
        ambiguous_flags=flags,
        follow_up_observations=follow_up_observations,
    ).rulings


# --- Stage 2: deterministic decision table (Agent Build Spec §5) ---
class NextStep(BaseModel):
    kind: Literal["FINALIZE", "GATHER_EVIDENCE", "ESCALATE"]
    reason: Optional[str] = None
    gather: list[str] = []  # flag_ids needing one round of evidence


def decide_next_step(rulings: list[FlagRuling], cycle: int) -> NextStep:
    if any(r.ruling == "unresolvable_escalate" for r in rulings):
        return NextStep(kind="ESCALATE", reason="unresolvable_ruling")
    unresolved = [r for r in rulings if r.ruling != "benign_explained"]
    if cycle >= 2:  # the single re-Judge cycle already ran; no further looping, ever
        if unresolved:
            return NextStep(kind="ESCALATE", reason="max_cycles_exceeded")
        return NextStep(kind="FINALIZE")
    if unresolved:
        return NextStep(kind="GATHER_EVIDENCE", gather=[r.flag_id for r in unresolved])
    return NextStep(kind="FINALIZE")


# --- Stage 4: grounding gate (Agent Build Spec §6.4) ---
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _tokenize(path: str):
    for name, idx in _TOKEN.findall(path):
        yield name if name else int(idx)


def _resolve(path: str, root: dict) -> bool:
    """True if a dotted/indexed path (e.g. 'a.b[0].c') resolves against root."""
    cur = root
    for tok in _tokenize(path):
        if isinstance(tok, int):
            if not isinstance(cur, list) or tok >= len(cur):
                return False
            cur = cur[tok]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return False
            cur = cur[tok]
    return True


def deterministic_final_gate(
    proposal_id: str,
    rulings: list[FlagRuling],
    next_step: NextStep,
    evidence_bundle: dict,
    flags: list[dict],
    follow_up_observations: dict,
) -> AgentResult:
    if next_step.kind == "ESCALATE":
        return AgentResult(
            proposal_id=proposal_id, outcome="escalated", rulings=rulings,
            final_verdict={"escalation_reason": next_step.reason},
        )
    root = {**evidence_bundle, "ambiguous_flags": flags, "follow_up_observations": follow_up_observations}
    for r in rulings:
        for path in r.cited_evidence:
            if not _resolve(path, root):
                return AgentResult(
                    proposal_id=proposal_id, outcome="escalated", rulings=rulings,
                    final_verdict={"escalation_reason": "grounding_check_failed"},
                )
    if all(r.ruling == "benign_explained" for r in rulings):
        return AgentResult(
            proposal_id=proposal_id, outcome="resolved", rulings=rulings,
            final_verdict={"verdict": "STEP-UP", "confidence_band": "high"},
        )
    return AgentResult(proposal_id=proposal_id, outcome="escalated", rulings=rulings, final_verdict=None)


# --- the pipeline: Judge -> table -> gather once -> re-Judge -> gate (hard cap 2 AI calls) ---
def run_pipeline(proposal_id: str, evidence_bundle: dict, ambiguous_flags: list[dict],
                 mock_observations: Optional[dict] = None) -> AgentResult:
    flags = [AmbiguousFlag(**f).model_dump() for f in ambiguous_flags]  # validate at the trust boundary

    rulings = run_judge(evidence_bundle, flags, {})
    next_step = decide_next_step(rulings, cycle=1)

    follow_up: dict = {}
    if next_step.kind == "GATHER_EVIDENCE":
        # Stage 3: gather ONE round. Real actions (request_abha_consent, etc.) go here;
        # for now the N4 mock is whatever the fixture pre-canned per flag.
        mock_observations = mock_observations or {}
        follow_up = {fid: mock_observations.get(fid, {"status": "unavailable"}) for fid in next_step.gather}
        rulings = run_judge(evidence_bundle, flags, follow_up)
        next_step = decide_next_step(rulings, cycle=2)

    return deterministic_final_gate(proposal_id, rulings, next_step, evidence_bundle, flags, follow_up)
