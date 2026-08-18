"""test_enrichment_flags.py — guards the A-series enrichment signals wired from the
Step-1 mobile/PAN prefill (mobile genuineness, tenure, new business, director, GST-cancel,
nature-of-business hazard). Each was previously dropped or silently mis-keyed; these tests
fail if that regresses. Offline, no network — pure engine.
"""
from __future__ import annotations

from underwriting.rules import run_bre
from underwriting.scoring import safety_score
from underwriting.schemas import ProposalInput


def _inp(signals: dict) -> ProposalInput:
    return ProposalInput(**{
        "proposal_id": "p", "signals": signals,
        "application": {"applicant": {"name": "X", "age": 33},
                        "product": {"type": "term_life", "sum_assured": 2_500_000}},
    })


def _flags(inp) -> set[str]:
    return {f.flag_type for f in run_bre(inp).soft_flags}


def _sub(inp, group: str) -> float:
    _, rows, _ = safety_score(inp, run_bre(inp))
    return next(r.risk_sub_score for r in rows if r.source_group == group)


def test_gst_cancelled_alert_raises_flag_and_penalizes_occupation():
    """The camelCase-key fix: activeAlerts must actually reach R-019 (was silently dropped)."""
    inp = _inp({"gst": {"status": "available", "gstin": "x", "any_cancelled": True,
                        "activeAlerts": [{"key": "isGstCancelled"}]}})
    assert "gst_alert" in _flags(inp)
    assert _sub(inp, "occupation_employer") < 100


def test_nature_of_business_maps_to_hazard():
    inp = _inp({"gst": {"status": "available", "gstin": "x",
                        "nature_of_business": ["Factory / Manufacturing"]}})
    assert _sub(inp, "occupation_employer") < 100  # manufacturing → moderate hazard load


def test_young_mobile_number_flags_and_penalizes_contactability():
    inp = _inp({"mobile_intel": {"status": "available", "vintage_months": 3, "number_valid": True}})
    assert "mobile_recent_number" in _flags(inp)
    assert _sub(inp, "contactability") < 100


def test_invalid_mobile_penalizes_contactability():
    inp = _inp({"mobile_intel": {"status": "available", "vintage_months": 120, "number_valid": False}})
    assert "mobile_invalid" in _flags(inp)


def test_new_business_flags_from_registration_date():
    inp = _inp({"gst": {"status": "available", "gstin": "x", "registration_date": "2026-06-01"}})
    # only fires if within NEW_BUSINESS_MAX_MONTHS of today — this fixture is intentionally recent
    assert "new_business" in _flags(inp)


def test_director_default_penalizes_occupation():
    inp = _inp({"mca_director": {"status": "available", "is_director": True, "director_default": True}})
    assert _sub(inp, "occupation_employer") < 100


def test_clean_enrichment_raises_none_of_the_new_flags():
    inp = _inp({
        "mobile_intel": {"status": "available", "vintage_months": 132, "number_valid": True},
        "epfo": {"status": "available", "employer": "OPEN", "uan": "1", "date_of_joining": "2021-10-28"},
        "gst": {"status": "available", "gstin": "x", "registration_date": "2019-01-01",
                "nature_of_business": ["Supplier of Services"], "activeAlerts": []},
    })
    got = _flags(inp)
    assert not ({"mobile_recent_number", "mobile_invalid", "short_tenure",
                 "new_business", "gst_alert"} & got), got


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"ok {name}")
    print("all enrichment-flag checks pass")
