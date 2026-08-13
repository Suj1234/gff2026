"""report.py — assemble the full combined output report (Appendix A / §8).

Layer 5. Everything the report needs is already computed by the pipeline
(`PipelineResult`): BRE outcome + flags, risk scores, safety score + per-source
breakdown, the final decision, judge rulings, and the run-metadata stamp. This
module is pure assembly + two derived pieces:

  - `sections` — I-Adore-style per-source risk levels. The risk LEVEL is a
    judgment we PRODUCE (§1.8), derived deterministically from the safety-score
    sub-scores the scorer already computed — one of Low / Moderate / High from
    the band cutoffs (§4A). We do NOT rebuild any analyzer — the facts flow
    through from the input signals; only the level label is ours.
  - `audit_log` — the append-only trail (§11): every stage that ran, in order,
    with a reason. Timestamps are derived from the input's `meta.received_at`
    (a FACT in the bundle), never wall-clock — so the same input → byte-identical
    report on retry (idempotency, §11). No `datetime.now()` anywhere.

Robustness (§11) baked in:
  - Version stamps (rules/prompt/model) on every report via `RunMetadata`.
  - Partial data: a missing source never crashes assembly — it still produces a
    section (the scorer defaults an absent source to its clean sub-score today;
    see the KNOWN LIMITATION below).
  - Idempotency: this assembly is a pure function of the pipeline result — no
    wall-clock, no randomness. NOTE the report still carries the LLM cost/token
    counts from `run_metadata`, so two *real* LLM runs of the same proposal can
    differ in those fields even when the decision is identical. The §11
    guarantee is on the DECISION (stable on retry), not byte-identity of the
    whole report.

KNOWN LIMITATION (flagged for Phase 5, scoring layer): a source that sent NO
facts is scored as clean (sub_score ~100 → "Low"), and its `findings` text can
assert a clean state that was never observed (e.g. "labs in range" with zero
labs). That is a Phase-2 `scoring.py` behavior — missing should read as
"unavailable / not assessed", not "safe". `_level` here faithfully reflects
whatever sub_score the scorer produced; it does not (yet) distinguish
absent-source from assessed-clean. Do not read a "Low" section as evidence the
source was checked.
"""

from __future__ import annotations

from typing import Any, Optional

from . import config as C
from .judge import PROMPT_VERSION
from .schemas import (
    AuditEntry,
    CitedEvidence,
    Decision,
    ProposalInput,
    ReportOutput,
    RiskScores,
    RunMetadata,
    SafetyScore,
    SectionEvaluation,
)

# The safety-score source groups → the report section name they map to, plus a
# short reason phrase used when the section is unavailable. Sections are the
# I-Adore surface; the level comes from the group's sub-score band.
_GROUP_TO_SECTION = {
    "identity_kyc": "identity_checks",
    "contactability": "contactability",
    "occupation_employer": "occupation",
    "financial": "financial_evaluation",
    "lifestyle": "lifestyle_analysis",
    "medical": "medical_evaluation",
    "velocity_graph": "velocity_graph",
    "geography": "geography",
    "litigation_fir": "litigation_fir",
    "fraud_check": "fraud_check",
    "insurance_portfolio": "insurance_portfolio_iib",
}


def _level(sub_score: float, assessed: bool = True) -> str:
    """Map a 0-100 sub-score to a risk LEVEL label (inverse of safety band).

    Higher sub-score = safer = Low risk. Uses the same band cutoffs as the
    composite safety score (§4A) so section levels and the overall score agree.

    `assessed=False` (the source never arrived) returns "Not Assessed" — NOT "Low".
    This is the fix for the absent-source bug: a section for a source that was never
    checked must not read as a clean/Low result asserting a state never observed.
    """
    if not assessed:
        return "Not Assessed"
    band = C.safety_band(sub_score)
    return {"Low Risk": "Low", "Moderate Risk": "Moderate", "High Risk": "High"}[band]


def _report_meta(inp: ProposalInput) -> dict[str, Any]:
    """Header block (§8 report_meta) — pulled straight from the declared facts.

    Every field is optional-safe: a partial bundle still produces a header.
    """
    app = inp.application
    a = app.applicant
    return {
        "applicant_name": a.name,
        "application_no": inp.proposal_id,
        "product_name": (inp.meta or {}).get("product_name") or (inp.meta or {}).get("insurer"),
        "sum_assured": app.product.sum_assured,
        "premium": app.product.premium,
        "report_date": (inp.meta or {}).get("received_at"),
        "profile": {
            "gender": a.gender,
            "age": a.age,
            "marital_status": a.marital_status,
            "location": a.pincode,
            "occupation_type": (app.occupation.declared_type if app.occupation else None),
        },
    }


def _sections(breakdown) -> dict[str, SectionEvaluation]:
    """Per-source sections with derived risk levels (§8 sections).

    Every scored group yields a section, so a partial bundle still produces a full
    sections block rather than missing keys — never a crash (§11). An UNASSESSED
    group (source never arrived) is now surfaced as risk_level "Not Assessed" with
    `assessed=False`, NOT a clean "Low" — the fix for the KNOWN LIMITATION where an
    absent source read as a clean, checked result.
    """
    out: dict[str, SectionEvaluation] = {}
    for row in breakdown:
        name = _GROUP_TO_SECTION.get(row.source_group, row.source_group)
        out[name] = SectionEvaluation(
            risk_level=_level(row.risk_sub_score, row.assessed),
            sub_score=row.risk_sub_score,
            weight=row.weight,
            findings=row.why,
            assessed=row.assessed,
        )
    return out


def _risk_and_fraud_verdict(bre, risk: RiskScores) -> dict[str, Any]:
    """The narrative verdict block (§8). Built from the flags + scores we produced."""
    flag_types = {f.flag_type for f in bre.soft_flags}
    return {
        "risk_summary": _join_reasons(bre, ("R-009", "R-010", "R-017")) or "No elevated clinical risk flags.",
        "fraud_summary": _join_reasons(bre, ("R-001", "R-002", "R-003", "R-013"))
        or "No synthetic-identity or tampering signal.",
        "non_disclosure": "non_disclosure_signal" in flag_types,
        "confidence_band": risk.composite_band or "low",
    }


def _join_reasons(bre, rule_prefixes: tuple[str, ...]) -> str:
    parts = [
        f.reason
        for f in bre.soft_flags
        if any(f.related_rule.startswith(p) for p in rule_prefixes)
    ]
    return " ".join(parts)


def _cited_chain(rulings) -> list[CitedEvidence]:
    """Flatten the judge rulings into the evidence chain (§8 cited_evidence_chain)."""
    chain: list[CitedEvidence] = []
    for r in rulings:
        for src in r.cited_evidence:
            chain.append(CitedEvidence(claim=r.flag_id, cited_source=src, ruling=r.ruling))
    return chain


def _audit_log(inp: ProposalInput, bre, decision: Decision, judge_cycles: int) -> list[AuditEntry]:
    """Append-only audit trail (§11). Deterministic timestamps derived from the
    input's received_at (a FACT) so the report is idempotent on retry."""
    ts = (inp.meta or {}).get("received_at")
    log: list[AuditEntry] = [
        AuditEntry(
            step="bre", actor="system", timestamp=ts,
            detail=f"BRE outcome {bre.outcome}"
            + (f" (hard gate {bre.hard_gate})" if bre.hard_gate else "")
            + (f"; {len(bre.ambiguous_flags)} ambiguous flag(s)" if bre.ambiguous_flags else ""),
        )
    ]
    for cyc in range(1, judge_cycles + 1):
        log.append(AuditEntry(
            step=f"judge_cycle_{cyc}", actor="agent", timestamp=ts,
            detail="grey-zone flags ruled" + (" (re-judge after one gather cycle)" if cyc == 2 else ""),
        ))
    log.append(AuditEntry(
        step="decision", actor="system", timestamp=ts,
        detail=f"{decision.verdict}"
        + (f" — {decision.escalation_reason}" if decision.escalation_reason else "")
        + (f" — {decision.next_step}" if decision.next_step else ""),
    ))
    return log


def _tags(decision: Decision, bre) -> list[str]:
    tags = [decision.verdict]
    if decision.escalation_reason:
        tags.append(decision.escalation_reason.upper())
    for f in bre.soft_flags:
        tags.append(f.flag_type.upper())
    # Preserve order, drop dups.
    seen: set[str] = set()
    return [t for t in tags if not (t in seen or seen.add(t))]


def build_report(result) -> ReportOutput:
    """Assemble a `PipelineResult` into the full Appendix-A `ReportOutput`.

    Pure function of the pipeline result — no I/O, no clock, no randomness. Same
    input → same report (idempotency, §11).
    """
    inp: ProposalInput = result.input
    bre = result.bre
    decision: Decision = result.decision
    safety: SafetyScore = result.safety_score
    risk: RiskScores = result.risk_scores

    run_meta = result.run_meta or {}
    metadata = RunMetadata(
        rules_version=C.RULES_VERSION,
        prompt_version=PROMPT_VERSION if result.judge_cycles else None,
        model=run_meta.get("model") if result.judge_cycles else None,
        total_cost_usd=run_meta.get("total_cost_usd", 0.0),
        tags=_tags(decision, bre),
        judge_cycles=result.judge_cycles,
        input_tokens=run_meta.get("input_tokens", 0),
        output_tokens=run_meta.get("output_tokens", 0),
    )

    return ReportOutput(
        report_meta=_report_meta(inp),
        safety_score=safety,
        scoring_breakdown=result.scoring_breakdown,
        scoring_total=result.scoring_total,
        signals=inp.signals.model_dump(),
        sections=_sections(result.scoring_breakdown),
        risk_scores=risk,
        bre_result=bre,
        risk_and_fraud_verdict=_risk_and_fraud_verdict(bre, risk),
        decision=decision,
        cited_evidence_chain=_cited_chain(result.rulings),
        run_metadata=metadata,
        audit_log=_audit_log(inp, bre, decision, result.judge_cycles),
    )
