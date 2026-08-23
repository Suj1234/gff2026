"""test_health_agent_routes.py — HTTP-level integration test for the 3 health-agent
endpoints (HEALTH_AGENT_PLAN.md §6), fully offline (monkeypatches the DSPy call
functions so no network/LLM key is needed, AND uses MOBILE_PAN_MOCK_MODE — Phase K —
so /verify-otp never touches the real vendor gateway either; same offline discipline as
journey/test_step4_vitals.py, now with no dependency on any specific "safe" test number).

Env is set BEFORE importing journey/underwriting so db.py binds a throwaway DB.
"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name.replace(os.sep, '/')}"
os.environ["UW_DEBUG_OTP"] = "1"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ["MOBILE_PAN_MOCK_MODE"] = "1"  # Phase K: no real vendor call, any number works
os.environ.pop("NURALX_BASE_URL", None)  # force the mock face-scan path

from starlette.testclient import TestClient  # noqa: E402

from journey.db import init_db  # noqa: E402
from journey.health_agent import engine  # noqa: E402
from underwriting.api import app  # noqa: E402

init_db()

_TEST_MOBILE = "9554259281"  # arbitrary now that mock mode is on — any number would do


def _login(c: TestClient, mobile: str = _TEST_MOBILE) -> int:
    r = c.post("/api/auth/send-otp", json={"mobile": mobile, "insurer_slug": "acme"}).json()
    assert r["success"], r
    r2 = c.post("/api/auth/verify-otp", json={
        "mobile": mobile, "otp": r["debug_otp"], "otp_ref_id": r["otp_ref_id"],
        "insurer_slug": "acme", "initial_sum_insured": 2500000,
        "initial_plan_type": "individual_health"}).json()
    assert r2["success"], r2
    return int(r2["application_id"])


def test_health_agent_endpoints_end_to_end(monkeypatch):
    """One login, one application, exercising ALL the endpoint behaviors in sequence —
    deliberately consolidated into a single test function so this file makes exactly
    ONE real vendor round-trip (via _login), not one per sub-check."""
    c = TestClient(app)

    # ------------------------------------------------------------------
    # 1. Triage with no evidence -> flags nothing, degrades gracefully.
    # ------------------------------------------------------------------
    monkeypatch.setattr(engine, "_real_triage", lambda *a, **kw: [])
    app_id = _login(c)  # the ONE live vendor round-trip in this whole file

    r = c.post(f"/api/journey/health/triage/{app_id}")
    body = r.json()
    assert body["success"] is True
    assert body["flagged"] == []

    # ------------------------------------------------------------------
    # 2. No LLM configured -> RuntimeError from the engine -> still 200, empty flagged.
    # ------------------------------------------------------------------
    def raise_no_lm(*a, **kw):
        raise RuntimeError("No LLM configured. Set LLM_MODEL and your provider key in .env")
    monkeypatch.setattr(engine, "_real_triage", raise_no_lm)
    r2 = c.post(f"/api/journey/health/triage/{app_id}")
    assert r2.status_code == 200
    assert r2.json()["success"] is True and r2.json()["flagged"] == []

    # ------------------------------------------------------------------
    # 2b. A live-LLM-call failure that ISN'T "no LLM configured" (bad JSON, gateway
    # timeout, DSPy parse error — anything `list(out.flagged)` in _real_triage can raise)
    # must still degrade to a 200 with success=False, never surface as a 500 (the bug
    # found 2026-08-21: it wasn't caught at all, so the browser saw a raw 500 and the
    # UI's "Try again" flow had no JSON body to read a message from).
    # ------------------------------------------------------------------
    def raise_other(*a, **kw):
        raise ValueError("malformed LLM output")
    monkeypatch.setattr(engine, "_real_triage", raise_other)
    r2b = c.post(f"/api/journey/health/triage/{app_id}")
    assert r2b.status_code == 200
    assert r2b.json()["success"] is False

    # ------------------------------------------------------------------
    # 3. Triage reads real ABHA facts from the bundle and flags a bucket.
    # ------------------------------------------------------------------
    def fake_triage(catalog, face, abha, presc, vol):
        if any(cd.startswith("I25") for cd in abha.get("icd_codes", [])):
            return [{"bucket": "cardiac", "trigger_fact": "abha icd I25.10", "confidence": "high"}]
        return []
    monkeypatch.setattr(engine, "_real_triage", fake_triage)

    from journey.step_routes import _mutate_bundle
    from journey.db import get_session
    from journey.models import Application
    db = next(get_session())
    a = db.get(Application, app_id)
    def add(bundle):
        bundle.setdefault("signals", {})["abha_health_records"] = {
            "status": "available", "icd_codes": ["I25.10"], "diagnoses": [], "prescriptions": [],
            "unstructured_notes": [],
        }
    _mutate_bundle(a, add)
    db.add(a)
    db.commit()

    r3 = c.post(f"/api/journey/health/triage/{app_id}")
    body3 = r3.json()
    assert body3["success"] is True
    assert [f["bucket"] for f in body3["flagged"]] == ["cardiac"]
    assert body3["flagged"][0]["label"] == "Cardiac / heart condition"

    # ------------------------------------------------------------------
    # 4. thread/start on an unknown bucket fails safe.
    # ------------------------------------------------------------------
    bad = c.post(f"/api/journey/health/thread/start/{app_id}",
                 json={"app_id": app_id, "bucket": "not_a_real_bucket"})
    assert bad.json()["success"] is False

    # ------------------------------------------------------------------
    # 5. thread/answer on an unknown thread_id fails safe.
    # ------------------------------------------------------------------
    bad2 = c.post(f"/api/journey/health/thread/answer/{app_id}",
                  json={"app_id": app_id, "thread_id": "nonexistent", "answer": "hi"})
    assert bad2.json()["success"] is False

    # ------------------------------------------------------------------
    # 6. Full thread flow to completion: start -> answer -> done -> facts land.
    # ------------------------------------------------------------------
    monkeypatch.setattr(engine, "_real_triage",
                         lambda *a, **kw: [{"bucket": "hypertension", "trigger_fact": "test", "confidence": "high"}])

    call_n = {"n": 0}
    def fake_next_question(label, trigger, targets, transcript, turns, max_turns):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                    is_complete=False, is_terminal=False, question="When did this start?")
        return SimpleNamespace(covered_targets=list(targets), unprompted_conditions=[],
                                is_complete=True, is_terminal=False, question=None)
    monkeypatch.setattr(engine, "_real_next_question", fake_next_question)

    def fake_verify(label, info_targets, covered_targets, transcript):
        return SimpleNamespace(is_consistent=True, problem=None, follow_up_question=None)
    monkeypatch.setattr(engine, "_real_verify", fake_verify)

    def fake_summarize(label, transcript, ended_reason, uncovered):
        return SimpleNamespace(onset="2020", current_status="controlled", treatment="amlodipine",
                                severity_notes=None, free_text_summary="Controlled hypertension since 2020.")
    monkeypatch.setattr(engine, "_real_summarize", fake_summarize)

    triage4 = c.post(f"/api/journey/health/triage/{app_id}").json()
    assert [f["bucket"] for f in triage4["flagged"]] == ["hypertension"]

    start = c.post(f"/api/journey/health/thread/start/{app_id}",
                    json={"app_id": app_id, "bucket": "hypertension"}).json()
    assert start["success"] is True
    assert start["question"] == "When did this start?"
    assert start["thread_id"] == "hypertension"

    answer1 = c.post(f"/api/journey/health/thread/answer/{app_id}",
                      json={"app_id": app_id, "thread_id": "hypertension", "answer": "around 2020"}).json()
    assert answer1["done"] is True
    assert answer1["summary"]["current_status"] == "controlled"
    assert answer1["next_thread"] is None

    snap = c.get(f"/api/journey/app/{app_id}").json()
    cd = snap["health_declaration"]["condition_detail"]
    assert len(cd) == 1
    assert cd[0]["condition"] == "hypertension"
    assert cd[0]["current_status"] == "controlled"
    assert cd[0]["source"] == "health_agent"
    assert snap["health_agent"]["threads"]["hypertension"]["done"] is True

    # Re-answering an already-done thread is a safe no-op, not an error.
    again = c.post(f"/api/journey/health/thread/answer/{app_id}",
                    json={"app_id": app_id, "thread_id": "hypertension", "answer": "ok"}).json()
    assert again["done"] is True


def test_thread_answer_persists_across_multiple_turns(monkeypatch):
    """Regression lock for the 2026-08-21 bug: `step_thread` receives `state` as a
    reference NESTED inside `app.bundle` (the SQLAlchemy MutableDict-tracked column) and
    mutates it IN PLACE (`turns_used += 1`, `transcript.append(...)`). MutableDict only
    instruments the top-level dict's own `__setitem__` — it never sees a mutation on a
    list/dict nested inside it — so that in-place mutation silently corrupted
    `app.bundle`'s live value BEFORE `_mutate_bundle`'s own deep-copy ran; by the time
    SQLAlchemy compared "old" vs "new" to decide whether to emit an UPDATE, they were
    already equal, so NOTHING was ever written to the DB. The bug was invisible on the
    FIRST turn of `test_health_agent_endpoints_end_to_end` above because that test's
    thread closes in exactly one turn — it never exercises a SECOND `/thread/answer`
    call needing to see the FIRST call's persisted state. This test forces >=2 open
    turns specifically to catch that: if the fix (`copy.deepcopy(state)` before
    `step_thread` in step_routes.py) regresses, turn 2 sees turns_used=0 and an empty
    transcript instead of turns_used=1 and the turn-1 Q/A pair — a real HTTP round trip
    each time, no direct engine-function calls that could paper over the persistence gap."""
    from journey.health_agent import engine

    c = TestClient(app)
    monkeypatch.setattr(engine, "_real_triage",
                         lambda *a, **kw: [{"bucket": "diabetes", "trigger_fact": "test", "confidence": "high"}])
    app_id = _login(c)

    call_n = {"n": 0}
    def fake_next_question(label, trigger, targets, transcript, turns, max_turns):
        call_n["n"] += 1
        # Turn 1 and 2 stay open; the assertion is entirely on what STATE each call
        # receives (turns/transcript), not on how the thread eventually closes.
        return SimpleNamespace(covered_targets=[], unprompted_conditions=[],
                                is_complete=False, is_terminal=False,
                                question=f"question #{call_n['n']}")
    monkeypatch.setattr(engine, "_real_next_question", fake_next_question)

    c.post(f"/api/journey/health/triage/{app_id}")
    start = c.post(f"/api/journey/health/thread/start/{app_id}",
                    json={"app_id": app_id, "bucket": "diabetes"}).json()
    assert start["question"] == "question #1"

    # Turn 1: answer question #1 -> the NEXT HTTP call must see turns_used=1 and the
    # turn-1 Q/A in transcript, proving the write from THIS call actually persisted.
    answer1 = c.post(f"/api/journey/health/thread/answer/{app_id}",
                      json={"app_id": app_id, "thread_id": "diabetes", "answer": "turn 1 answer"}).json()
    assert answer1["done"] is False
    assert answer1["question"] == "question #2"

    snap_after_turn1 = c.get(f"/api/journey/app/{app_id}").json()
    thread_state = snap_after_turn1["health_agent"]["threads"]["diabetes"]
    assert thread_state["turns_used"] == 1, (
        f"turn 1 never persisted (got turns_used={thread_state['turns_used']}) — "
        "the MutableDict nested-mutation bug has regressed"
    )
    assert thread_state["transcript"] == [{"q": "question #1", "a": "turn 1 answer"}]

    # Turn 2: answer question #2 -> must build on turn 1's persisted state, not restart.
    answer2 = c.post(f"/api/journey/health/thread/answer/{app_id}",
                      json={"app_id": app_id, "thread_id": "diabetes", "answer": "turn 2 answer"}).json()
    assert answer2["question"] == "question #3"

    snap_after_turn2 = c.get(f"/api/journey/app/{app_id}").json()
    thread_state2 = snap_after_turn2["health_agent"]["threads"]["diabetes"]
    assert thread_state2["turns_used"] == 2
    assert thread_state2["transcript"] == [
        {"q": "question #1", "a": "turn 1 answer"},
        {"q": "question #2", "a": "turn 2 answer"},
    ]
