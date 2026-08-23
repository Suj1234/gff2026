"""engine.py — the health-triage agent's state machine (HEALTH_AGENT_PLAN.md §3-§4.2).

Four layers, cleanly separable for testing (mirrors underwriting.judge's pattern:
module-level `dspy.ChainOfThought` instances behind thin functions that raise if no LM
is configured):

  1. `run_triage(...)`         — Phase 1, one call, `_safe_triage` bounds its output.
  2. `step_thread(...)`        — Phase 2, ONE TURN of one condition thread, pure/stateless
                                  (takes + returns a plain-dict state) — this is what a real
                                  HTTP endpoint calls once per request (step_routes.py's
                                  thread/start + thread/answer), since a server can't block
                                  a function call waiting for the applicant's next HTTP
                                  request the way a test's `answer_callback` can.
  3. `run_condition_thread(...)` — a blocking loop over `step_thread`, for offline testing
                                  and the eval/live-smoke harness (§9) where a synchronous
                                  `answer_callback` can stand in for the real turn-by-turn
                                  HTTP round-trip.
  4. `run_all_threads(...)`    — the orchestrator: one thread per flagged bucket, then
                                  EXACTLY ONE bounded second triage pass (§4.2) for
                                  anything volunteered along the way.

Every state-machine function takes its DSPy caller as an injectable parameter
(`triage_fn`, `next_question_fn`, `summarize_fn`) defaulting to the real module-level
callables — this is what makes `test_health_agent_*.py` (§9) able to feed canned
responses without a network call, exactly like `judge.py`'s own tests.
"""

from __future__ import annotations

from typing import Callable, Optional

import dspy

from .config import (
    CONDITION_BUCKETS,
    MAX_CONDITIONS_PROBED,
    MAX_SECOND_PASS_BUCKETS,
    MAX_TURNS_PER_CONDITION,
)
from .lm import configure_lm
from .signatures import (
    NextAdaptiveQuestion,
    SummarizeConditionThread,
    TriageConditions,
    VerifyThreadComplete,
)

_LM_READY = configure_lm()
_triage_module = dspy.ChainOfThought(TriageConditions)
_next_question_module = dspy.ChainOfThought(NextAdaptiveQuestion)
_verify_module = dspy.ChainOfThought(VerifyThreadComplete)
_summarize_module = dspy.ChainOfThought(SummarizeConditionThread)

_KNOWN_BUCKETS = set(CONDITION_BUCKETS)


def _require_lm() -> None:
    if not _LM_READY:
        raise RuntimeError("No LLM configured. Set LLM_MODEL and your provider key in .env")


# ---------------------------------------------------------------------------
# Real DSPy call wrappers — thin, raise if no LM (same discipline as judge.py)
# ---------------------------------------------------------------------------
def _real_triage(
    condition_catalog: list[str],
    face_scan_facts: dict,
    abha_facts: dict,
    prescription_facts: dict,
    volunteered_text: list[str],
) -> list[dict]:
    _require_lm()
    out = _triage_module(
        condition_catalog=condition_catalog,
        face_scan_facts=face_scan_facts or {},
        abha_facts=abha_facts or {},
        prescription_facts=prescription_facts or {},
        volunteered_text=volunteered_text or [],
    )
    return list(out.flagged)


def _real_next_question(
    condition_label: str,
    trigger_fact: str,
    info_targets: list[str],
    conversation_so_far: list[dict],
    turns_used: int,
    max_turns: int,
):
    _require_lm()
    return _next_question_module(
        condition_label=condition_label,
        trigger_fact=trigger_fact,
        info_targets=info_targets,
        conversation_so_far=conversation_so_far,
        turns_used=turns_used,
        max_turns=max_turns,
    )


def _real_verify(
    condition_label: str,
    info_targets: list[str],
    covered_targets: list[str],
    conversation_so_far: list[dict],
):
    _require_lm()
    return _verify_module(
        condition_label=condition_label,
        info_targets=info_targets,
        covered_targets=covered_targets,
        conversation_so_far=conversation_so_far,
    )


def _real_summarize(
    condition_label: str,
    conversation_so_far: list[dict],
    ended_reason: str,
    uncovered_targets: list[str],
):
    _require_lm()
    return _summarize_module(
        condition_label=condition_label,
        conversation_so_far=conversation_so_far,
        ended_reason=ended_reason,
        uncovered_targets=uncovered_targets,
    )


# ---------------------------------------------------------------------------
# Phase 1 — Triage (HEALTH_AGENT_PLAN.md §3)
# ---------------------------------------------------------------------------
def _safe_triage(raw: list[dict]) -> list[dict]:
    """Defensive bounding on triage output (same discipline as `_safe_extract` in
    `underwriting/rules.py:601-613`) — ABHA free-text and OCR'd prescription text are
    untrusted DATA that could carry adversarial instructions; this makes sure the
    LLM's OUTPUT is bounded regardless of what its input contained."""
    out = []
    for item in (raw or [])[:MAX_CONDITIONS_PROBED]:
        if not isinstance(item, dict):
            continue
        bucket = str(item.get("bucket", "")).strip()
        if bucket not in _KNOWN_BUCKETS:
            continue  # never trust a bucket name the LLM invented
        confidence = item.get("confidence")
        out.append({
            "bucket": bucket,
            "trigger_fact": str(item.get("trigger_fact", ""))[:200],
            "confidence": confidence if confidence in ("high", "medium", "low") else "low",
        })
    return out


def run_triage(
    face_scan_facts: Optional[dict] = None,
    abha_facts: Optional[dict] = None,
    prescription_facts: Optional[dict] = None,
    volunteered_text: Optional[list[str]] = None,
    *,
    triage_fn: Optional[Callable] = None,
) -> list[dict]:
    """One triage call -> bounded list of flagged condition buckets. `triage_fn` is
    injectable for offline testing (defaults to the real DSPy call).

    NOTE: the default is resolved at CALL time (`triage_fn or _real_triage`), not bound
    as a mutable default argument — a `= _real_triage` default would capture the
    function object at import time, so `monkeypatch.setattr(engine, "_real_triage", ...)`
    in a test would silently have no effect (a real bug caught while wiring the API
    endpoints: `journey/step_routes.py`'s call sites never pass `triage_fn` explicitly,
    so they'd otherwise always hit the real LLM even under a test patch)."""
    fn = triage_fn or _real_triage
    raw = fn(
        list(CONDITION_BUCKETS),
        face_scan_facts or {},
        abha_facts or {},
        prescription_facts or {},
        volunteered_text or [],
    )
    return _safe_triage(raw)


# ---------------------------------------------------------------------------
# Phase 2 — per-condition adaptive conversation thread (HEALTH_AGENT_PLAN.md §4)
# ---------------------------------------------------------------------------
def new_thread_state(bucket_key: str, trigger_fact: str) -> dict:
    """The initial, serializable state for one condition thread — this is exactly what
    a caller (the API layer, or the test-loop below) persists between turns. Every key
    is a plain str/list/dict/int/bool so it round-trips through the journey `bundle`
    (a JSON column) with no special encoding."""
    return {
        "bucket": bucket_key,
        "trigger_fact": trigger_fact,
        "transcript": [],       # [{q, a}, ...]
        "covered": [],          # list, not set — JSON-serializable
        "fallback_asked": [],
        "unprompted_conditions": [],
        "turns_used": 0,
        "verify_attempted": False,  # VerifyThreadComplete runs at most once per thread
        "done": False,
        "ended_reason": None,   # 'complete' | 'turn_cap' once done
        "next_question": None,  # the question awaiting an answer, or None once done
        "summary": None,        # filled once done
    }


def step_thread(
    state: dict,
    answer: Optional[str],
    *,
    next_question_fn: Optional[Callable] = None,
    verify_fn: Optional[Callable] = None,
    summarize_fn: Optional[Callable] = None,
) -> dict:
    """ONE TURN of one condition thread — pure function, plain-dict state in, updated
    plain-dict state out. This is what a real HTTP endpoint calls once per request
    (step_routes.py): `thread/start` calls it with `answer=None` to get the FIRST
    question; `thread/answer` calls it with the applicant's answer to get the next one
    (or the close-out summary once `state["done"]` is True).

    `run_condition_thread` below is a thin blocking loop over this same function — one
    source of truth for the turn logic, so the offline test suite and the real
    turn-by-turn API can never drift apart.

    NOTE: defaults resolve at CALL time (`next_question_fn or _real_next_question`),
    not as mutable default arguments — see `run_triage`'s docstring for why a
    `= _real_next_question` default would break `monkeypatch.setattr` in tests."""
    next_question_fn = next_question_fn or _real_next_question
    verify_fn = verify_fn or _real_verify
    summarize_fn = summarize_fn or _real_summarize
    if state["done"]:
        return state  # calling step_thread again on a finished thread is a no-op

    bucket = CONDITION_BUCKETS[state["bucket"]]
    targets = bucket["info_targets"]
    covered = set(state["covered"])

    # Record the applicant's answer to the PREVIOUS question, if this isn't the first turn.
    if answer is not None and state["next_question"] is not None:
        state["transcript"].append({"q": state["next_question"], "a": answer})
        state["turns_used"] += 1

    if state["turns_used"] >= MAX_TURNS_PER_CONDITION:
        return _close_out_thread(state, targets, covered, "turn_cap", summarize_fn)

    uncovered = [t for t in targets if t not in covered]
    if not uncovered:
        return _verify_then_close_or_ask(state, bucket, targets, covered, verify_fn, summarize_fn)

    try:
        step = next_question_fn(bucket["label"], state["trigger_fact"], targets,
                                 state["transcript"], state["turns_used"], MAX_TURNS_PER_CONDITION)
        state["unprompted_conditions"].extend(step.unprompted_conditions)  # never dropped
        covered.update(t for t in step.covered_targets if t in targets)  # ignore invented names
        state["covered"] = sorted(covered)
        if step.is_terminal:
            return _close_out_thread(state, targets, covered, "turn_cap", summarize_fn)
        if step.is_complete:
            if not [t for t in targets if t not in covered]:
                return _verify_then_close_or_ask(state, bucket, targets, covered, verify_fn, summarize_fn)
            return _close_out_thread(state, targets, covered, "turn_cap", summarize_fn)
        state["next_question"] = step.question
    except Exception:
        # LLM failure -> ask about the next still-uncovered target NOT already asked via
        # this fallback path (cycling back to the first uncovered target once every
        # uncovered target has had a fallback question). Never marks anything covered
        # (we can't confirm that without the extraction call) — advancing is tracked
        # separately via `fallback_asked` so a sustained outage still asks a DIFFERENT
        # question each turn instead of repeating turn after turn.
        not_yet = [t for t in uncovered if t not in state["fallback_asked"]]
        target = not_yet[0] if not_yet else uncovered[0]
        state["fallback_asked"].append(target)
        state["next_question"] = f"Could you tell me more about: {target}?"
    return state


def _verify_then_close_or_ask(state: dict, bucket: dict, targets: list[str], covered: set[str],
                               verify_fn: Callable, summarize_fn: Callable) -> dict:
    """Every target looks covered — before closing, run ONE independent check for
    contradictions (e.g. a resolution date before the onset date) or a target the prior
    turn marked covered but the transcript never actually addressed. Runs at most once
    per thread (`verify_attempted`), so a second pass never re-litigates the same
    thread or turns this into an unbounded back-and-forth. Fails open on any verifier
    error — a broken guardrail call must never block a thread from ever closing."""
    if state["verify_attempted"]:
        return _close_out_thread(state, targets, covered, "complete", summarize_fn)
    state["verify_attempted"] = True
    try:
        result = verify_fn(bucket["label"], targets, sorted(covered), state["transcript"])
        if not result.is_consistent and result.follow_up_question:
            state["next_question"] = result.follow_up_question
            return state
    except Exception:
        pass  # fail open — a broken verifier must never block closing
    return _close_out_thread(state, targets, covered, "complete", summarize_fn)


def _close_out_thread(state: dict, targets: list[str], covered: set[str], reason: str,
                       summarize_fn: Callable) -> dict:
    uncovered_final = [t for t in targets if t not in covered]
    if uncovered_final and reason == "complete":
        reason = "turn_cap"  # exited via is_complete but targets genuinely remain open
    try:
        summary_out = summarize_fn(CONDITION_BUCKETS[state["bucket"]]["label"],
                                    state["transcript"], reason, uncovered_final)
        summary = {
            "onset": summary_out.onset,
            "current_status": summary_out.current_status,
            "treatment": summary_out.treatment,
            "severity_notes": summary_out.severity_notes,
            "free_text_summary": summary_out.free_text_summary,
        }
    except Exception:
        summary = {"onset": None, "current_status": None, "treatment": None,
                   "severity_notes": None,
                   "free_text_summary": "(summary unavailable — see raw transcript)"}
    state["covered"] = sorted(covered)
    state["done"] = True
    state["ended_reason"] = reason
    state["next_question"] = None
    state["summary"] = summary
    return state


def run_condition_thread(
    bucket_key: str,
    trigger_fact: str,
    answer_callback: Callable[[str], str],
    *,
    next_question_fn: Optional[Callable] = None,
    verify_fn: Optional[Callable] = None,
    summarize_fn: Optional[Callable] = None,
) -> dict:
    """Drives one condition's conversation to completion by looping `step_thread`.
    `answer_callback` is injected so this is testable with canned answers (mirrors
    judge.py's testing pattern) and stands in for the real per-turn HTTP round-trip —
    the journey's ACTUAL API (step_routes.py) calls `step_thread` directly, once per
    request, since a server can't block one function call across HTTP requests the way
    this synchronous loop can in a test."""
    state = new_thread_state(bucket_key, trigger_fact)
    state = step_thread(state, None, next_question_fn=next_question_fn, verify_fn=verify_fn,
                         summarize_fn=summarize_fn)
    while not state["done"]:
        answer = answer_callback(state["next_question"])
        state = step_thread(state, answer, next_question_fn=next_question_fn, verify_fn=verify_fn,
                             summarize_fn=summarize_fn)
    return {
        "bucket": state["bucket"],
        "trigger_fact": state["trigger_fact"],
        "summary": state["summary"],
        "transcript": state["transcript"],
        "turns_used": state["turns_used"],
        "ended_reason": state["ended_reason"],
        "unprompted_conditions": state["unprompted_conditions"],
    }


# ---------------------------------------------------------------------------
# Orchestrator — one thread per flagged bucket, then ONE bounded second pass
# (HEALTH_AGENT_PLAN.md §4.2)
# ---------------------------------------------------------------------------
def run_all_threads(
    flagged: list[dict],
    answer_callback: Callable[[str], str],
    *,
    triage_fn: Optional[Callable] = None,
    next_question_fn: Optional[Callable] = None,
    verify_fn: Optional[Callable] = None,
    summarize_fn: Optional[Callable] = None,
) -> list[dict]:
    """Runs one thread per triage-flagged bucket, then a SINGLE bounded second pass for
    anything applicants volunteered along the way — never a third pass, never
    unbounded. Not a generic "anything else?" free-text question (that would reopen the
    unbounded-chat problem this whole design avoids) — volunteered text is re-triaged
    through the SAME evidence-based reasoning as the original call."""
    results = [
        run_condition_thread(f["bucket"], f["trigger_fact"], answer_callback,
                              next_question_fn=next_question_fn, verify_fn=verify_fn,
                              summarize_fn=summarize_fn)
        for f in flagged
    ]
    already_run = {r["bucket"] for r in results}
    volunteered_text = [c for r in results for c in r["unprompted_conditions"]]
    if volunteered_text:
        second_pass = run_triage(
            volunteered_text=volunteered_text,
            triage_fn=triage_fn,
        )
        new_buckets = [f for f in second_pass if f["bucket"] not in already_run][:MAX_SECOND_PASS_BUCKETS]
        results += [
            run_condition_thread(f["bucket"], f["trigger_fact"], answer_callback,
                                  next_question_fn=next_question_fn, verify_fn=verify_fn,
                                  summarize_fn=summarize_fn)
            for f in new_buckets
        ]
        # A second-pass thread that itself surfaces a THIRD volunteered condition does
        # NOT trigger a third pass — those stay captured in that thread's own
        # unprompted_conditions (still present in `results`, never lost) but are not
        # re-triaged. Bounds total conversation length even in the
        # applicant-mentions-everything case.
    return results
