"""test_bank_statement_race.py — regression lock for the 2026-08-21 clobber found live
on app GFF-99E1E8: `_analyze_bank_statement_bg` (background, ~40s real call) races
`/financial`'s autosave-on-blur on the SAME bundle. Unlocked, whichever request's
read->mutate->write commits LAST wins — a completed iAdore result (account_aggregator +
_journey.bank_statement_upload="done") got silently reverted by a `financial_declared`
write that read the bundle BEFORE the iAdore write landed. Both routes now wrap their
read->mutate->write in `_app_lock(app_id)` (step_routes.py, same pattern as the prior
health-agent thread-state fix). Reproduces the race with real threads + a controllable
delay so it's deterministic, not timing-flaky.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name.replace(os.sep, '/')}"
os.environ["UW_DEBUG_OTP"] = "1"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ["MOBILE_PAN_MOCK_MODE"] = "1"
os.environ.pop("NURALX_BASE_URL", None)

from starlette.testclient import TestClient  # noqa: E402

from journey.db import init_db, session_scope  # noqa: E402
from journey.models import Application  # noqa: E402
from journey import step_routes  # noqa: E402
from underwriting.api import app  # noqa: E402

init_db()

_RAW_REPORT = {"analysis": {"imputedAnnualIncome": 2_181_3750, "avgMonthlyBalance": 90_000}}


def _login(c: TestClient) -> int:
    r = c.post("/api/auth/send-otp", json={"mobile": "9000000123", "insurer_slug": "acme"}).json()
    r2 = c.post("/api/auth/verify-otp", json={
        "mobile": "9000000123", "otp": r["debug_otp"], "otp_ref_id": r["otp_ref_id"],
        "insurer_slug": "acme", "initial_sum_insured": 2500000,
        "initial_plan_type": "individual_health"}).json()
    assert r2["success"], r2
    return int(r2["application_id"])


def test_financial_autosave_cannot_clobber_a_concurrent_bank_statement_result(monkeypatch):
    c = TestClient(app)
    app_id = _login(c)

    # Slow, controllable fake iAdore call: blocks until the main thread lets it proceed,
    # so the financial-autosave write is guaranteed to land WHILE the bg task is between
    # its read and its write — the exact race window that clobbered GFF-99E1E8 live.
    release = threading.Event()

    def slow_analyze(path):
        release.wait(timeout=5)
        return _RAW_REPORT

    monkeypatch.setattr("bank_statement.analyze", slow_analyze)

    # Run the background analysis directly on a real thread (bypassing BackgroundTasks,
    # which only fires after the HTTP response in TestClient) so it genuinely overlaps
    # the /financial call below.
    bg_thread = threading.Thread(
        target=step_routes._analyze_bank_statement_bg, args=(app_id, __file__))
    bg_thread.start()
    time.sleep(0.05)  # let the bg thread get past its read, into the slow analyze() call

    fin = c.post("/api/journey/financial", json={
        "app_id": app_id, "declared_annual_income": 6_000_000}).json()
    assert fin["success"], fin

    release.set()
    bg_thread.join(timeout=5)
    assert not bg_thread.is_alive(), "background analysis never finished"

    with session_scope() as s:
        final = s.get(Application, app_id)
        aa = final.bundle.get("signals", {}).get("account_aggregator")
        marker = final.bundle.get("_journey", {}).get("bank_statement_upload")
        income = final.bundle.get("application", {}).get("financial", {}).get("declared_annual_income")

    # Both writes must survive: the concurrent /financial call must not erase the
    # completed iAdore result, and the bg task's completion must not erase the income edit.
    assert aa is not None and aa.get("status") == "available", aa
    assert marker == {"status": "done"}, marker
    assert income == 6_000_000, income


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
