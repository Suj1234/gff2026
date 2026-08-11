"""test_phase_b.py — Phase B: wire the real vendor APIs into the bundle (JOURNEY_PLAN.md §8).

Three vendor seams, each an adapter over a canned RESPONSE (mock the response, never the
step — §3):

  B1. iAdore (Perfios) bank statement → `account_aggregator` + the STEP_UP re-judge's
      `follow_up_observations.bank_statement`.
  B2. NuralX face scan webhook → `rppg_scan` / `liveness_facematch` / `facial_bmi_smoking`.
  B3. mock ABHA API keyed off PAN/mobile → `abha_health_records`, powering R-010.

These tests prove each mapping lands in the exact internal contract the engine reads,
and that a real rule fires on the mapped facts end to end.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from underwriting import mock_abha, sources
from underwriting.api import app
from underwriting.rules import r003_identity_fraud, r010_non_disclosure, r017_rppg, run_bre
from underwriting.schemas import ProposalInput, Signals
from underwriting.sources import bank_statement as bs
from underwriting.sources import nuralx as nx


# ===========================================================================
# B1 — iAdore bank statement
# ===========================================================================

# A canned iAdore report (Perfios-style `analysis` envelope; a mix of paise + rupee
# fields; verdict fields present that the adapter MUST drop — §1.8).
RAW_IADORE = {
    "analysis": {
        "accountHolderName": "Anjali Nair",
        "imputedAnnualIncomePaise": 90_000_000,   # ₹9,00,000
        "salaryCreditMonthly": 75_000,            # rupees
        "avgMonthlyBalancePaise": 6_000_000,      # ₹60,000
        "expenseToIncomeRatio": 0.5,
        "incomeBasis": "SALARY_CREDITS",
        "credits": [{"type": "salary", "amount": 75000, "regular": True}],
        # vendor verdicts the adapter must NOT ingest (§1.8):
        "riskTriggers": [{"finding": "irregular", "risk": "high"}],
        "incomeVerified": True,
    },
}


def test_iadore_maps_to_account_aggregator_and_drops_verdicts():
    aa = bs.to_account_aggregator(RAW_IADORE)
    assert aa["status"] == "available"
    assert aa["imputed_annual_income"] == 900_000   # paise → rupees
    assert aa["avg_monthly_balance"] == 60_000      # paise → rupees
    assert aa["income_source"] == "salary"          # basis normalized (R-008 fact)
    assert "riskTriggers" not in aa and "incomeVerified" not in aa  # verdicts dropped


def test_iadore_maps_to_follow_up_bank_statement_shape():
    """The STEP_UP re-judge reads exactly {verified_annual_income, salary_credit_monthly,
    avg_monthly_balance, corroborates_declared_income}."""
    obs = bs.to_follow_up_observation(RAW_IADORE, declared_annual_income=900_000)
    assert set(obs) >= {
        "status", "verified_annual_income", "salary_credit_monthly",
        "avg_monthly_balance", "corroborates_declared_income",
    }
    assert obs["verified_annual_income"] == 900_000
    assert obs["salary_credit_monthly"] == 75_000
    assert obs["avg_monthly_balance"] == 60_000
    assert obs["corroborates_declared_income"] is True   # 900k ≥ 0.8 × 900k declared


def test_iadore_corroboration_is_our_judgment_not_the_vendors():
    """Corroboration is OUR call from facts (§1.8): the same statement fails to
    corroborate a much larger declared income."""
    weak = bs.to_follow_up_observation(RAW_IADORE, declared_annual_income=5_000_000)
    assert weak["corroborates_declared_income"] is False
    # Unknown declared income at gather time → we don't assert either way.
    unknown = bs.to_follow_up_observation(RAW_IADORE)
    assert unknown["corroborates_declared_income"] is None


def test_iadore_report_rupee_and_monthly_only_variants():
    """Report versions vary: a rupee-only annual figure, or only a monthly salary to
    annualize — both must resolve."""
    rupee = bs.to_account_aggregator({"summary": {"verifiedAnnualIncome": 1_200_000}})
    assert rupee["imputed_annual_income"] == 1_200_000
    monthly_only = bs.to_account_aggregator({"analysis": {"salaryCreditMonthly": 50_000}})
    assert monthly_only["imputed_annual_income"] == 600_000  # 50k × 12


def test_iadore_adapter_registered_and_maps_to_aa_contract():
    """Registered under its own key so it doesn't clobber the AA adapter; both land in
    the internal account_aggregator shape (a deployment picks one income source)."""
    assert "account_aggregator_bank_statement" in sources.registered()
    internal = sources.adapt("account_aggregator_bank_statement", RAW_IADORE)
    assert internal["imputed_annual_income"] == 900_000


def test_iadore_adapter_survives_empty_and_garbage():
    for bad in (None, {}, {"analysis": {}}, {"analysis": "nope"}):
        assert bs.to_account_aggregator(bad)["status"] == "unavailable"
        assert bs.to_follow_up_observation(bad)["status"] == "unavailable"


def test_iadore_unmatched_schema_warns_not_silent(caplog):
    """E3 — a report that PARSES but matches NO income/balance/salary field (an unknown
    iAdore schema version) must WARN, not silently return clean-looking all-None income.
    The tripwire until a real report is captured and pinned (JOURNEY_PLAN §later E2)."""
    import logging

    unknown = {"analysis": {"someNewIncomeKey": 900000, "otherField": "x"}}
    with caplog.at_level(logging.WARNING, logger="underwriting.sources.bank_statement"):
        aa = bs.to_account_aggregator(unknown)
    assert aa["imputed_annual_income"] is None            # nothing matched
    assert any("matched NO income" in r.message for r in caplog.records), \
        "an unmatched-schema report must emit a coverage warning"


def test_iadore_follow_up_drives_step_up_rejudge(monkeypatch):
    """End-to-end: an iAdore report mapped to the follow-up shape drives the STEP_UP
    income re-judge to a corroborated ISSUE (the B1 done-when).

    The pipeline's `_fixture_gather` reads `follow_up_observations.bank_statement`; we
    populate it from the iAdore adapter (not a hand-written dict), so the vendor report
    flows into the re-judge. The judge is stubbed offline: cycle 1 asks for income
    corroboration, cycle 2 (after the bank statement lands) rules it benign, grounded in
    the follow-up path."""
    from underwriting import judge as J
    from underwriting import pipeline
    from underwriting.schemas import FlagRuling

    obs = bs.to_follow_up_observation(RAW_IADORE, declared_annual_income=900_000)
    inp = ProposalInput(**{
        "proposal_id": "IADORE-STEPUP",
        "application": {
            "applicant": {"name": "Anjali Nair", "age": 30},
            "occupation": {"declared_type": "self_employed"},
            "product": {"type": "individual_health", "sum_assured": 4_000_000},
            "financial": {"declared_annual_income": 900_000},
            "health_declaration": {"height_cm": 162, "weight_kg": 56, "bmi": 21.3},
        },
        "signals": {
            "liveness_facematch": {"status": "available", "liveness_pass": True,
                                   "face_match_score": 0.96, "deepfake_flag": False},
            "account_aggregator": {"status": "available", "imputed_annual_income": 900_000,
                                   "income_source": "AA_fallback_only", "avg_monthly_balance": 60_000},
        },
        # The iAdore-derived follow-up — what the gather cycle returns for bank_statement.
        "follow_up_observations": {"bank_statement": obs},
    })

    calls = {"n": 0}

    def fake_judge(evidence_bundle, flags, follow_up_observations=None):
        calls["n"] += 1
        second = calls["n"] >= 2
        out = []
        for f in flags:
            fid = f["flag_id"] if isinstance(f, dict) else f.flag_id
            if second:
                out.append(FlagRuling(
                    flag_id=fid, ruling="benign_explained",
                    cited_evidence=["follow_up_observations.bank_statement.verified_annual_income"]))
            else:
                out.append(FlagRuling(
                    flag_id=fid, ruling="needs_income_corroboration",
                    cited_evidence=["signals.account_aggregator.imputed_annual_income"]))
        return out

    monkeypatch.setattr(pipeline, "run_judge", fake_judge)
    monkeypatch.setattr(J, "extract_condition", lambda note: [])

    result = pipeline.run(inp)
    assert result.judge_cycles == 2
    assert result.decision.verdict == "ISSUE"


def test_real_iadore_gatherer_drives_step_up_end_to_end(monkeypatch):
    """E1 — the REAL gather seam: `make_iadore_gatherer` runs the iAdore client on the
    uploaded PDF INSIDE the STEP_UP cycle (not a hand-stuffed follow_up). This proves the
    connector the first Phase-B pass was missing: uploaded PDF → iAdore analyze → adapt →
    gather → re-judge → ISSUE. `analyze` is injected (mock the response, never the step)."""
    from underwriting import judge as J
    from underwriting import pipeline
    from underwriting.schemas import FlagRuling

    # The injected iAdore client: called with the uploaded PDF path, returns a report.
    def fake_analyze(path):
        assert path == "/uploads/anjali.pdf"
        return RAW_IADORE

    inp = ProposalInput(**{
        "proposal_id": "IADORE-REAL-GATHER",
        "application": {
            "applicant": {"name": "Anjali Nair", "age": 30},
            "occupation": {"declared_type": "self_employed"},
            "product": {"type": "individual_health", "sum_assured": 4_000_000},
            "financial": {"declared_annual_income": 900_000},
            "health_declaration": {"height_cm": 162, "weight_kg": 56, "bmi": 21.3},
        },
        "signals": {
            "liveness_facematch": {"status": "available", "liveness_pass": True,
                                   "face_match_score": 0.96, "deepfake_flag": False},
            "account_aggregator": {"status": "available", "imputed_annual_income": 900_000,
                                   "income_source": "AA_fallback_only", "avg_monthly_balance": 60_000},
        },
        # NO pre-canned follow_up_observations — the real gatherer produces it live.
        "documents": [{"type": "bank_statement", "path": "/uploads/anjali.pdf"}],
    })

    calls = {"n": 0}

    def fake_judge(evidence_bundle, flags, follow_up_observations=None):
        calls["n"] += 1
        second = calls["n"] >= 2
        out = []
        for f in flags:
            fid = f["flag_id"] if isinstance(f, dict) else f.flag_id
            ruling = "benign_explained" if second else "needs_income_corroboration"
            cited = (["follow_up_observations.bank_statement.verified_annual_income"] if second
                     else ["signals.account_aggregator.imputed_annual_income"])
            out.append(FlagRuling(flag_id=fid, ruling=ruling, cited_evidence=cited))
        return out

    monkeypatch.setattr(pipeline, "run_judge", fake_judge)
    monkeypatch.setattr(J, "extract_condition", lambda note: [])

    gather = bs.make_iadore_gatherer(analyze=fake_analyze)
    result = pipeline.run(inp, gather=gather)
    assert result.judge_cycles == 2
    # The re-judge's citation resolved against the iAdore-produced follow-up → ISSUE.
    assert result.decision.verdict == "ISSUE"
    assert result.follow_up["bank_statement"]["verified_annual_income"] == 900_000


def test_real_iadore_gatherer_fails_safe_without_upload(monkeypatch):
    """E1 fail-safe: no uploaded PDF → the gatherer returns bank_statement `unavailable`
    (never a crash, never a silently-clean income). The re-judge then can't corroborate,
    so the grounding/next-step logic keeps it out of ISSUE."""
    from underwriting.schemas import ProposalInput as PI

    gather = bs.make_iadore_gatherer(analyze=lambda p: RAW_IADORE)
    inp = PI(**{"proposal_id": "NO-PDF",
                "application": {"applicant": {"name": "A", "age": 30},
                                "product": {"type": "individual_health", "sum_assured": 500_000}}})
    out = gather("NO-PDF", ["request_additional_document(bank_statement)"], inp)
    assert out["bank_statement"]["status"] == "unavailable"


def test_real_iadore_gatherer_fails_safe_on_vendor_error():
    """E1 fail-safe: iAdore raising (gateway down / bad PDF) → bank_statement
    `unavailable`, not a crash propagating out of the pipeline."""
    def boom(path):
        raise RuntimeError("gateway down")

    gather = bs.make_iadore_gatherer(analyze=boom)
    inp = ProposalInput(**{
        "proposal_id": "ERR",
        "application": {"applicant": {"name": "A", "age": 30},
                        "product": {"type": "individual_health", "sum_assured": 500_000}},
        "documents": [{"type": "bank_statement", "path": "/x.pdf"}],
    })
    out = gather("ERR", ["request_additional_document(bank_statement)"], inp)
    assert out["bank_statement"]["status"] == "unavailable"
    assert "iadore_error" in out["bank_statement"]["reason"]


# ===========================================================================
# B2 — NuralX face scan webhook
# ===========================================================================

# A canned NuralX success webhook body (docs §5): mixed field shapes — some
# {value, confidenceLevel}, some plain, BP nested, plus liveness + bmi/smoking.
RAW_NURALX_OK = {
    "status": "completed",
    "client_transaction_ID": "uuid-1",
    "results": {
        "pulseRate": {"value": 72, "confidenceLevel": 0.91},
        "respirationRate": {"value": 16, "confidenceLevel": 0.87},
        "bloodPressure": {"value": {"systolic": 118, "diastolic": 76}, "confidenceLevel": 0.82},
        "oxygenSaturation": 97,
        "liveness": {"livenessPass": True, "livenessScore": 0.94, "faceMatchScore": 0.97,
                     "deepfakeFlag": False},
        "bmiEstimate": 24.5,
        "smokingEstimate": "non_smoker",
    },
}


def test_nuralx_maps_rppg_vitals_to_engine_keys():
    """Vitals keyed heart_rate / respiratory_rate / spo2 / bp — the exact keys R-017 reads."""
    r = nx.to_rppg_scan(RAW_NURALX_OK)
    assert r["status"] == "available"
    v = r["vitals"]
    assert v["heart_rate"] == 72          # {value,...} unwrapped
    assert v["respiratory_rate"] == 16
    assert v["spo2"] == 97                # plain number
    assert v["bp"] == {"systolic": 118, "diastolic": 76}  # nested BP


def test_nuralx_maps_liveness_facematch_r003_shape():
    lf = nx.to_liveness_facematch(RAW_NURALX_OK)
    assert lf == {
        "status": "available", "liveness_pass": True, "liveness_score": 0.94,
        "face_match_score": 0.97, "deepfake_flag": False,
    }


def test_nuralx_maps_facial_bmi_smoking():
    b = nx.to_facial_bmi_smoking(RAW_NURALX_OK)
    assert b["bmi_estimate"] == 24.5 and b["smoking_estimate"] == "non_smoker"


def test_nuralx_failure_and_timeout_are_unavailable_not_crash():
    """Unreachable / failure webhook → every signal `unavailable` (§11), never a crash."""
    for body in (
        {"status": "error", "client_transaction_ID": "x"},
        {"status": "timeout", "client_transaction_ID": "x"},
        {"status": "completed", "results": {}},   # empty results = no scan
        None, {},
    ):
        sig = nx.to_signals(body)
        assert sig["rppg_scan"]["status"] == "unavailable"
        assert sig["liveness_facematch"]["status"] == "unavailable"
        assert sig["facial_bmi_smoking"]["status"] == "unavailable"


def test_nuralx_deepfake_webhook_drives_r003_decline():
    """A deepfake-flagged NuralX result, mapped, fires the R-003 hard DECLINE gate."""
    body = {"status": "completed", "results": {
        "pulseRate": 70, "liveness": {"livenessPass": False, "deepfakeFlag": True}}}
    lf = nx.to_liveness_facematch(body)
    sig = Signals(liveness_facematch=lf)
    from underwriting.schemas import RuleOutcome
    assert r003_identity_fraud(sig).outcome == RuleOutcome.HARD_DECLINE


def test_nuralx_abnormal_vital_drives_r017_stepup():
    """A tachycardic pulse, mapped to rppg_scan.vitals, triggers R-017 step-up."""
    body = {"status": "completed", "results": {"pulseRate": 130, "oxygenSaturation": 97}}
    sig = Signals(rppg_scan=nx.to_rppg_scan(body))
    assert r017_rppg(sig).beyond_matrix is True


def test_nuralx_route_callback_populates_internal_signals(monkeypatch):
    """The webhook route fans the raw body out to the internal signal shapes and exposes
    them on the session poll (what the journey merges into the bundle)."""
    import underwriting.nuralx_routes as routes

    monkeypatch.setenv("NURALX_CALLBACK_SECRET", "test-secret")  # scoped, auto-restored
    token = "webhook-test-token"
    routes._SESSIONS[token] = {"token": token, "status": "PENDING", "scan_url": "x",
                               "vitals": None, "raw_results": None, "signals": None}
    client = TestClient(app)
    body = {**RAW_NURALX_OK, "client_transaction_ID": token}
    resp = client.post("/nuralx/callback?key=test-secret", json=body)
    assert resp.status_code == 200
    sig = routes._SESSIONS[token]["signals"]
    assert sig["rppg_scan"]["vitals"]["heart_rate"] == 72
    assert sig["liveness_facematch"]["liveness_pass"] is True


# ===========================================================================
# B3 — mock ABHA API keyed off PAN/mobile
# ===========================================================================

def test_abha_keyed_off_pan_returns_scripted_record():
    rec = mock_abha.records_for(pan="BHYPM4927Q")
    assert rec["status"] == "available"
    assert "E11.9" in rec["icd_codes"] and "I25.10" in rec["icd_codes"]
    assert "metformin" in rec["prescriptions"]
    assert rec["unstructured_notes"]  # free-text present for the LLM path


def test_abha_keyed_off_mobile_and_plus91_normalized():
    """Same record via the mobile key; +91 / spaces / dashes normalize to the 10 digits."""
    by_mobile = mock_abha.records_for(mobile="9739780007")
    assert by_mobile["icd_codes"] == mock_abha.records_for(pan="BHYPM4927Q")["icd_codes"]
    assert mock_abha.records_for(mobile="+91 97397 80007")["icd_codes"] == by_mobile["icd_codes"]


def test_abha_unknown_identity_is_clean_not_unavailable():
    """An unknown identity → a CLEAN record (lookup ran, nothing adverse), which is
    distinct from `unavailable` (lookup could not run)."""
    rec = mock_abha.records_for(pan="ZZZPZ0000Z")
    assert rec["status"] == "available" and rec["icd_codes"] == []
    outage = mock_abha.records_for(pan="ZZZPZ0000Z", found=False)
    assert outage["status"] == "unavailable"


def test_abha_returns_only_engine_fields():
    """The record is exactly the fields the engine reads (schemas + postpone_check)."""
    rec = mock_abha.records_for(pan="BHYPM4927Q")
    assert set(rec) == {
        "status", "diagnoses", "icd_codes", "prescriptions", "unstructured_notes",
        "days_since_acute_event", "active_pregnancy",
    }


def test_abha_postpone_and_pregnancy_records():
    from underwriting.rules import postpone_check
    from underwriting.schemas import RuleOutcome

    acute = mock_abha.records_for(pan="POSTPONE01A")
    assert acute["days_since_acute_event"] == 21
    sig = Signals(abha_health_records=acute)
    assert postpone_check(sig).outcome == RuleOutcome.POSTPONE

    preg = mock_abha.records_for(pan="PREGNANT01A")
    assert preg["active_pregnancy"] is True
    assert postpone_check(Signals(abha_health_records=preg)).outcome == RuleOutcome.POSTPONE


def test_abha_record_fires_r010_non_disclosure():
    """The B3 done-when: a keyed ABHA record drives R-010 on a declared-clean applicant.
    The applicant declared no conditions; ABHA shows undisclosed diabetes + cardiac."""
    rec = mock_abha.records_for(pan="BHYPM4927Q")
    inp = ProposalInput(**{
        "proposal_id": "ABHA-R010",
        "application": {
            "applicant": {"name": "Paulson Mathew", "age": 34},
            "product": {"type": "individual_health", "sum_assured": 500_000},
            # declared clean — the non-disclosure
            "health_declaration": {"conditions": [], "past_medical_history": "none"},
        },
        "signals": {"abha_health_records": rec},
    })
    bre = run_bre(inp)  # structured-only path (no LLM) still catches ICD/drug evidence
    r010 = next(r for r in bre.rule_results if r.rule_id == "R-010")
    assert r010.flags, "R-010 must flag the undisclosed condition(s)"
    undisclosed = r010.flags[0].context.get("undisclosed", [])
    assert "diabetes" in undisclosed and "heart_disease" in undisclosed


def test_abha_free_text_only_fires_r010_only_via_extractor():
    """E5 — the messy-ABHA path (§4.2): a record whose ONLY evidence is a free-text note
    (no ICD codes, no drugs) is SILENT on the deterministic path and fires R-010 ONLY
    when the LLM `extract_condition` reads the note. This is the path the structured
    BHYPM4927Q record would otherwise mask — proving the free-text extraction works, not
    just the structured crosswalk."""
    from underwriting.schemas import HealthDeclaration

    rec = mock_abha.records_for(pan="MESSY01A")
    assert rec["icd_codes"] == [] and rec["prescriptions"] == []   # free-text ONLY
    assert rec["unstructured_notes"], "the note is the only evidence"

    sig = Signals(abha_health_records=rec)
    health = HealthDeclaration(conditions=[])

    # Deterministic (no extractor): SILENT — there is no structured evidence to read.
    assert not r010_non_disclosure(sig, health).flags

    # With the extractor (what the LLM returns for the scanned note): R-010 fires.
    def fake_extract(note_text: str):
        return ["coronary artery disease"] if "coronary" in note_text.lower() else []

    ruled = r010_non_disclosure(sig, health, extractor=fake_extract)
    assert ruled.flags, "the free-text path must surface the undisclosed condition"
    assert "heart_disease" in ruled.flags[0].context.get("undisclosed", [])


def test_abha_route_is_consent_gated():
    """Even the mock goes through the consent step (files/CLAUDE.md §22): no consent →
    no record."""
    client = TestClient(app)
    ok = client.post("/abha/records", json={"pan": "BHYPM4927Q", "consent_granted": True})
    assert ok.status_code == 200 and ok.json()["signal"]["status"] == "available"
    denied = client.post("/abha/records", json={"pan": "BHYPM4927Q", "consent_granted": False})
    assert denied.json()["signal"]["status"] == "consent_declined"
