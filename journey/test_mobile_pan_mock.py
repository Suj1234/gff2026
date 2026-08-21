"""test_mobile_pan_mock.py — MOBILE_PAN_MOCK_MODE (HEALTH_AGENT_PLAN.md Phase K): any
mobile number resolves instantly via /verify-otp with no real vendor call, so journey
tests aren't hostage to a single live-verified test number or vendor gateway latency.

Env is set BEFORE importing journey/underwriting so db.py binds a throwaway DB.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name.replace(os.sep, '/')}"
os.environ["UW_DEBUG_OTP"] = "1"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ["MOBILE_PAN_MOCK_MODE"] = "1"  # the whole point of this test file
os.environ.pop("NURALX_BASE_URL", None)

from starlette.testclient import TestClient  # noqa: E402

from journey.db import init_db  # noqa: E402
from journey import mobile_pan  # noqa: E402
from underwriting.api import app  # noqa: E402

init_db()


def _login(c: TestClient, mobile: str) -> dict:
    r = c.post("/api/auth/send-otp", json={"mobile": mobile, "insurer_slug": "acme"}).json()
    assert r["success"], r
    return c.post("/api/auth/verify-otp", json={
        "mobile": mobile, "otp": r["debug_otp"], "otp_ref_id": r["otp_ref_id"],
        "insurer_slug": "acme", "initial_sum_insured": 2500000,
        "initial_plan_type": "individual_health"}).json()


def test_mock_mode_flag_is_read_correctly():
    assert mobile_pan.mock_mode_enabled() is True


def test_arbitrary_never_before_seen_number_resolves_instantly():
    """A number that appears NOWHERE else in this repo — proves mock mode really does
    accept ANY number, not just the pre-existing keyed demo identities."""
    c = TestClient(app)
    r2 = _login(c, "9123456780")
    assert r2["success"] is True, r2
    app_id = r2["application_id"]

    snap = c.get(f"/api/journey/app/{app_id}").json()
    assert snap["applicant"].get("name", "").startswith("Test Applicant")
    assert snap["signals"]["pan_verify"]["pan"], "synthetic PAN should be present"
    assert snap["signals"]["pan_verify"]["status"] == "available"


def test_synthetic_profile_is_deterministic():
    """Same number -> same synthetic profile every time (no RNG), so a test asserting
    on specific values doesn't flake."""
    p1 = mobile_pan.mock_profile_for("9123456780")
    p2 = mobile_pan.mock_profile_for("9123456780")
    assert p1 == p2


def test_keyed_demo_identity_still_resolves_in_mock_mode():
    """The keyed demo identities (mirroring mock_abha.py's Paulson/Sabarish) still work
    under mock mode, so existing fixtures/docs referencing them stay valid."""
    c = TestClient(app)
    r2 = _login(c, "9739780007")
    assert r2["success"] is True
    snap = c.get(f"/api/journey/app/{r2['application_id']}").json()
    assert snap["applicant"]["name"] == "Paulson Varghese"
    assert snap["signals"]["pan_verify"]["pan"] == "BHYPM4927Q"


def test_different_numbers_get_different_synthetic_profiles():
    p1 = mobile_pan.mock_profile_for("9111111111")
    p2 = mobile_pan.mock_profile_for("9222222222")
    assert p1["pan"] != p2["pan"]
    assert p1["identity"]["name"] != p2["identity"]["name"]


def test_no_real_vendor_call_is_attempted_in_mock_mode(monkeypatch):
    """The strongest guarantee: even if fetch_profile WOULD raise/hang, mock mode never
    calls it at all."""
    def boom(*a, **kw):
        raise AssertionError("fetch_profile should never be called in mock mode")
    monkeypatch.setattr(mobile_pan, "fetch_profile", boom)

    c = TestClient(app)
    r2 = _login(c, "9000000001")
    assert r2["success"] is True  # would have raised if fetch_profile were called


def test_no_real_sms_is_sent_in_mock_mode(monkeypatch):
    """The exact gap found 2026-08-21: MOBILE_PAN_MOCK_MODE only ever gated the downstream
    Mobile->PAN prefill, NOT the /send-otp SMS itself — so 'mock mode' still texted the
    applicant's real phone every time. This is the regression lock: even if
    msg91.send_sms_otp WOULD fire a real network call, mock mode must never reach it."""
    from journey import auth_routes, msg91

    def boom(*a, **kw):
        raise AssertionError("send_sms_otp should never be called in mock mode")
    monkeypatch.setattr(auth_routes.msg91, "send_sms_otp", boom)
    # Prove creds being present doesn't matter either — mock mode wins regardless.
    monkeypatch.setattr(msg91, "creds_present", lambda: True)

    c = TestClient(app)
    r = c.post("/api/auth/send-otp", json={"mobile": "9000000002", "insurer_slug": "acme"}).json()
    assert r["success"] is True  # would have raised if send_sms_otp were called
    assert r["debug_otp"]


def test_otp_fixed_code_skips_real_sms_and_is_always_the_configured_code(monkeypatch):
    """OTP_FIXED_CODE (demo env) is independent of MOBILE_PAN_MOCK_MODE — every OTP
    becomes the fixed code and no real SMS goes out, without mocking mobile->PAN."""
    from journey import auth_routes, msg91

    def boom(*a, **kw):
        raise AssertionError("send_sms_otp should never be called with OTP_FIXED_CODE set")
    monkeypatch.setattr(auth_routes.msg91, "send_sms_otp", boom)
    monkeypatch.setenv("OTP_FIXED_CODE", "123456")

    c = TestClient(app)
    r = c.post("/api/auth/send-otp", json={"mobile": "9000000003", "insurer_slug": "acme"}).json()
    assert r["success"] is True
    assert r["debug_otp"] == "123456"

    v = c.post("/api/auth/verify-otp", json={
        "mobile": "9000000003", "otp": "123456", "otp_ref_id": r["otp_ref_id"],
        "insurer_slug": "acme"}).json()
    assert v["success"] is True, v
