"""judge.py — the LLM grey-zone judge (Layer 3, §6). The ONLY AI in the system.

One narrow DSPy signature (`dspy.ChainOfThought(GreyZoneJudge)`, NOT `ReAct`) that
rules on each ambiguous flag the BRE flagged, plus `extract_condition` — the
unstructured-record path (§4.2, §6): read a messy/free-text ABHA note and emit a
structured condition the R-010 crosswalk can then compare.

Judge boundary (§1.6, files/CLAUDE.md): the Judge rules on flags only. It never
sets a premium/loading number, never touches AML/PEP/STP-gate/identity-fraud, and
its most severe ruling is `unresolvable_escalate`. `map_decision` (decision.py) —
deterministic code — turns rulings into the final verdict; the Judge never does.

Production discipline (§6, §11, files/CLAUDE.md line 19):
  - DSPy call-history retention OFF in prod (default keeps ~10k calls → OOM); ON in eval.
  - LLM response caching OFF in prod; ON in eval (reproducible regression replay).
  Toggle with `configure_lm(eval_mode=...)` or env `UW_EVAL_MODE=1`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import dspy

from .schemas import AmbiguousFlag, FlagRuling

PROMPT_VERSION = "v1"
LLM_TIMEOUT_S = 60  # fail fast on an unreachable gateway  # TODO(underwriting-manual): tune for prod SLA


# ---------------------------------------------------------------------------
# .env loader — same tiny loader as agent.py (no python-dotenv dependency)
# ---------------------------------------------------------------------------
def _load_env() -> None:
    # .env sits at the repo root, one level above the package.
    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


# ---------------------------------------------------------------------------
# Stage 1 signature — one ruling per flag, evidence-grounded (§6)
# ---------------------------------------------------------------------------
class GreyZoneJudge(dspy.Signature):
    """Rule on EACH ambiguous flag from this grey-zone insurance proposal.
    One ruling per flag, in the SAME call -- do not skip any flag.

    Use ONLY the evidence in evidence_bundle, the specific context given per
    flag, and -- if this is a re-judge call -- follow_up_observations gathered
    in the one permitted evidence-gathering cycle for this case. Never infer a
    fact not present in these inputs.

    A flag is benign_explained only if you can cite a SPECIFIC piece of evidence
    (a real dotted path into evidence_bundle / follow_up_observations, e.g.
    'signals.itr.latest_total_taxable_income') that resolves it -- not an absence
    of concern. If nothing in the inputs resolves a flag and no evidence-gathering
    category (income / medical / identity) applies, rule unresolvable_escalate
    rather than guessing benign.

    A `cross_signal_moral_hazard` flag is special: it names a COMBINATION of
    individually-benign facts (a mobile holder mismatch, a third-party premium payer,
    an elderly nominee, a sudden large sum-assured) that together may describe a
    fronting / proxy / early-claim pattern. Reason over the WHOLE combination, not
    each fact alone. Rule benign_explained only if the combination has an innocent
    explanation you can cite (e.g. a joint-family payer with a consistent address);
    otherwise unresolvable_escalate -- a human underwriter must judge a suspected
    fronting pattern. You never clear it just because each fact is individually fine.

    You never set a premium or loading number and never decide AML / sanctions /
    identity-fraud / eligibility -- those are decided by deterministic rules, not
    you. Your most severe possible ruling is unresolvable_escalate."""

    evidence_bundle: dict = dspy.InputField(desc="the full facts bundle for this proposal")
    ambiguous_flags: list[dict] = dspy.InputField(desc="the specific grey-zone flags to rule on")
    follow_up_observations: dict = dspy.InputField(desc="gathered evidence; empty dict on cycle 1")
    rulings: list[FlagRuling] = dspy.OutputField(desc="exactly one ruling per flag, each with cited_evidence")


# ---------------------------------------------------------------------------
# Unstructured extraction (§4.2, §6) — messy ABHA note → structured condition
# ---------------------------------------------------------------------------
class ExtractCondition(dspy.Signature):
    """Extract the medical condition(s) stated in a free-text / scanned ABHA note
    so the deterministic R-010 crosswalk can compare them against the declaration.

    Emit only condition labels literally supported by the note text (e.g.
    'coronary artery disease', 'hypertension'); do NOT diagnose or infer beyond
    what the text says. Return an empty list if the note states no condition."""

    note_text: str = dspy.InputField(desc="the unstructured / free-text medical note")
    conditions: list[str] = dspy.OutputField(desc="condition labels literally supported by the note")


# ---------------------------------------------------------------------------
# LM configuration + prod/eval discipline (§6, §11)
# ---------------------------------------------------------------------------
def _eval_mode() -> bool:
    return os.environ.get("UW_EVAL_MODE", "").strip().lower() in ("1", "true", "yes")


def configure_lm(eval_mode: Optional[bool] = None) -> bool:
    """Configure the DSPy LM from .env with prod/eval discipline. Returns readiness.

    Prod  (eval_mode=False): response caching OFF, call-history retention OFF.
    Eval  (eval_mode=True) : caching ON (reproducible replay), history ON (debug).
    """
    model = os.environ.get("LLM_MODEL")
    if not model:
        return False
    if eval_mode is None:
        eval_mode = _eval_mode()

    api_base = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    kwargs = {"api_base": api_base} if api_base else {}
    # cache=False in prod: never serve a stale ruling for a changed proposal.
    # timeout: fail fast on an unreachable gateway instead of hanging the caller.
    lm = dspy.LM(model, cache=eval_mode, timeout=LLM_TIMEOUT_S, num_retries=0, **kwargs)
    dspy.configure(lm=lm)

    # DSPy keeps ~10k calls in lm.history by default → OOM in prod (files/CLAUDE.md
    # line 19). Disable retention explicitly in prod via BOTH knobs: the canonical
    # `disable_history` flag AND the size cap, so retention is off regardless of which
    # one this DSPy version honours. Verified: in prod a real call leaves history at 0.
    try:
        dspy.settings.configure(
            disable_history=not eval_mode,
            max_history_size=0 if not eval_mode else 10_000,
        )
    except Exception:
        # Older/newer DSPy without the knobs: cap the list on the LM directly.
        lm.history = lm.history if eval_mode else []
    return True


_LM_READY = configure_lm()
_judge = dspy.ChainOfThought(GreyZoneJudge)
_extractor = dspy.ChainOfThought(ExtractCondition)


def has_api_key() -> bool:
    """A key is present AND the LM is configured."""
    return _LM_READY and any(
        os.environ.get(k)
        for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
    )


def live_enabled() -> bool:
    """Gate for the live smoke test: a key AND explicit opt-in `UW_RUN_LIVE=1`.

    Requiring opt-in (not just a key) keeps a stray key in `.env` from hanging the
    default suite against an unreachable gateway — the live test is deliberately
    off unless you ask for it.
    """
    return has_api_key() and os.environ.get("UW_RUN_LIVE", "").strip().lower() in ("1", "true", "yes")


def _flags_as_dicts(flags: list) -> list[dict]:
    """Accept AmbiguousFlag models or plain dicts; validate at the trust boundary."""
    out = []
    for f in flags:
        out.append(f.model_dump() if isinstance(f, AmbiguousFlag) else AmbiguousFlag(**f).model_dump())
    return out


def run_judge(
    evidence_bundle: dict,
    flags: list,
    follow_up_observations: Optional[dict] = None,
) -> list[FlagRuling]:
    """One Judge call → one FlagRuling per flag (§6.1). Raises if no LM configured."""
    if not _LM_READY:
        raise RuntimeError("No LLM configured. Set LLM_MODEL and your provider key in .env")
    out = _judge(
        evidence_bundle=evidence_bundle,
        ambiguous_flags=_flags_as_dicts(flags),
        follow_up_observations=follow_up_observations or {},
    )
    return list(out.rulings)


def extract_condition(note_text: str) -> list[str]:
    """Unstructured-record path (§4.2): free-text ABHA note → condition labels."""
    if not _LM_READY:
        raise RuntimeError("No LLM configured. Set LLM_MODEL and your provider key in .env")
    return list(_extractor(note_text=note_text).conditions)


def model_name() -> Optional[str]:
    """The configured judge model id (for run_metadata stamping, §6/§11)."""
    return os.environ.get("LLM_MODEL")


def usage_since(baseline: int) -> dict:
    """Best-effort token/cost roll-up from lm.history entries added since `baseline`.

    History is OFF in prod (§6) so this returns zeros there — the model + prompt
    version are still stamped; capture is meaningful only in eval mode where history
    is on. `cost` is reliable; per-call token counts are best-effort (some DSPy
    adapter history entries carry cost but omit usage → tokens read 0).
    # ponytail: reads DSPy internals for the audit stamp; swap for a LiteLLM usage
    # callback if exact per-call tokens become a hard requirement.
    """
    lm = getattr(dspy.settings, "lm", None)
    hist = getattr(lm, "history", None) or []
    recent = hist[baseline:]
    in_tok = out_tok = 0
    cost = 0.0

    def _get(u, k):  # usage may be a dict OR a litellm Usage object
        if isinstance(u, dict):
            return u.get(k) or 0
        return getattr(u, k, 0) or 0

    for h in recent:
        u = (h or {}).get("usage") or {}
        in_tok += _get(u, "prompt_tokens")
        out_tok += _get(u, "completion_tokens")
        cost += (h or {}).get("cost", 0.0) or 0.0
    return {"input_tokens": in_tok, "output_tokens": out_tok, "total_cost_usd": round(cost, 6)}


def history_len() -> int:
    lm = getattr(dspy.settings, "lm", None)
    return len(getattr(lm, "history", None) or [])
