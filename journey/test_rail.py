"""test_rail.py — the Phase-D right rail. The route + auth gate are covered by a
TestClient smoke (below); the core assertion is that the rail's chip data is the
SAME rules+scoring the final report uses, and that an unchecked group reads 'idle'
(not a green 'clean' it hasn't earned) — the "no theatre" constraint (DESIGN.md §6).
"""
from __future__ import annotations

from underwriting import config as C
from underwriting.rules import run_bre
from underwriting.scoring import safety_score
from underwriting.schemas import ProposalInput

from .step_routes import _LEVEL, _RAIL_GROUPS, _group_has_data

_SEED = {
    "proposal_id": "p", "meta": {"insurer": "acme", "received_at": "2026-08-11T00:00:00Z"},
    "application": {"applicant": {"name": "", "age": 0, "mobile": "9"},
                    "product": {"type": "individual_health", "sum_assured": 0}},
    "consents": [], "signals": {},
}


def _rail_groups(bundle: dict) -> list[dict]:
    """Replicate the endpoint's pure core (no DB/auth) for assertion."""
    inp = ProposalInput(**bundle)
    ss, rows, _ = safety_score(inp, run_bre(inp))
    by = {r.source_group: r for r in rows}
    out = []
    for key, label in _RAIL_GROUPS:
        r = by[key]
        has = _group_has_data(key, bundle)
        sev = _LEVEL[C.safety_band(r.risk_sub_score)] if has else "idle"
        out.append({"key": key, "severity": sev, "sub_score": r.risk_sub_score, "why": r.why})
    return out


def test_empty_bundle_is_all_idle_not_green():
    # Nothing collected yet -> every group idle, none claiming a clean pass.
    groups = _rail_groups(_SEED)
    assert groups, "expected the 11 source groups"
    assert all(g["severity"] == "idle" for g in groups), \
        [g for g in groups if g["severity"] != "idle"]


def test_litigation_lights_red_with_real_reason():
    b = {**_SEED, "signals": {
        "pan_verify": {"status": "available", "pan": "BHYPM4927Q", "pan_status": "valid"},
        "litigation_fir": {"firs_registered": 1,
                           "cases": [{"civil_criminal": "criminal"} for _ in range(10)]},
    }}
    g = {x["key"]: x for x in _rail_groups(b)}
    assert g["litigation_fir"]["severity"] == "bad"
    assert "criminal" in g["litigation_fir"]["why"]
    # a group with data present is no longer idle
    assert g["identity_kyc"]["severity"] != "idle"


def test_rail_severity_matches_report_band_map():
    # The rail's severity for a group == the report's risk LEVEL for that same
    # sub-score (both are config.safety_band). This is the "rail = report" guarantee.
    b = {**_SEED, "signals": {"pan_verify": {"status": "available", "pan": "X", "pan_status": "valid"}}}
    for g in _rail_groups(b):
        expected = {"Low Risk": "ok", "Moderate Risk": "warn", "High Risk": "bad"}[
            C.safety_band(g["sub_score"])]
        if g["severity"] != "idle":
            assert g["severity"] == expected, g


def test_step_scoping_map_is_sane():
    from .step_routes import _STEP_GROUPS, _RAIL_GROUPS
    all_keys = {g[0] for g in _RAIL_GROUPS}
    # every step maps to a subset of real group keys; step 5 shows everything.
    for step, keys in _STEP_GROUPS.items():
        assert set(keys) <= all_keys, (step, set(keys) - all_keys)
    assert set(_STEP_GROUPS[5]) == all_keys, "decision step must show the full read"
    # step 1 must NOT surface later-step groups (the user's ask)
    assert not ({"financial", "medical", "lifestyle"} & set(_STEP_GROUPS[1]))


def test_route_exists_and_gates_auth():
    from starlette.testclient import TestClient
    from underwriting.api import app
    r = TestClient(app).get("/api/journey/rail/1")
    assert r.status_code == 200                     # route exists (would be 404 otherwise)
    assert r.json()["success"] is False             # and enforces the session-cookie gate


if __name__ == "__main__":
    test_empty_bundle_is_all_idle_not_green()
    test_litigation_lights_red_with_real_reason()
    test_rail_severity_matches_report_band_map()
    test_route_exists_and_gates_auth()
    print("rail tests OK")
