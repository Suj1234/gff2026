"""test_sources.py — vendor adapters behind the internal contract (§3, §9).

Proves the adapter rule holds:
  1. A real-vendor RAW response (camelCase, wrapped envelope, paise, text status)
     maps through the adapter to the exact internal contract shape the BRE reads.
  2. The mapped bundle round-trips into a valid `ProposalInput` and runs the pipeline
     — so a real vendor is a swap-in, not a rewrite.
  3. The mocks keep passing: fixtures authored directly against the internal shape
     pass through `adapt` unchanged (unregistered-or-identity), so the whole existing
     suite is unaffected by the adapter layer.

Mock the RESPONSE (the canned raw payloads below), never the step — the adapter is
real code, identical dev/staging/prod.
"""

from __future__ import annotations

import json
from pathlib import Path

from underwriting import sources
from underwriting.rules import run_bre
from underwriting.schemas import ProposalInput

FIX = Path(__file__).parent / "fixtures"


# A canned RAW PAN-vendor response (Karza/Signzy-style: `result` envelope, camelCase,
# a text panStatus). This is what the vendor actually returns; the adapter maps it.
RAW_PAN = {
    "requestId": "abc-123",
    "result": {
        "pan": "ABCPS1234K",
        "panStatus": "EXISTING AND VALID",
        "fullName": "Suresh Iyer",
        "dateOfBirth": "1990-03-14",
        "gender": "M",
        "aadhaarSeedingStatus": "SEEDED",
        "address": "12 MG Road, Koramangala, Bengaluru, 560034",
    },
}

# A canned RAW AA/BSA response (Perfios/Finbox-style: `analysis` envelope, paise,
# an incomeBasis label, verdict fields present that the adapter must DROP).
RAW_AA = {
    "analysis": {
        "accountHolderName": "Suresh Iyer",
        "imputedAnnualIncomePaise": 179_000_000,  # ₹17,90,000
        "avgMonthlyBalancePaise": 25_000_000,     # ₹2,50,000
        "expenseToIncomeRatio": 0.35,
        "incomeBasis": "GST_ITR",
        "credits": [{"type": "salary", "amount": 149000, "regular": True}],
        # a verdict the vendor shouldn't set and we must not ingest (§1.8):
        "riskTriggers": [{"finding": "irregular_salary", "risk": "high"}],
    },
}


def test_pan_adapter_maps_to_internal_contract():
    internal = sources.adapt("pan_verify", RAW_PAN)
    assert internal["pan"] == "ABCPS1234K"
    assert internal["pan_status"] == "valid"       # text label normalized (R-002 fact)
    assert internal["aadhaar_seeded"] is True
    assert internal["status"] == "available"
    # ProposalInput accepts it as-is (it IS the internal shape now).
    ProposalInput(**{
        "proposal_id": "ADAPT-PAN",
        "application": {"applicant": {"name": "Suresh Iyer", "age": 34},
                        "product": {"type": "individual_health", "sum_assured": 500000}},
        "signals": {"pan_verify": internal},
    })


def test_pan_adapter_unknown_status_fails_safe():
    """An unrecognized vendor status must NOT become 'valid' — fail safe to invalid
    so R-002 doesn't wave through an identity it couldn't confirm."""
    raw = {"result": {"pan": "X", "panStatus": "SOME_NEW_LABEL"}}
    assert sources.adapt("pan_verify", raw)["pan_status"] == "invalid"


def test_aa_adapter_maps_and_drops_verdicts():
    internal = sources.adapt("account_aggregator", RAW_AA)
    assert internal["imputed_annual_income"] == 1_790_000   # paise → rupees
    assert internal["income_source"] == "gst_itr"           # basis normalized (R-008 fact)
    assert "riskTriggers" not in internal and "risk_triggers" not in internal  # verdict dropped (§1.8)


def test_adapted_bundle_runs_the_pipeline():
    """A whole raw signals bundle → adapt_bundle → ProposalInput → BRE. Proves the
    vendor→contract seam produces something the real engine consumes end to end."""
    raw_signals = {"pan_verify": RAW_PAN, "account_aggregator": RAW_AA}
    internal_signals = sources.adapt_bundle(raw_signals)
    inp = ProposalInput(**{
        "proposal_id": "ADAPT-E2E",
        "application": {"applicant": {"name": "Suresh Iyer", "age": 34},
                        "product": {"type": "individual_health", "sum_assured": 500000}},
        "signals": internal_signals,
    })
    bre = run_bre(inp)  # must not raise; the adapted facts are consumed by real rules
    assert bre.outcome in {"CLEAN", "GREY-ZONE", "DECLINE", "REFER"}


def test_adapter_is_opt_in_mocks_untouched():
    """The adapter layer is opt-in: it sits at the raw-ingestion seam, and the pipeline
    (and the fixtures, which are authored in the internal shape) never routes through it.
    So the existing suite is untouched — proven by the fact that nothing in pipeline.py
    imports `sources`. An UNregistered source is passed through `adapt` unchanged, which
    is the contract for any source without a bespoke adapter yet."""
    passthrough = sources.adapt("some_unregistered_source", {"a": 1, "b": 2})
    assert passthrough == {"a": 1, "b": 2}  # identity for unregistered sources
    # And the pipeline path takes no dependency on the adapter registry.
    import underwriting.pipeline as P
    assert "sources" not in getattr(P, "__dict__", {})


def test_registry_lists_shipped_adapters():
    assert "pan_verify" in sources.registered()
    assert "account_aggregator" in sources.registered()
    assert "litigation_fir" in sources.registered()
    assert "email_intel" in sources.registered()
    assert "prescription_ocr" in sources.registered()


# A canned RAW litigation block (docs §1 — the mobile→PAN call's `data.litigation`,
# Paulson: 10 criminal cases, 1 pending, FIR + NI-Act-138 cheque-bounce riskTags).
RAW_LITIGATION = {
    "totalCases": 10, "pendingCases": 1, "criminalCases": 10, "highSeverityCases": 10,
    "filter": {"pincode_matched": True},
    "cases": [
        {"type": "Criminal", "status": "Pending", "severity": "high",
         "acts": ["bharatiya nyaya sanhita"], "sections": ["281"],
         "firDetails": [{"policeStation": "Oonnukal", "firYear": "2026", "firNo": "328"}],
         "riskTags": ["Criminal"]},
        {"type": "Criminal", "status": "Disposed", "severity": "high",
         "riskTags": ["Financial Liability", "Criminal", "Cheque bounce"]},
    ],
}


def test_litigation_adapter_maps_to_internal_contract():
    """The silent-miss fix (§6 gap #1): raw litigation → the internal `litigation_fir`
    shape the scorer/R-018 read. `type` → civil_criminal; firDetails[] → firs_registered;
    cheque-bounce riskTags carried."""
    internal = sources.adapt("litigation_fir", RAW_LITIGATION)
    assert internal["status"] == "available"
    assert internal["total_cases"] == 10 and internal["criminal_cases"] == 10
    assert internal["firs_registered"] == 1  # from the single case's firDetails
    assert all(c["civil_criminal"] == "criminal" for c in internal["cases"])
    assert internal["cases"][1]["cheque_bounce"] is True  # NI Act §138 riskTag


def test_litigation_adapter_clean_and_empty_are_safe():
    """A clean profile (0 cases) and a missing block must not crash and must not
    invent litigation (§11)."""
    assert sources.adapt("litigation_fir", {"totalCases": 0, "cases": []})["criminal_cases"] == 0
    assert sources.adapt("litigation_fir", {})["status"] == "unavailable"


def test_litigation_adapter_survives_malformed_input():
    """Trust boundary (§11): type-confused vendor JSON must degrade, never crash — and
    a non-list `firDetails` must NOT miscount as 1 FIR (len('x')==1)."""
    assert sources.adapt("litigation_fir", None)["status"] == "unavailable"
    assert sources.adapt("litigation_fir", {"cases": "nope"})["criminal_cases"] == 0   # non-list cases
    assert sources.adapt("litigation_fir", {"cases": ["x", 42, None]})["cases"] == []  # non-dict entries skipped
    fir = sources.adapt("litigation_fir", {"cases": [{"type": "Criminal", "firDetails": "x"}]})
    assert fir["firs_registered"] == 0  # garbage firDetails is not a real FIR


# A canned RAW email-intel response (live-gateway verified 2026-08-24: vendor fraud score
# is actually 1-999, HIGHER=RISKIER — not the docs' claimed 1-100/higher=safer).
RAW_EMAIL = {
    "success": True,
    "data": {
        "email": "sujeet.kr2496@gmail.com",
        "verification": {
            "validity": {"isDisposable": False, "result": "valid",
                         "smtpReachable": True, "isBlocked": False, "hasMxRecords": True},
            "individualMatch": [{"name": "sujeet kr", "match": False, "score": 0}],
            "spamRecord": {"isSpam": False, "reportCount": 0},
        },
        "fraud": {"risk": {"score": 83, "fraudRisk": "Very Low"}},
    },
}


def test_email_adapter_rescales_score():
    """Live-verified 2026-08-24: vendor 1-999 higher=riskier → internal 0-1, linear
    rescale (no inversion). 83 → 83/999 = 0.0831."""
    internal = sources.adapt("email_intel", RAW_EMAIL)
    assert internal["status"] == "available"
    assert internal["is_disposable"] is False and internal["is_spam"] is False
    assert internal["name_match"] is False
    assert internal["smtp_reachable"] is True and internal["is_blocked"] is False
    assert internal["has_mx_records"] is True
    assert abs(internal["fraud_risk_score"] - 0.0831) < 1e-4  # 83 / 999
    assert "fraudRisk" not in internal  # vendor verdict label dropped (§1.8)


def test_email_adapter_missing_score_is_absent_not_zero():
    """A missing vendor score must map to absent, NOT 0.0 (which would read 'safe')."""
    internal = sources.adapt("email_intel", {"data": {"email": "x@y.com"}})
    assert internal["fraud_risk_score"] is None


def test_email_adapter_survives_malformed_input():
    """Trust boundary (§11): a non-list individualMatch / non-dict nested field / a
    stringified score must degrade, never crash. A stringified '83' still rescales."""
    assert sources.adapt("email_intel", None)["status"] == "unavailable"
    # individualMatch arriving as a string used to IndexError then AttributeError.
    assert sources.adapt("email_intel", {"data": {"verification": {"individualMatch": "x"}}})["name_match"] is None
    assert sources.adapt("email_intel", {"data": {"verification": "x"}})["is_spam"] is None  # non-dict nested
    score = sources.adapt("email_intel", {"data": {"fraud": {"risk": {"score": "83"}}}})["fraud_risk_score"]
    assert abs(score - 0.0831) < 1e-4
    # a bool must not be treated as a numeric score (True/100 nonsense).
    assert sources.adapt("email_intel", {"data": {"fraud": {"risk": {"score": True}}}})["fraud_risk_score"] is None


# A canned RAW prescription-OCR extraction (HEALTH_AGENT_PLAN.md §2/§10) — this is the
# ACTUAL output captured from a live gemini-2.5-flash call against a synthetic cardiac
# prescription (underwriting/tests/fixtures/prescriptions/sample_cardiac_statin.png,
# verified 2026-08-21), not hand-invented — so the adapter is tested against a real
# vendor shape, same discipline as RAW_PAN/RAW_AA above.
RAW_PRESCRIPTION = {
    "clinic_or_doctor": "Apex Heart & Vascular Centre",
    "patient_name": "Vikram Nair",
    "date": "03-Jan-2025",
    "diagnosis_notes": "Known case of Ischaemic Heart Disease s/p PTCA (2021). Stable "
                        "angina, on secondary prevention therapy.",
    "drugs": [
        {"name": "Tab. Atorvastatin 20mg", "dosage": "0-0-1 (at night)", "duration": "90 days"},
        {"name": "Tab. Metoprolol 25mg", "dosage": "1-0-1", "duration": "90 days"},
        {"name": "Tab. Aspirin 75mg", "dosage": "0-1-0 (after lunch)", "duration": "90 days"},
    ],
    "raw_text": "Apex Heart & Vascular Centre\nDr. Suresh Menon...\nRx\n1. Tab. "
                "Atorvastatin 20mg\n0-0-1 (at night) - 90 days\n...",
}


def test_prescription_ocr_adapter_maps_to_internal_contract():
    internal = sources.adapt("prescription_ocr", RAW_PRESCRIPTION)
    assert internal["status"] == "available"
    assert internal["drug_names"] == [
        "Tab. Atorvastatin 20mg", "Tab. Metoprolol 25mg", "Tab. Aspirin 75mg",
    ]
    assert internal["diagnosis_notes"] == [RAW_PRESCRIPTION["diagnosis_notes"]]
    assert internal["raw_text"] and "Apex Heart" in internal["raw_text"][0]
    # ProposalInput accepts it as-is (it IS the internal shape now).
    ProposalInput(**{
        "proposal_id": "ADAPT-RX",
        "application": {"applicant": {"name": "Vikram Nair", "age": 58},
                        "product": {"type": "individual_health", "sum_assured": 500000}},
        "signals": {"prescription_ocr": internal},
    })


def test_prescription_ocr_adapter_no_upload_is_unavailable():
    """No document uploaded / OCR never ran → unavailable, not a clean empty record
    (§11) — absent ≠ assessed-clean, same discipline as mock_abha.py."""
    assert sources.adapt("prescription_ocr", None)["status"] == "unavailable"
    assert sources.adapt("prescription_ocr", {})["status"] == "unavailable"


def test_prescription_ocr_adapter_survives_malformed_input():
    """Trust boundary (§11): a non-list drugs field, non-dict/non-str drug entries, and
    non-string notes must degrade, never crash."""
    assert sources.adapt("prescription_ocr", {"drugs": "nope"})["drug_names"] == []
    assert sources.adapt("prescription_ocr", {"drugs": [1, None, {"name": None}]})["drug_names"] == []
    assert sources.adapt("prescription_ocr", {"diagnosis_notes": 42})["diagnosis_notes"] == []
    # a garbled/no-legible-text page is still a completed OCR attempt, not "unavailable":
    result = sources.adapt("prescription_ocr", {"raw_text": "", "drugs": []})
    assert result["status"] == "available" and result["drug_names"] == []
