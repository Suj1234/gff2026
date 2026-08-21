"""test_orchestrator.py — run_all_threads: the bounded second-pass catch-all
(HEALTH_AGENT_PLAN.md §4.2, §9). Offline, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

from journey.health_agent.config import CONDITION_BUCKETS, MAX_SECOND_PASS_BUCKETS
from journey.health_agent.engine import run_all_threads


def _immediate_complete(unprompted=None):
    """A next_question_fn stub that closes the thread in turn 0, reporting whatever
    `unprompted` list was given (mimics 'the applicant mentioned X mid-answer')."""
    def fn(label, trigger, info_targets, transcript, turns, max_turns):
        return SimpleNamespace(covered_targets=list(info_targets),
                                unprompted_conditions=unprompted or [],
                                is_complete=True, is_terminal=False, question=None)
    return fn


def _summary_stub():
    def fn(label, transcript, ended_reason, uncovered):
        return SimpleNamespace(onset=None, current_status=None, treatment=None,
                                severity_notes=None, free_text_summary="ok")
    return fn


def test_no_volunteered_conditions_runs_only_flagged_threads():
    flagged = [{"bucket": "cardiac", "trigger_fact": "x", "confidence": "high"}]
    results = run_all_threads(
        flagged, lambda q: "ok",
        next_question_fn=_immediate_complete(), summarize_fn=_summary_stub(),
    )
    assert [r["bucket"] for r in results] == ["cardiac"]


def test_single_volunteered_condition_gets_its_own_thread():
    flagged = [{"bucket": "cardiac", "trigger_fact": "x", "confidence": "high"}]

    def triage_fn(catalog, face, abha, presc, vol):
        if vol and "diabetes" in " ".join(vol).lower():
            return [{"bucket": "diabetes", "trigger_fact": "volunteered", "confidence": "medium"}]
        return []

    results = run_all_threads(
        flagged, lambda q: "ok",
        triage_fn=triage_fn,
        next_question_fn=_immediate_complete(unprompted=["also has diabetes"]),
        summarize_fn=_summary_stub(),
    )
    assert [r["bucket"] for r in results] == ["cardiac", "diabetes"]


def test_second_pass_never_returns_an_already_run_bucket():
    """If the second-pass triage re-flags a bucket already covered by the first pass
    (e.g. the applicant re-mentions their cardiac issue), it must not run a duplicate
    thread."""
    flagged = [{"bucket": "cardiac", "trigger_fact": "x", "confidence": "high"}]

    def triage_fn(catalog, face, abha, presc, vol):
        return [{"bucket": "cardiac", "trigger_fact": "dup", "confidence": "high"},
                {"bucket": "diabetes", "trigger_fact": "new", "confidence": "high"}]

    results = run_all_threads(
        flagged, lambda q: "ok",
        triage_fn=triage_fn,
        next_question_fn=_immediate_complete(unprompted=["something"]),
        summarize_fn=_summary_stub(),
    )
    buckets = [r["bucket"] for r in results]
    assert buckets.count("cardiac") == 1
    assert "diabetes" in buckets


def test_second_pass_capped_at_max_new_buckets():
    """3+ volunteered conditions across multiple flagged threads -> at most
    MAX_SECOND_PASS_BUCKETS new threads run, never more."""
    flagged = [{"bucket": "cardiac", "trigger_fact": "x", "confidence": "high"},
               {"bucket": "hypertension", "trigger_fact": "y", "confidence": "high"}]

    def triage_fn(catalog, face, abha, presc, vol):
        # Simulate 3 distinct volunteered conditions all triaging to real buckets.
        return [{"bucket": "diabetes", "trigger_fact": "v1", "confidence": "high"},
                {"bucket": "thyroid", "trigger_fact": "v2", "confidence": "high"},
                {"bucket": "respiratory", "trigger_fact": "v3", "confidence": "high"}]

    results = run_all_threads(
        flagged, lambda q: "ok",
        triage_fn=triage_fn,
        next_question_fn=_immediate_complete(unprompted=["thing one", "thing two"]),
        summarize_fn=_summary_stub(),
    )
    new_buckets = [r["bucket"] for r in results if r["bucket"] not in ("cardiac", "hypertension")]
    assert len(new_buckets) <= MAX_SECOND_PASS_BUCKETS


def test_no_third_pass_even_if_second_pass_thread_volunteers_something():
    """A second-pass thread that itself surfaces a THIRD volunteered condition does
    NOT trigger a third triage pass — bounds total conversation length. The volunteered
    text is still captured in that thread's own unprompted_conditions (never lost),
    just not re-triaged."""
    flagged = [{"bucket": "cardiac", "trigger_fact": "x", "confidence": "high"}]
    triage_call_count = {"n": 0}

    def triage_fn(catalog, face, abha, presc, vol):
        triage_call_count["n"] += 1
        return [{"bucket": "diabetes", "trigger_fact": "from first volunteer", "confidence": "high"}]

    call_index = {"n": 0}
    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        call_index["n"] += 1
        # First thread (cardiac) volunteers "diabetes"; the second-pass thread
        # (diabetes) volunteers a THIRD condition ("oncology") — must not cause a
        # third triage pass.
        unprompted = ["also has diabetes"] if call_index["n"] == 1 else ["and possibly something else"]
        return SimpleNamespace(covered_targets=list(info_targets), unprompted_conditions=unprompted,
                                is_complete=True, is_terminal=False, question=None)

    results = run_all_threads(
        flagged, lambda q: "ok",
        triage_fn=triage_fn, next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert triage_call_count["n"] == 1  # exactly one second pass, never a third
    assert [r["bucket"] for r in results] == ["cardiac", "diabetes"]
    # the third volunteered condition is still visible, just not its own thread:
    diabetes_result = next(r for r in results if r["bucket"] == "diabetes")
    assert diabetes_result["unprompted_conditions"] == ["and possibly something else"]


def test_total_thread_count_never_exceeds_flagged_plus_cap():
    flagged = [{"bucket": b, "trigger_fact": "x", "confidence": "high"}
               for b in list(CONDITION_BUCKETS)[:2]]

    def triage_fn(catalog, face, abha, presc, vol):
        return [{"bucket": b, "trigger_fact": "v", "confidence": "high"} for b in CONDITION_BUCKETS]

    results = run_all_threads(
        flagged, lambda q: "ok",
        triage_fn=triage_fn,
        next_question_fn=_immediate_complete(unprompted=["x"]),
        summarize_fn=_summary_stub(),
    )
    assert len(results) <= len(flagged) + MAX_SECOND_PASS_BUCKETS
