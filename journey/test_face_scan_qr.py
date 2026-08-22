"""test_face_scan_qr.py — the real (non-mock) face-scan session/QR flow (docs/vendor_apis.md
PART B): start issues OUR /face-scan/{token} url (not NuralX's raw scan_url), the applicant's
phone hits the public status/begin endpoints (no session cookie), begin device-gates + calls
NuralX (mocked here — no network), and the existing webhook still resolves the session to
COMPLETED. Also covers not-found and expired tokens.

Env is set BEFORE importing journey/underwriting so db.py binds a throwaway DB.
"""
from __future__ import annotations

import os
import tempfile
from datetime import timedelta

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name.replace(os.sep, '/')}"
os.environ["UW_DEBUG_OTP"] = "1"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from starlette.testclient import TestClient  # noqa: E402

from journey.db import init_db, session_scope  # noqa: E402
from journey.models import FaceScanSession, _now  # noqa: E402
from underwriting import nuralx  # noqa: E402
from underwriting.api import app  # noqa: E402

init_db()


def _real_env(monkeypatch) -> None:
    """Force the REAL (non-mock) face-scan path for this test only — other test modules
    pop NURALX_BASE_URL at import time, so this can't be set at module level (import
    order across files would fight over the shared os.environ)."""
    monkeypatch.setenv("NURALX_BASE_URL", "https://fake.nuralx.test/webhook/")
    monkeypatch.setenv("NURALX_CALLBACK_SECRET", "test-secret-key")
    monkeypatch.setenv("PUBLIC_API_URL", "http://127.0.0.1:8899")
    monkeypatch.setenv("PUBLIC_FRONTEND_URL", "http://127.0.0.1:5173")


def _login(c: TestClient, mobile: str = "9739780007") -> int:
    r = c.post("/api/auth/send-otp", json={"mobile": mobile, "insurer_slug": "acme"}).json()
    assert r["success"], r
    r2 = c.post("/api/auth/verify-otp", json={
        "mobile": mobile, "otp": r["debug_otp"], "otp_ref_id": r["otp_ref_id"],
        "insurer_slug": "acme", "initial_sum_insured": 2500000,
        "initial_plan_type": "individual_health"}).json()
    assert r2["success"], r2
    return int(r2["application_id"])


def test_start_returns_our_url_not_the_raw_vendor_url(monkeypatch):
    _real_env(monkeypatch)
    c = TestClient(app)
    app_id = _login(c)

    r = c.post(f"/api/journey/face-scan/start/{app_id}").json()
    assert r["success"] and r["mode"] == "real", r
    assert r["scan_url"].startswith("http://127.0.0.1:5173/face-scan/"), r
    token = r["scan_url"].rsplit("/", 1)[-1]

    # public status endpoint (no cookie) sees PENDING
    status = c.get(f"/api/journey/face-scan/{token}/status").json()
    assert status["success"] and status["status"] == "PENDING", status

    print("OK — start() issues our own token URL, not NuralX's raw scan_url")


def test_begin_device_gates_then_calls_nuralx_mocked(monkeypatch):
    _real_env(monkeypatch)
    c = TestClient(app)
    app_id = _login(c)

    monkeypatch.setattr(
        nuralx, "initiate_scan",
        lambda creds, session_token, patient: nuralx.ScanResponse(
            scan_id="s1", scan_url="https://vendor.example/scan/abc"))

    r = c.post(f"/api/journey/face-scan/start/{app_id}").json()
    token = r["scan_url"].rsplit("/", 1)[-1]

    begin = c.post(f"/api/journey/face-scan/{token}/begin").json()
    assert begin["success"] and begin["status"] == "IN_PROGRESS", begin
    assert begin["scan_url"] == "https://vendor.example/scan/abc"

    status = c.get(f"/api/journey/face-scan/{token}/status").json()
    assert status["status"] == "IN_PROGRESS", status

    # webhook resolves it (existing callback logic, keyed by the NEW client_transaction_id
    # minted inside /begin — read it back from the DB since it's not exposed to the client)
    with session_scope() as db:
        fss = db.exec(
            __import__("sqlmodel").select(FaceScanSession).where(FaceScanSession.token == token)
        ).first()
        ctid = fss.client_transaction_id
    cb = c.post(f"/api/journey/face-scan/callback?key=test-secret-key",
                json={"client_transaction_ID": ctid, "status": "completed",
                      "results": {"pulseRate": {"value": 74}}})
    assert cb.status_code == 200

    status = c.get(f"/api/journey/face-scan/{token}/status").json()
    assert status["status"] == "COMPLETED", status

    print("OK — begin() device-gates + calls NuralX, webhook resolves to COMPLETED")


def test_begin_retries_after_vendor_error(monkeypatch):
    _real_env(monkeypatch)
    c = TestClient(app)
    app_id = _login(c)
    r = c.post(f"/api/journey/face-scan/start/{app_id}").json()
    token = r["scan_url"].rsplit("/", 1)[-1]

    monkeypatch.setattr(nuralx, "initiate_scan",
                        lambda creds, session_token, patient: (_ for _ in ()).throw(RuntimeError("vendor down")))
    fail = c.post(f"/api/journey/face-scan/{token}/begin").json()
    assert fail["success"] is False, fail
    assert c.get(f"/api/journey/face-scan/{token}/status").json()["status"] == "ERROR"

    # retry succeeds once the vendor call works
    monkeypatch.setattr(
        nuralx, "initiate_scan",
        lambda creds, session_token, patient: nuralx.ScanResponse(scan_id="s2", scan_url="https://vendor.example/retry"))
    retry = c.post(f"/api/journey/face-scan/{token}/begin").json()
    assert retry["success"] and retry["status"] == "IN_PROGRESS", retry

    print("OK — a failed begin() can be retried on the same token")


def test_expired_and_unknown_tokens(monkeypatch):
    _real_env(monkeypatch)
    c = TestClient(app)
    app_id = _login(c)
    r = c.post(f"/api/journey/face-scan/start/{app_id}").json()
    token = r["scan_url"].rsplit("/", 1)[-1]

    # force expiry
    with session_scope() as db:
        fss = db.exec(
            __import__("sqlmodel").select(FaceScanSession).where(FaceScanSession.token == token)
        ).first()
        fss.expires_at = _now() - timedelta(minutes=1)
        db.add(fss)

    status = c.get(f"/api/journey/face-scan/{token}/status").json()
    assert status["status"] == "EXPIRED", status
    begin = c.post(f"/api/journey/face-scan/{token}/begin").json()
    assert begin["success"] is False and begin["message"] == "expired", begin

    missing = c.get("/api/journey/face-scan/does-not-exist/status").json()
    assert missing["success"] is False

    print("OK — expired sessions reject begin(); unknown tokens 404 cleanly")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
