# Agent Build Specification — Onboarding Risk Assessment Agent
## Revised: staged pipeline, not a ReAct tool-loop — matched to a proven production pattern

**What changed and why:** the original version of this spec used `dspy.ReAct` — a general loop where the LLM freely picks from a toolset each cycle. A working, production-proven system (Agentic CVC, an insurance claims-correction pipeline) implements "the least agentic design" literally: narrow, single-purpose LLM calls (Classify, Digitize, Judge) interleaved with deterministic Python code that does the actual deciding, plus exactly one bounded retry loop for one named failure mode — never an open agent loop. This revision adopts that pattern.

---

## 1. The Agent as a Callable Service — unchanged in shape

```
POST /internal/v1/agent/onboarding-risk-assessment/invoke
Request body:  EvidenceBundle + AmbiguousFlags   (Section 2)
Response body: AgentResult                        (Section 3)
```

One endpoint, one request, one response, invoked by the orchestrator only when the BRE routes a case to GREY-ZONE. What's different is what runs *inside*: a staged pipeline (Section 6), not a tool-choosing loop.

---

## 2. Evidence Bundle & Ambiguous Flags — Input Schema

The BRE, deterministically, does not just say "GREY-ZONE" — it packages exactly which flagged items need judgment, mirroring `reconcile.build_operations` producing `ambiguous_groups`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AgentInvocation",
  "type": "object",
  "required": ["proposal_id", "evidence_bundle", "ambiguous_flags"],
  "properties": {
    "proposal_id": {"type": "string"},
    "evidence_bundle": { "$ref": "#/$defs/EvidenceBundle" },
    "ambiguous_flags": {
      "type": "array",
      "description": "deterministically identified by the BRE — the ONLY things the Judge is asked to rule on",
      "items": {
        "type": "object",
        "required": ["flag_id", "flag_type", "related_rule", "context"],
        "properties": {
          "flag_id": {"type": "string", "example": "flg_001"},
          "flag_type": {"enum": ["identity_mismatch", "income_thin_file", "velocity_anomaly", "occupation_ambiguity", "non_disclosure_signal"]},
          "related_rule": {"type": "string", "example": "R-015"},
          "context": {"type": "object", "description": "the specific slice of evidence_bundle relevant to THIS flag only — not the whole bundle repeated per flag"}
        }
      }
    },
    "follow_up_observations": {
      "type": "object",
      "description": "empty on the first pass; populated only if this is the single allowed re-Judge cycle (Section 6.3)",
      "default": {}
    }
  },
  "$defs": {
    "EvidenceBundle": { "type": "object", "description": "same structure as the prior revision — identity, income, product, enrichment, health_declaration, ml_scores. Unchanged; see git history if needed." }
  }
}
```

`EvidenceBundle` itself is unchanged from the prior revision — full facts, every enrichment flag, every ML score. What's new is `ambiguous_flags`: the BRE no longer just says "grey-zone," it says exactly *which* things are ambiguous and why, the same way `reconcile.build_operations` hands `JudgeStructure` a specific, bounded list of groups rather than the whole claim.

---

## 3. Agent Result — Output Schema, with per-stage cost accounting

Matches the observability discipline in the reference system's `run_metadata.json` — per-stage token/cost tracking is not optional at scale.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AgentResult",
  "type": "object",
  "required": ["proposal_id", "outcome", "rulings", "final_verdict"],
  "properties": {
    "proposal_id": {"type": "string"},
    "outcome": {"enum": ["resolved", "escalated"]},
    "rulings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["flag_id", "ruling", "cited_evidence", "reasoning", "cycle"],
        "properties": {
          "flag_id": {"type": "string"},
          "ruling": {"enum": ["benign_explained", "needs_income_corroboration", "needs_medical_check", "needs_identity_reverification", "unresolvable_escalate"]},
          "cited_evidence": {"type": "array", "items": {"type": "string"}, "description": "paths into evidence_bundle or follow_up_observations ONLY — grounding-checked deterministically"},
          "reasoning": {"type": "string"},
          "cycle": {"type": "integer", "description": "1 for the first Judge call, 2 if the single bounded re-Judge fired — never higher"}
        }
      }
    },
    "final_verdict": {
      "type": "object",
      "description": "produced ENTIRELY by deterministic code (Section 6.2) reading the rulings above — the LLM never emits this directly",
      "properties": {
        "verdict": {"enum": ["STEP-UP", "REFER", "DECLINE", null]},
        "confidence_band": {"enum": ["high", "medium", "low", null]},
        "escalation_reason": {"enum": ["unresolvable_ruling", "grounding_check_failed", "max_cycles_exceeded", null]}
      }
    },
    "run_metadata": {
      "type": "object",
      "description": "mirrors the reference system's run_metadata.json — required for cost control at scale",
      "properties": {
        "per_stage": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "properties": {
              "input_tokens": {"type": "integer"}, "output_tokens": {"type": "integer"},
              "cached_tokens": {"type": "integer"}, "calls": {"type": "integer"},
              "total_cost_usd": {"type": "number"}
            }
          },
          "example": {"judge_cycle_1": {"input_tokens": 2100, "output_tokens": 340, "cached_tokens": 0, "calls": 1, "total_cost_usd": 0.00041}}
        },
        "total_cost_usd": {"type": "number"},
        "latency_seconds": {"type": "number"},
        "tags": {"type": "array", "items": {"type": "string"}, "example": ["ALL_BENIGN", "REJUDGE_TRIGGERED", "NEEDS_HUMAN_REVIEW"]}
      }
    }
  }
}
```

---

## 4. Internal API Gateway Contracts — unchanged in shape from the prior revision

(All 15 pipeline-stage contracts — mobile vintage, PAN verify, e-KYC, CKYC, liveness, EPFO, GST/ITR, AA, MCA, geography, velocity graph, occupation class — remain exactly as previously specified: vendor-agnostic internal shape, adapter behind it per chosen vendor. See Section 1's "what I need from you" list above for which of these need real vendor docs before they stop being mocks.)

---

## 5. Deterministic Actions — triggered by the decision table, never chosen by the LLM

This is the key structural change from the prior revision. These are no longer "agent tools the LLM calls." They are actions **deterministic code invokes** based on what the Judge ruled — the same relationship `reconcile.choose_operations` has to `JudgeStructure`'s output.

| Action | Triggered when (deterministic condition) | Underlying call |
|---|---|---|
| `request_abha_consent()` | Decision table sees `ruling == "needs_medical_check"` on any flag, **and** this is cycle 1 (never triggered twice for the same case) | `POST /internal/v1/medical/abha-consent-request` — same contract as before |
| `trigger_rppg_scan()` | Same condition, when ABHA is declined/empty and rPPG is the configured fallback screening step | `POST /internal/v1/medical/rppg-scan-request` |
| `request_additional_document(doc_type)` | `ruling == "needs_income_corroboration"`, cycle 1 only; `doc_type` selected deterministically from the flag's `flag_type` (income flags → `recent_bank_statement`; occupation flags → `employer_letter`) | Per-doc-type WhatsApp templates, unchanged from prior revision |
| `request_identity_reverification()` | `ruling == "needs_identity_reverification"`, cycle 1 only | Re-triggers the N5 liveness/face-match step, not a new endpoint |
| *(no action)* | `ruling == "benign_explained"` or `"unresolvable_escalate"` | — these never trigger a follow-up; they go straight to the decision table's final verdict logic |

The decision table is a fixed lookup, not a model — this is the part that must stay boring and testable:

```python
def decide_next_step(rulings: list[FlagRuling], cycle: int) -> NextStep:
    if any(r.ruling == "unresolvable_escalate" for r in rulings):
        return NextStep.ESCALATE(reason="unresolvable_ruling")
    if cycle >= 2:
        # the single re-Judge cycle already ran; no further looping, ever
        unresolved = [r for r in rulings if r.ruling != "benign_explained"]
        if unresolved:
            return NextStep.ESCALATE(reason="max_cycles_exceeded")
        return NextStep.FINALIZE(verdict="STEP-UP" if any_followup_occurred else "STEP-UP")
    needs_income = [r for r in rulings if r.ruling == "needs_income_corroboration"]
    needs_medical = [r for r in rulings if r.ruling == "needs_medical_check"]
    needs_identity = [r for r in rulings if r.ruling == "needs_identity_reverification"]
    if needs_income or needs_medical or needs_identity:
        return NextStep.GATHER_EVIDENCE(needs_income, needs_medical, needs_identity)  # then re-Judge once, cycle=2
    return NextStep.FINALIZE(verdict="STEP-UP")  # all benign_explained
```

This is deliberately dumb and readable — exactly the point. The judgment lives in the Judge's rulings; the *consequences* of those rulings are a lookup table, not a second layer of AI decision-making.

---

## 6. The Staged Pipeline — literal implementation

### 6.1 Stage 1 — GreyZoneJudge (narrow, single call, one per case per cycle)

```python
import dspy
from pydantic import BaseModel
from typing import Literal

class AmbiguousFlag(BaseModel):
    flag_id: str
    flag_type: str
    related_rule: str
    context: dict

class FlagRuling(BaseModel):
    flag_id: str
    ruling: Literal["benign_explained", "needs_income_corroboration",
                     "needs_medical_check", "needs_identity_reverification",
                     "unresolvable_escalate"]
    cited_evidence: list[str]
    reasoning: str

class GreyZoneJudge(dspy.Signature):
    """Rule on EACH ambiguous flag from this grey-zone insurance proposal.
    One ruling per flag, in the SAME call — do not skip any flag.

    Use ONLY the evidence in evidence_bundle, the specific context given
    per flag, and — if this is a re-judge call — follow_up_observations
    gathered in the one permitted evidence-gathering cycle for this case.
    Never infer a fact not present in these inputs.

    A flag is benign_explained only if you can cite a SPECIFIC piece of
    evidence that resolves it — not an absence of concern. If nothing in
    the inputs resolves a flag and no evidence-gathering category applies,
    rule unresolvable_escalate rather than guessing benign."""

    evidence_bundle: dict = dspy.InputField()
    ambiguous_flags: list[AmbiguousFlag] = dspy.InputField()
    follow_up_observations: dict = dspy.InputField(desc="empty dict on cycle 1")

    rulings: list[FlagRuling] = dspy.OutputField()

judge = dspy.ChainOfThought(GreyZoneJudge)
# Optimized offline against a growing labeled regression set (Section 6.4)
# using dspy.MIPROv2 or dspy.GEPA — both are proven optimizers in the
# reference system; GEPA is worth A/B testing once enough labeled cases
# exist, per their production note that "optimizers like GEPA let us keep
# improving as prod data grows."
```

### 6.2 Stage 2 — deterministic decision table (Section 5) → either finalize or gather-once

No code here beyond the lookup table already shown in Section 5. This stage never calls an LLM.

### 6.3 Stage 3 — the ONE bounded evidence-gathering + re-Judge cycle (only if Stage 2 says so)

Mirrors the reference system's gap-closing loop exactly: bounded, single-purpose, triggered only by a specific named condition (there, "undercount"; here, a specific unresolved ruling type), never a general retry.

```python
def run_grey_zone_pipeline(evidence_bundle, ambiguous_flags):
    rulings = judge(evidence_bundle=evidence_bundle, ambiguous_flags=ambiguous_flags,
                     follow_up_observations={})
    next_step = decide_next_step(rulings.rulings, cycle=1)

    if next_step.kind == "GATHER_EVIDENCE":
        observations = {}
        for flag in next_step.needs_income:
            observations[flag.flag_id] = request_additional_document(
                doc_type=doc_type_for(flag), reason_for_request=flag.related_rule)
        for flag in next_step.needs_medical:
            observations[flag.flag_id] = request_abha_consent(reason_for_request=flag.related_rule)
        for flag in next_step.needs_identity:
            observations[flag.flag_id] = request_identity_reverification()

        # exactly ONE re-Judge call, with the gathered observations attached —
        # no further gathering happens after this, regardless of outcome
        rulings = judge(evidence_bundle=evidence_bundle, ambiguous_flags=ambiguous_flags,
                         follow_up_observations=observations)
        next_step = decide_next_step(rulings.rulings, cycle=2)

    return deterministic_final_gate(rulings.rulings, next_step, evidence_bundle)
```

### 6.4 Deterministic final validation gate

Mirrors the reference system's `validation` block (`matches_within_tolerance`, `net_consistency_ok`) — a hard, non-LLM check before anything is treated as resolved:

```python
def deterministic_final_gate(rulings, next_step, evidence_bundle) -> AgentResult:
    if next_step.kind == "ESCALATE":
        return AgentResult(outcome="escalated", rulings=rulings, final_verdict=None)

    # grounding check: EVERY cited_evidence path must resolve against the
    # actual evidence_bundle/observations sent — this is the equivalent of
    # their matches_within_tolerance check, and it is not optional
    for r in rulings:
        for path in r.cited_evidence:
            if not resolves(path, evidence_bundle):
                return AgentResult(outcome="escalated", rulings=rulings,
                                    final_verdict={"escalation_reason": "grounding_check_failed"})

    if all(r.ruling == "benign_explained" for r in rulings):
        return AgentResult(outcome="resolved", rulings=rulings,
                            final_verdict={"verdict": "STEP-UP", "confidence_band": "high"})
    return AgentResult(outcome="escalated", rulings=rulings, final_verdict=None)
```

Note what this deliberately does NOT do: it never asks the LLM "are you confident," and it never lets a self-reported confidence number stand in for a grounded, checkable fact. The reference system's own hard lesson: *"LLMs are very good at 'achieving' goals — even if it requires hallucinations... matching against claim amount has been a key check. But we find that in 10% of cases the claim amount itself is wrong."* The analogous discipline here: never trust the model's own "resolved" claim without deterministically checking every citation actually resolves.

---

## 7. One Fully Worked Example — the staged version

**Case:** Priya Sharma, self-employed, same facts as before (mobile ported 18 days ago, CKYC address mismatch, `self_employed_thin_file`, velocity-graph hit on another insurer's proposal 22 days ago).

**7.1 — BRE output (deterministic):**
```json
{
  "proposal_id": "PRP-2026-000123",
  "ambiguous_flags": [
    {"flag_id": "flg_001", "flag_type": "velocity_anomaly", "related_rule": "R-015",
     "context": {"related_proposals": [{"insurer": "other", "days_ago": 22, "sum_insured_band": "low", "product": "term"}]}},
    {"flag_id": "flg_002", "flag_type": "income_thin_file", "related_rule": "R-015",
     "context": {"income_source": "aa_fallback", "estimated_monthly_income": 42000}},
    {"flag_id": "flg_003", "flag_type": "identity_mismatch", "related_rule": "R-015",
     "context": {"ckyc_field": "address", "match": false}}
  ]
}
```

**7.2 — Judge, cycle 1:**
```json
{
  "rulings": [
    {"flag_id": "flg_001", "ruling": "benign_explained",
     "cited_evidence": ["ambiguous_flags[0].context.related_proposals[0]"],
     "reasoning": "The other proposal is a smaller-band term policy at a different insurer 22 days ago — consistent with comparison shopping, not cover-stacking.", "cycle": 1},
    {"flag_id": "flg_002", "ruling": "needs_income_corroboration",
     "cited_evidence": [], "reasoning": "AA-derived income estimate has no document corroboration yet.", "cycle": 1},
    {"flag_id": "flg_003", "ruling": "unresolvable_escalate",
     "cited_evidence": [], "reasoning": "No evidence-gathering category resolves an address-only CKYC mismatch; needs human judgment on whether it's material.", "cycle": 1}
  ]
}
```

**7.3 — Deterministic decision table (cycle 1):** `flg_003` rules `unresolvable_escalate` → **immediate ESCALATE**, per Section 5's first check. No follow-up cycle runs, no document request fires. This is a real, honest change from the prior revision's worked example — the more conservative, better-grounded design escalates a case the looser ReAct-loop version might have resolved with an under-justified STEP-UP.

**7.4 — Final `AgentResult`:**
```json
{
  "proposal_id": "PRP-2026-000123", "outcome": "escalated",
  "rulings": [ /* the three rulings above */ ],
  "final_verdict": null,
  "run_metadata": {"per_stage": {"judge_cycle_1": {"input_tokens": 1840, "output_tokens": 290, "calls": 1, "total_cost_usd": 0.00035}},
                    "total_cost_usd": 0.00035, "tags": ["MIXED_RULING", "ESCALATED_CYCLE_1"]}
}
```

This routes to N16 (human review) with the full ruling trace attached — the underwriter sees exactly what was resolved, what wasn't, and why, rather than a bare "REFER."

---

## 8. Open Decisions (business/procurement, not spec gaps)

- Real vendor per API row (Section 1's table).
- **LLM provider/model for the Judge calls** — the reference system runs Gemini 3.1 Flash Lite (medium thinking) in production, chosen on cost; DSPy's model-agnosticism makes this a genuinely cheap A/B, not a lock-in. Needs a decision before any real cost projection is possible.
- **DSPy version** — the reference system pins `3.3.0b1` (a beta). Decide whether to pin the same version for parity with proven behavior, or track latest stable — beta pins carry their own risk (see Section 10).
- **Optimizer choice** — MIPROv2 vs GEPA, worth A/B testing once a labeled regression set (Section 6.4/Section 10) exists; the reference system uses both.
- Actual underwriting thresholds, confidence-band definitions.

---

## 9. Self-Audit — updated

Same honest answer as before, with one addition: **this revision is more conservative than the last one, on purpose.** The staged pipeline with a hard-bounded single re-Judge cycle and a deterministic grounding gate will escalate more cases to human review than a freer ReAct loop would have. That's not a regression — it's the "least agentic" principle actually being enforced, matched to a system that has proven it in production rather than in design.

---

## 10. Production Learnings — bake these in from day one, not after an incident

Lifted directly from the reference system's hard-won gotchas — these are not generic advice, they're specific failures worth pre-empting:

- **DSPy retains call history by default (~10,000 calls) → memory blow-up in production.** This must be explicitly disabled in the prod path (`dspy.settings.configure(...)` history retention off, or equivalent for whatever DSPy version is pinned). Keep it on only in eval/dev where replaying history is useful.
- **Caching:** turn LLM response caching OFF in prod (you want fresh judgment on live cases), but keep it ON for the eval/regression harness (Section 6.4) so deterministic-code changes can be tested against the *same* labeled cases without re-spending on LLM calls every run — the reference system's test harness has an explicit `--no-cache` flag for the cases where you do need a fresh LLM call.
- **Cost discipline:** don't default to the largest/most expensive model. The reference system runs its narrow Judge-equivalent step on a small, cheap model with medium thinking, not a frontier model — "cost of agentic solutions have to be significantly lower than human costs, taking into account success rate." Test a small model first; only escalate to a larger one for cases the small model handles badly.
- **Token hygiene:** structured I/O, trimmed context, don't dump full documents/bundles when a relevant slice suffices — this is why `ambiguous_flags[i].context` is a slice of the evidence bundle, not the whole bundle repeated per flag.
- **Eval harness is not optional infrastructure — it's the thing that makes iteration safe.** Build a growing set of underwriter-labeled grey-zone cases (their "claim master," 1000+ cases) and re-run the Judge+decision-table pipeline against all of them on every code change, using cached LLM responses for speed. Every time a strange real case is found in production, add it to this set immediately — don't just fix the code and move on.
- **Observability:** every run should produce something like the `run_metadata` block in Section 3 — per-stage tokens/cost, tags, and a full backtraceable reasoning path. This is what makes an escalated or auto-decided case explainable after the fact, and it's what "audit trail" concretely means in implementation, not just in policy language.
- **Working with Claude Code on this:** keep chat turns quick (under ~5 minutes) to benefit from prompt caching; if you need longer to review a design or test results, consider starting a fresh session rather than leaving a stale, expensive context sitting idle. Build the test environment so Claude Code can run tests end-to-end itself, not just write code you test manually — per the reference team's own note, "if your test env is robust and the agent can independently run it, 90% of your job is done."
