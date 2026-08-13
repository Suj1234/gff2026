"""decision.py — the deterministic decision mapper (Core 6) + decide_next_step.

Maps a `BreResult` (+ optional LLM rulings from Phase 3) to ONE final decision
from the Core 6 (IMPLEMENTATION_PLAN.md §7). First matching row wins.

Phase 1 scope: the NON-LLM rows — 1-6 (all reachable from rules alone), plus the
LLM-terminal rows 9-10 (still deterministic code: they read the Judge's rulings).
Rows 7-8 (LLM rules all benign → ISSUE; needs-a-doc → STEP_UP) are wired here too
so the mapper is complete, but they only activate once Phase 3 supplies rulings;
with no rulings the mapper stops at the grey-zone→REFER/STEP_UP deterministic edge.

Hard lines (§7, §1.6):
  - Rows 1-6 never call the LLM.
  - DECLINE only ever from row 1 (a deterministic hard gate).
  - The LLM never sets a loading number — row 5's % comes from the actuarial table
    (rules.py R-009 → config loading matrix).
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel

from . import config as C
from .schemas import BreResult, Decision, FlagRuling, Outcome


# ===========================================================================
# decide_next_step — the pipeline router (§7.1); cycle capped at 2
# ===========================================================================
class NextStep(BaseModel):
    kind: Literal["FINALIZE", "GATHER_EVIDENCE", "ESCALATE"]
    reason: Optional[str] = None
    gather: list[str] = []  # flag_ids needing one round of evidence


# Map a Judge ruling → the real gather action (§7.1). The ACTION is real code
# (Phase 3 wires the gateway calls); the vendor response is mocked in dev.
# LIFE: the medical gather is a tele-MER (the life evidence channel); a health
# deployment would map `needs_medical_check` to `request_abha_consent()` instead —
# same ruling, deployment-specific action (no health fixture depends on the ABHA key).
RULING_TO_ACTION = {
    "needs_income_corroboration": "request_additional_document(bank_statement)",
    "needs_medical_check": "request_medical_exam(tele_mer)",
    "needs_identity_reverification": "request_identity_reverification()",
}


def decide_next_step(rulings: list[FlagRuling], cycle: int) -> NextStep:
    """FINALIZE | GATHER_EVIDENCE | ESCALATE. One re-Judge cycle only (cap 2)."""
    if any(r.ruling == "unresolvable_escalate" for r in rulings):
        return NextStep(kind="ESCALATE", reason="unresolvable_ruling")
    unresolved = [r for r in rulings if r.ruling != "benign_explained"]
    if cycle >= 2:  # the single re-Judge cycle already ran; never loop further
        if unresolved:
            return NextStep(kind="ESCALATE", reason="max_cycles_exceeded")
        return NextStep(kind="FINALIZE")
    if unresolved:
        return NextStep(kind="GATHER_EVIDENCE", gather=[r.flag_id for r in unresolved])
    return NextStep(kind="FINALIZE")


# ===========================================================================
# Loading — the actuarial % is deterministic (never the LLM), band as a string
# ===========================================================================
def _loading_band(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    lo = int(pct)
    hi = lo + 20  # indicative band around the computed point  # TODO(underwriting-manual)
    return f"{lo}-{hi}%"


# ===========================================================================
# Grounding gate (§7.1, §11, files/CLAUDE.md line 18) — every cited_evidence path
# must resolve against the REAL bundle, INCLUDING on the escalate path (the fix).
# ===========================================================================
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


def coverage_ok(flags: list, rulings: list[FlagRuling]) -> bool:
    """The judge returned exactly one ruling per raised flag, ids matching (§6).

    Guards the trust boundary between the (LLM) judge and the deterministic mapper:
    the prompt asks the model to rule on every flag, but a malformed response —
    dropped flag, hallucinated/extra flag_id, duplicate, or empty set while flags
    exist — must NOT be trusted to ISSUE. Any mismatch → caller REFERs. When there
    are no flags at all, there is nothing to cover (vacuously true).
    """
    flag_ids = [getattr(f, "flag_id", None) if not isinstance(f, dict) else f.get("flag_id")
                for f in (flags or [])]
    flag_set = {fid for fid in flag_ids if fid}
    ruling_ids = [r.flag_id for r in rulings]
    if not flag_set:
        return not ruling_ids  # no flags → the judge must not invent rulings either
    if len(ruling_ids) != len(set(ruling_ids)):
        return False  # a duplicate ruling for one flag
    return set(ruling_ids) == flag_set  # exact 1:1 coverage, no missing / no extra


def grounding_ok(rulings: list[FlagRuling], root: dict) -> bool:
    """Every cited_evidence path across ALL rulings resolves against the bundle.

    THE FIX (§7.1): this runs on the whole ruling set — the escalate path is checked
    too, so a hallucinated escalation reason can't smuggle in a fabricated citation.
    A ruling with no citations is not auto-trusted: `benign_explained` MUST cite
    (the Judge prompt requires it); an uncited benign ruling fails grounding.
    """
    for r in rulings:
        if r.ruling == "benign_explained" and not r.cited_evidence:
            return False  # benign requires a specific cited fact (§6)
        for path in r.cited_evidence:
            if not _resolve(path, root):
                return False
    return True


# ===========================================================================
# Confidence gate (§11) — calibrated, NOT the model's self-report. Low → REFER.
# ===========================================================================
# Confidence is a deterministic function of how cleanly the rulings + citations
# resolved, calibrated against the eval set — never a number the LLM reports.
# ponytail: coverage-ratio proxy; recalibrate the cutoff against eval outcomes.
CONFIDENCE_MIN = 0.60  # TODO(underwriting-manual): calibrate vs the labeled eval set, not model self-report


def confidence(rulings: list[FlagRuling], root: dict) -> float:
    """0-1 confidence: fraction of rulings that are decisive AND fully grounded.

    A ruling is decisive+grounded when it is not `unresolvable_escalate` and every
    cited path resolves. All-escalate or ungrounded → low confidence → REFER.
    """
    if not rulings:
        return 1.0  # nothing to resolve
    good = 0
    for r in rulings:
        if r.ruling == "unresolvable_escalate":
            continue
        if r.cited_evidence and all(_resolve(p, root) for p in r.cited_evidence):
            good += 1
        elif not r.cited_evidence and r.ruling != "benign_explained":
            # a needs_* ruling legitimately has nothing to cite yet — count it decisive
            good += 1
    return good / len(rulings)


# ===========================================================================
# The Core-6 decision mapper (§7). First matching row wins.
# ===========================================================================
def map_decision(
    bre: BreResult,
    rulings: Optional[list[FlagRuling]] = None,
    evidence_root: Optional[dict] = None,
) -> Decision:
    """Map BRE outcome (+ optional Phase-3 rulings) → one Core-6 Decision.

    `evidence_root` is the real bundle the citations are checked against (the
    pipeline passes it in Phase 3). When rulings are present it MUST be supplied so
    the grounding + confidence gates can run — see `_map_grey_zone_with_rulings`.
    """

    # --- Row 1: fraud / failed liveness / invalid identity → DECLINE (only source) ---
    if bre.outcome == "DECLINE":
        return Decision(
            verdict=Outcome.DECLINE.value,
            escalation_reason=bre.hard_gate,
            reason_summary=f"Deterministic hard gate {bre.hard_gate}: identity fraud / "
                           f"invalid identity / failed liveness → decline.",
            reason_codes=bre.reason_codes,
        )

    # --- Rows 2-3: AML/PEP/sanctions OR age/SI outside band → REFER (hard gate) ---
    if bre.outcome == "REFER" and bre.hard_gate:
        return Decision(
            verdict=Outcome.REFER.value,
            escalation_reason=bre.hard_gate,
            reason_summary=f"Deterministic hard gate {bre.hard_gate}: compliance "
                           f"(AML/PEP/sanctions) or age/SI outside band → manual underwriter.",
            reason_codes=bre.reason_codes,
        )

    # --- Row 4: recent medical event inside postpone window → POSTPONE ---
    if bre.outcome == "POSTPONE":
        return Decision(
            verdict=Outcome.POSTPONE.value,
            escalation_reason="recent_medical_event",
            next_step=f"re_evaluate_after_{C.POSTPONE_REEVALUATE_MONTHS}_months",
            reason_summary="Recent acute medical event / pregnancy inside the postpone "
                           "window → postpone and re-evaluate.",
            reason_codes=bre.reason_codes,
        )

    # --- Grey-zone handling: if Phase-3 rulings are present, apply rows 7-10 ---
    if bre.outcome == "GREY-ZONE" and rulings is not None:
        return _map_grey_zone_with_rulings(bre, rulings, evidence_root or {})

    # --- Row 5: BMI×age×occupation exceeds standard matrix, otherwise acceptable → STEP_UP ---
    #     (beyond-matrix step-up with no other surviving soft flags)
    if bre.outcome == "GREY-ZONE" and not bre.soft_flags:
        return Decision(
            verdict=Outcome.STEP_UP.value,
            next_step="request_medicals_or_underwriter_review",
            escalation_reason="beyond_standard_matrix",
            reason_summary="Risk factors exceed the standard loading matrix → step-up "
                           "for medicals / underwriter review.",
            reason_codes=bre.reason_codes,
        )

    # --- Grey-zone WITHOUT rulings (Phase 1 deterministic edge): route to REFER ---
    #     Phase 3 replaces this with the LLM judge (rows 7-10). Until then, a case
    #     the rules cannot resolve goes to a human — never auto-issued, never declined.
    if bre.outcome == "GREY-ZONE":
        return Decision(
            verdict=Outcome.REFER.value,
            escalation_reason="grey_zone_unresolved_no_llm",
            indicative_loading_if_cleared=_loading_band(bre.loading_pct),
            reason_summary="Grey-zone: rules detected conflicts/gaps they cannot resolve "
                           f"({len(bre.soft_flags)} soft flag(s)). Phase 1 has no LLM → "
                           "route to a human underwriter.",
            reason_codes=bre.reason_codes,
        )

    # --- Row 5 (pure loading, no grey-zone): ISSUE_WITH_LOADING ---
    if bre.outcome == "LOADING":
        return Decision(
            verdict=Outcome.ISSUE_WITH_LOADING.value,
            loading_pct=bre.loading_pct,
            indicative_loading_if_cleared=_loading_band(bre.loading_pct),
            reason_summary=f"Acceptable with a standard-matrix loading of "
                           f"+{bre.loading_pct:g}% (actuarial table set the number).",
            reason_codes=bre.reason_codes,
        )

    # --- Row 6: all checks clean, low score, zero flags → ISSUE ---
    if bre.outcome == "CLEAN":
        return Decision(
            verdict=Outcome.ISSUE.value,
            reason_summary="All checks clean, low risk score, zero soft flags → auto-issue.",
            reason_codes=bre.reason_codes or ["R-014-auto-issue"],
        )

    # Defensive default: never auto-issue or decline an unrecognized state.
    return Decision(
        verdict=Outcome.REFER.value,
        escalation_reason="unrecognized_bre_outcome",
        reason_summary=f"Unrecognized BRE outcome '{bre.outcome}' → refer for safety.",
        reason_codes=bre.reason_codes,
    )


def _map_grey_zone_with_rulings(
    bre: BreResult, rulings: list[FlagRuling], evidence_root: dict
) -> Decision:
    """Rows 7-10 (§7): apply the LLM judge's rulings deterministically.

    This is deterministic code reading the Judge output — the LLM never emits the
    final verdict itself. Phase 3 supplies `rulings`; the loading % (if any) still
    comes only from the actuarial table (bre.loading_pct), never from the LLM.

    Order (§7.1, §11) — each is a REFER short-circuit, most-authoritative first:
      0. Coverage gate — the judge must return exactly one ruling per raised flag,
         with matching flag_ids. A skipped flag, an extra/hallucinated flag_id, a
         duplicate, or an empty ruling set → REFER `ruling_coverage_failed`. Without
         this, a judge that silently drops a flag could auto-ISSUE an unaddressed
         grey-zone signal (§1.1/§6: rule on EVERY flag, never guess).
      1. Grounding gate — every cited_evidence path must resolve, on ALL rulings
         (including the escalate path — the fix). A fabricated citation → REFER
         with `grounding_check_failed`, never trusted through.
      2. Row 10 — an explicit, grounded `unresolvable_escalate` is a decisive
         ruling; it keeps its own reason (checked before the confidence gate so a
         deliberate escalate isn't relabelled `low_confidence`).
      3. Confidence gate — calibrated (not model self-report); low → REFER.
    """
    # --- Gate 0: coverage — one ruling per raised flag, ids must match exactly ---
    if not coverage_ok(bre.ambiguous_flags, rulings):
        return Decision(
            verdict=Outcome.REFER.value,
            escalation_reason="ruling_coverage_failed",
            reason_summary="The judge did not return exactly one grounded ruling per "
                           "grey-zone flag (missing, extra, duplicate, or mismatched "
                           "flag_id) → refer; an unaddressed flag is never auto-issued.",
            reason_codes=bre.reason_codes,
        )

    # --- Gate 1: grounding (the fix — runs on every ruling, escalate included) ---
    if not grounding_ok(rulings, evidence_root):
        return Decision(
            verdict=Outcome.REFER.value,
            escalation_reason="grounding_check_failed",
            reason_summary="A cited evidence path did not resolve against the real "
                           "bundle (or a benign ruling cited nothing) → refer; the "
                           "LLM ruling is not trusted.",
            reason_codes=bre.reason_codes,
        )

    # --- Row 10: any grounded unresolvable_escalate → REFER (its own reason) ---
    if any(r.ruling == "unresolvable_escalate" for r in rulings):
        return Decision(
            verdict=Outcome.REFER.value, escalation_reason="unresolvable_escalate",
            reason_summary="LLM ruled a flag unresolvable → refer to underwriter.",
            reason_codes=bre.reason_codes,
        )

    # --- Gate 2: calibrated confidence (§11); low → REFER ---
    conf = confidence(rulings, evidence_root)
    if conf < CONFIDENCE_MIN:
        return Decision(
            verdict=Outcome.REFER.value,
            escalation_reason="low_confidence",
            reason_summary=f"Judge confidence {conf:.2f} < {CONFIDENCE_MIN} "
                           "(calibrated cutoff) → refer to a human underwriter.",
            reason_codes=bre.reason_codes,
        )

    ns = decide_next_step(rulings, cycle=2 if bre_gathered(bre) else 1)

    # Row 8: a flag needs a document / medical record → STEP_UP (gather once).
    if ns.kind == "GATHER_EVIDENCE":
        needs = {r.ruling for r in rulings if r.ruling != "benign_explained"}
        actions = sorted({RULING_TO_ACTION[n] for n in needs if n in RULING_TO_ACTION})
        return Decision(
            verdict=Outcome.STEP_UP.value,
            next_step="; ".join(actions) if actions else "gather_evidence",
            reason_summary="LLM flagged evidence gaps → step-up (gather once, then re-judge).",
            reason_codes=bre.reason_codes,
        )

    # Row 9: after the one step-up cycle, still unresolved → REFER.
    if ns.kind == "ESCALATE":
        return Decision(
            verdict=Outcome.REFER.value, escalation_reason=ns.reason,
            reason_summary="Still unresolved after one evidence-gathering cycle → refer.",
            reason_codes=bre.reason_codes,
        )

    # Row 7: LLM rules all flags benign_explained → resolve. If a loading applies,
    # it is ISSUE_WITH_LOADING (actuarial %); else ISSUE.
    if bre.loading_pct and bre.loading_pct > 0:
        return Decision(
            verdict=Outcome.ISSUE_WITH_LOADING.value,
            loading_pct=bre.loading_pct,
            indicative_loading_if_cleared=_loading_band(bre.loading_pct),
            reason_summary="LLM resolved all flags benign; standard-matrix loading applies.",
            reason_codes=bre.reason_codes,
        )
    return Decision(
        verdict=Outcome.ISSUE.value,
        reason_summary="LLM resolved every grey-zone flag as benign_explained → issue.",
        reason_codes=bre.reason_codes,
    )


def bre_gathered(bre: BreResult) -> bool:
    """Whether an evidence-gathering cycle already ran (set by the pipeline, Phase 3)."""
    return bool(getattr(bre, "gathered", False))
