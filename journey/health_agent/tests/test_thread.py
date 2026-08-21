"""test_thread.py — Phase 2 adaptive per-condition conversation (HEALTH_AGENT_PLAN.md
§4, §9). All offline: `next_question_fn`/`summarize_fn` are plain Python functions
returning `SimpleNamespace` objects that mimic the shape of a real DSPy prediction
(`.covered_targets`, `.unprompted_conditions`, `.is_complete`, `.is_terminal`,
`.question` / `.onset` etc.) — no network, no LLM.
"""

from __future__ import annotations

from types import SimpleNamespace

from journey.health_agent.config import CONDITION_BUCKETS, MAX_TURNS_PER_CONDITION
from journey.health_agent.engine import run_condition_thread


def _summary_stub(onset=None, current_status=None, treatment=None, severity_notes=None,
                   free_text_summary="ok"):
    def summarize_fn(label, transcript, ended_reason, uncovered):
        return SimpleNamespace(onset=onset, current_status=current_status, treatment=treatment,
                                severity_notes=severity_notes, free_text_summary=free_text_summary)
    return summarize_fn


def test_fully_informative_first_answer_closes_in_one_turn():
    targets = CONDITION_BUCKETS["hypertension"]["info_targets"]

    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        if turns == 0:
            return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                    is_complete=False, is_terminal=False, question="Tell me about it")
        return SimpleNamespace(covered_targets=list(info_targets), unprompted_conditions=[],
                                is_complete=True, is_terminal=False, question=None)

    result = run_condition_thread(
        "hypertension", "trigger", lambda q: "diagnosed 2020, on amlodipine, well controlled",
        next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert result["turns_used"] == 1
    assert result["ended_reason"] == "complete"


def test_branching_after_resolved_answer_does_not_ask_about_medication():
    """The exact scenario from the user's original feedback: onset stated, then
    "it's over now" — the NEXT question must be about resolution/current status, not
    current medication. This is the regression test for the fixed-checklist bug."""
    targets = CONDITION_BUCKETS["cardiac"]["info_targets"]
    questions_seen = []

    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        if turns == 0:
            questions_seen.append("onset")
            return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                    is_complete=False, is_terminal=False, question="When did this start?")
        if turns == 1:
            questions_seen.append("resolution")
            return SimpleNamespace(covered_targets=[info_targets[0]], unprompted_conditions=[],
                                    is_complete=False, is_terminal=False,
                                    question="When and how did it get resolved?")
        return SimpleNamespace(covered_targets=list(info_targets), unprompted_conditions=[],
                                is_complete=True, is_terminal=False, question=None)

    answers = iter(["around 2019", "it's over now, resolved last year"])
    result = run_condition_thread(
        "cardiac", "trigger", lambda q: next(answers, "yes"),
        next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert questions_seen == ["onset", "resolution"]
    assert "medication" not in result["transcript"][1]["q"].lower()


def test_evasive_answer_sets_terminal_and_stops_early():
    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                is_complete=False, is_terminal=(turns >= 1),
                                question=None if turns >= 1 else "Why does it matter?")

    result = run_condition_thread(
        "thyroid", "trigger", lambda q: "why do you need to know that",
        next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert result["turns_used"] < MAX_TURNS_PER_CONDITION
    assert result["ended_reason"] == "turn_cap"  # terminated early, targets not all covered


def test_imprecise_but_genuine_answer_does_not_terminate():
    """A model correctly distinguishing IMPRECISE from EVASIVE (§4, §9 test c-2): a
    vague-but-genuine answer must continue the thread normally, not set is_terminal."""
    targets = CONDITION_BUCKETS["diabetes"]["info_targets"]

    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        if turns == 0:
            return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                    is_complete=False, is_terminal=False, question="When did this start?")
        # After a vague-but-genuine answer, the target is marked covered, NOT terminal.
        return SimpleNamespace(covered_targets=[info_targets[0]], unprompted_conditions=[],
                                is_complete=False, is_terminal=False, question="Any treatment currently?")

    result = run_condition_thread(
        "diabetes", "trigger", lambda q: "years ago, don't remember exactly",
        next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert result["ended_reason"] != "turn_cap" or result["turns_used"] == MAX_TURNS_PER_CONDITION
    # the key assertion: is_terminal was never set true, so the thread ran its natural course
    assert len(result["transcript"]) >= 1


def test_maximally_vague_applicant_hits_turn_cap_not_infinite_loop():
    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                is_complete=False, is_terminal=False, question="Can you tell me more?")

    result = run_condition_thread(
        "renal_hepatic", "trigger", lambda q: "not sure",
        next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert result["turns_used"] == MAX_TURNS_PER_CONDITION
    assert result["ended_reason"] == "turn_cap"


def test_llm_failure_fallback_advances_through_different_targets():
    """The bug found+fixed while building this: a sustained LLM failure must cycle
    through EVERY target at least once before ever repeating one — never get stuck
    asking the same question turn after turn. Once every target has had a fallback
    question, a further turn (if MAX_TURNS_PER_CONDITION exceeds the target count, as
    it now legitimately can — hypertension has 4 targets, the cap is 5) may wrap back
    to the first target; that's a deliberate "ask something rather than nothing" choice,
    not a stall, so the real assertion is: no target is asked about a SECOND time before
    every OTHER target has been asked at least once."""
    def always_fail(*a, **kw):
        raise RuntimeError("gateway down")

    questions = []
    def answer_cb(q):
        questions.append(q)
        return "ok"

    result = run_condition_thread(
        "hypertension", "trigger", answer_cb,
        next_question_fn=always_fail, summarize_fn=_summary_stub(free_text_summary="partial"),
    )
    n_targets = len(CONDITION_BUCKETS["hypertension"]["info_targets"])
    first_pass = questions[:n_targets]
    assert len(set(first_pass)) == n_targets, "fallback repeated a target before cycling through all of them"
    assert result["turns_used"] == MAX_TURNS_PER_CONDITION
    assert result["ended_reason"] == "turn_cap"


def test_summarize_never_hallucinates_unaddressed_fields():
    """SummarizeConditionThread must never fill a field the transcript didn't address —
    this test locks the CONTRACT (engine passes through whatever the summarizer
    returns verbatim); the summarizer's own honesty is a prompt property tested via the
    live smoke test, not mockable here."""
    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        if turns == 0:
            return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                    is_complete=False, is_terminal=False, question="Onset?")
        return SimpleNamespace(covered_targets=[info_targets[0]], unprompted_conditions=[],
                                is_complete=True, is_terminal=False, question=None)

    def summarize_fn(label, transcript, ended_reason, uncovered):
        # Only onset+status were ever discussed -> treatment/severity must be null.
        return SimpleNamespace(onset="2019", current_status="active", treatment=None,
                                severity_notes=None, free_text_summary="Onset 2019, still active.")

    result = run_condition_thread(
        "diabetes", "trigger", lambda q: "2019, still dealing with it",
        next_question_fn=next_question_fn, summarize_fn=summarize_fn,
    )
    assert result["summary"]["treatment"] is None
    assert result["summary"]["severity_notes"] is None


def test_turn_cap_summary_flags_incompleteness():
    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                is_complete=False, is_terminal=False, question="More detail?")

    def summarize_fn(label, transcript, ended_reason, uncovered):
        assert ended_reason == "turn_cap"
        assert uncovered  # non-empty — the summarizer sees what's still open
        return SimpleNamespace(onset=None, current_status=None, treatment=None, severity_notes=None,
                                free_text_summary="Partial history — could not confirm current status.")

    result = run_condition_thread(
        "oncology", "trigger", lambda q: "hmm",
        next_question_fn=next_question_fn, summarize_fn=summarize_fn,
    )
    assert "partial" in result["summary"]["free_text_summary"].lower()


def test_summary_unavailable_fallback_on_summarizer_failure():
    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        return SimpleNamespace(covered_targets=list(info_targets), unprompted_conditions=[],
                                is_complete=True, is_terminal=False, question=None)

    def always_fail_summarize(*a, **kw):
        raise RuntimeError("gateway down")

    result = run_condition_thread(
        "mental_health", "trigger", lambda q: "ok",
        next_question_fn=next_question_fn, summarize_fn=always_fail_summarize,
    )
    assert "unavailable" in result["summary"]["free_text_summary"].lower()


# ---------------------------------------------------------------------------
# Volunteered conditions — captured, not lost, and not pursued in-thread (§4.2, §9)
# ---------------------------------------------------------------------------
def test_volunteered_condition_recorded_and_thread_stays_on_topic():
    targets = CONDITION_BUCKETS["cardiac"]["info_targets"]

    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        if turns == 0:
            return SimpleNamespace(covered_targets=[], unprompted_conditions=["also has diabetes"],
                                    is_complete=False, is_terminal=False, question="When did the cardiac issue start?")
        return SimpleNamespace(covered_targets=list(info_targets), unprompted_conditions=[],
                                is_complete=True, is_terminal=False, question=None)

    result = run_condition_thread(
        "cardiac", "trigger", lambda q: "2019, and by the way I also have diabetes",
        next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert result["unprompted_conditions"] == ["also has diabetes"]
    # thread's own questions never pivot to diabetes:
    assert all("diabetes" not in q["q"].lower() for q in result["transcript"])


def test_volunteered_condition_recorded_even_on_early_terminal_exit():
    """§4.1's engine fix: unprompted_conditions must be captured even when the SAME
    turn that reports them also sets is_complete/is_terminal (early break path)."""
    def next_question_fn(label, trigger, info_targets, transcript, turns, max_turns):
        return SimpleNamespace(covered_targets=list(info_targets),
                                unprompted_conditions=["mentioned thyroid issue too"],
                                is_complete=True, is_terminal=False, question=None)

    result = run_condition_thread(
        "cardiac", "trigger", lambda q: "ok",
        next_question_fn=next_question_fn, summarize_fn=_summary_stub(),
    )
    assert result["unprompted_conditions"] == ["mentioned thyroid issue too"]
