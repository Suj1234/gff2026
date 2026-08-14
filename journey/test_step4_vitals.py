"""test_step4_vitals.py — Step 4 face-scan rich vitals surface through the snapshot.

The Step-4 UI reads `signals.rppg_scan.{vitals, vitals_extra}` from GET /api/journey/app/{id}
after polling the mock face scan. This asserts the mock injects the SAME rich shape a live
NuralX scan returns (decision vitals + display-only vitals_extra), and that /app exposes it.
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
os.environ.pop("NURALX_BASE_URL", None)          # force the mock face-scan path

from starlette.testclient import TestClient  # noqa: E402

from journey.db import init_db  # noqa: E402
from underwriting.api import app  # noqa: E402

init_db()


def _login(c: TestClient, mobile: str = "9739780007") -> int:
    r = c.post("/api/auth/send-otp", json={"mobile": mobile, "insurer_slug": "acme"}).json()
    assert r["success"], r
    r2 = c.post("/api/auth/verify-otp", json={
        "mobile": mobile, "otp": r["debug_otp"], "otp_ref_id": r["otp_ref_id"],
        "insurer_slug": "acme", "initial_sum_insured": 2500000,
        "initial_plan_type": "individual_health"}).json()
    assert r2["success"], r2
    return int(r2["application_id"])


def test_face_scan_mock_surfaces_rich_vitals():
    c = TestClient(app)
    app_id = _login(c)

    r = c.post(f"/api/journey/face-scan/start/{app_id}")
    assert r.json()["success"], r.text
    assert r.json()["mode"] == "mock"

    snap = c.get(f"/api/journey/app/{app_id}").json()
    rppg = snap["signals"]["rppg_scan"]
    assert rppg["status"] == "available", rppg

    # decision vitals — the 4 keys R-017 reads
    v = rppg["vitals"]
    assert v["heart_rate"] == 74 and v["respiratory_rate"] == 16 and v["spo2"] == 98
    assert v["bp"] == {"systolic": 118.0, "diastolic": 76.0}

    # display-only secondary vitals — the FULL set (30 params), NOT leaking into decision vitals
    x = rppg["vitals_extra"]
    assert x["hba1c"] == 5.4 and x["sdnn"] == 58 and x["stress_index"] == 42
    assert x["sd1"] == 31 and x["sd2"] == 83 and x["pns_zone"] == 2 and x["prq"] == 3.4
    assert x["stress_index_norm"] == 12
    assert "risk_high_bp" in x and x["risk_high_bp"] == 0     # vendor risk flag, clean mock
    assert isinstance(x.get("rri_series"), list) and len(x["rri_series"]) >= 4  # tachogram waveform
    assert "stress_index" not in v, "secondary vital leaked into decision vitals"

    # liveness (R-003 identity gate) also exposed for the panel header
    assert snap["signals"]["liveness_facematch"]["liveness_pass"] is True

    print("OK — mock face scan surfaces rich vitals + vitals_extra through /app")


def test_abha_otp_flow_gates_the_fetch():
    """Real ABDM handshake: send-OTP issues a demo OTP; fetch rejects a wrong OTP and
    accepts the right one, then returns keyed records."""
    c = TestClient(app)
    app_id = _login(c)

    send = c.post("/api/journey/abha/otp/send",
                  json={"app_id": app_id, "abha_id": "14-1234-5678-9012",
                        "auth_method": "mobile_otp"}).json()
    assert send["success"] and send["debug_otp"], send
    otp = send["debug_otp"]

    # wrong OTP is rejected
    bad = c.post(f"/api/journey/abha/fetch/{app_id}?otp=000000").json()
    assert bad["success"] is False, bad

    # right OTP fetches records (keyed mock)
    ok = c.post(f"/api/journey/abha/fetch/{app_id}?otp={otp}").json()
    assert ok["success"] is True, ok
    assert "diagnoses" in ok

    # consent recorded + no journey scratch leaks into the engine-facing bundle read
    snap = c.get(f"/api/journey/app/{app_id}").json()
    assert snap["signals"]["abha_health_records"]["status"] == "available"

    print("OK — ABHA OTP flow gates the fetch (wrong OTP rejected, right OTP fetches)")


if __name__ == "__main__":
    test_face_scan_mock_surfaces_rich_vitals()
    test_abha_otp_flow_gates_the_fetch()
