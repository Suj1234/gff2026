"""test_bank_statement_lock.py — regression test for the 2026-08-21 data-loss bug found
live on app GFF-99E1E8: `_analyze_bank_statement_bg` (background analysis) and
`/financial` (autosave-on-blur) both read-modify-write the SAME application record with
no coordination between them. Whichever request finishes last wins and silently discards
the other's update — a completed bank-statement result got reverted back to "processing"
by an income-field autosave that happened to read the record just before the analysis
result was saved. Both routes now share the existing per-application lock (`_app_lock`
in step_routes.py) that was already used to fix this same class of bug in the
health-agent chat feature. This test reproduces the timing with two cooperating
worker threads and a synchronization event, so the outcome is deterministic.
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

_SAMPLE_VENDOR_REPORT = {"analysis": {"imputedAnnualIncome": 21_813_750, "avgMonthlyBalance": 90_000}}


def _login(c: TestClient) -> int:
    r = c.post("/api/auth/send-otp", json={"mobile": "9000000123", "insurer_slug": "acme"}).json()
    r2 = c.post("/api/auth/verify-otp", json={
        "mobile": "9000000123", "otp": r["debug_otp"], "otp_ref_id": r["otp_ref_id"],
        "insurer_slug": "acme", "initial_sum_insured": 2500000,
        "initial_plan_type": "individual_health"}).json()
    assert r2["success"], r2
    return int(r2["application_id"])


def test_financial_autosave_does_not_erase_a_concurrent_bank_statement_result(monkeypatch):
    c = TestClient(app)
    app_id = _login(c)

    # A controllable stand-in for the vendor call: it pauses until told to continue, so the
    # test can guarantee the /financial write below lands WHILE the analysis job is between
    # its own read and write of the application record.
    proceed = threading.Event()

    def paced_vendor_call(path):
        proceed.wait(timeout=5)
        return _SAMPLE_VENDOR_REPORT

    monkeypatch.setattr("bank_statement.analyze", paced_vendor_call)

    worker = threading.Thread(
        target=step_routes._analyze_bank_statement_bg, args=(app_id, __file__))
    worker.start()
    time.sleep(0.05)  # give the worker time to pass its own read and enter the paced call

    fin = c.post("/api/journey/financial", json={
        "app_id": app_id, "declared_annual_income": 6_000_000}).json()
    assert fin["success"], fin

    proceed.set()
    worker.join(timeout=5)
    assert not worker.is_alive(), "analysis worker never finished"

    with session_scope() as s:
        record = s.get(Application, app_id)
        aa = record.bundle.get("signals", {}).get("account_aggregator")
        marker = record.bundle.get("_journey", {}).get("bank_statement_upload")
        income = record.bundle.get("application", {}).get("financial", {}).get("declared_annual_income")

    # Neither update should be lost: the concurrent financial-income save must not erase
    # the completed analysis result, and the analysis completing must not erase the income edit.
    assert aa is not None and aa.get("status") == "available", aa
    assert marker == {"status": "done"}, marker
    assert income == 6_000_000, income
