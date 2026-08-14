"""step_routes.py — the per-step data-collection endpoints the console fetch()es.

Every endpoint: (1) auth-gates on the session cookie, (2) mutates the application's
bundle (deep-copy + reassign so the JSON column persists — see auth_routes note),
(3) records event_log + api_call_log. Vendor calls are real where a client exists,
mock/fallback otherwise, always flagged mode=real|mock.

Step 1 (Identity): DigiLocker Aadhaar (link -> callback -> merge), email intel, consent.
Later steps add their own endpoints here.
"""

from __future__ import annotations

import copy
import os
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from . import auth, digilocker
from .db import get_session, track_api_call, track_event
from .models import Application, Consent
from .models import Session as SessionRow

router = APIRouter(prefix="/api/journey", tags=["journey-steps"])
# DigiLocker redirects the user back to DIGILOCKER_REDIRECT_URL (/.env = /digilocker/callback),
# so that route lives at the top level to match the registered redirect URL exactly.
callback_router = APIRouter(tags=["journey-steps"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _require_app(request: Request, app_id: int, db: Session) -> Optional[Application]:
    sess = auth.resolve_session(db, request.cookies.get(auth.COOKIE_NAME))
    if sess is None or sess.application_id != app_id:
        return None
    return db.get(Application, app_id)


def _mutate_bundle(app: Application, fn) -> None:
    """Apply fn to a deep-copied bundle and reassign (persists via JSON change-tracking)."""
    bundle = copy.deepcopy(dict(app.bundle))
    fn(bundle)
    app.bundle = bundle


def _age_from_dob(dob: str) -> Optional[int]:
    """Whole-years age from an ISO date (YYYY-MM-DD). Used for pricing + engine rules."""
    from datetime import date
    try:
        y, m, d = (int(x) for x in dob[:10].split("-"))
        born = date(y, m, d)
    except (ValueError, TypeError):
        return None
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _record_consent(db: Session, app: Application, ctype: str, framework: str) -> None:
    db.add(Consent(application_id=app.id, type=ctype, framework=framework, granted=True))

    def add(bundle):
        consents = bundle.setdefault("consents", [])
        if not any(c.get("type") == ctype for c in consents):
            consents.append({"type": ctype, "framework": framework, "granted": True})
    _mutate_bundle(app, add)


# ---------------------------------------------------------------------------
# Step 1 — manual identity entry / PAN fallback (when Mobile->PAN prefill is empty
# or the vendor is down). Keeps the journey usable without the flaky prefill.
# ---------------------------------------------------------------------------
class IdentityRequest(BaseModel):
    app_id: int
    name: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    pan: Optional[str] = None
    pincode: Optional[str] = None
    address: Optional[str] = None


@router.post("/identity")
def set_identity(req: IdentityRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    def add(bundle):
        applicant = bundle.setdefault("application", {}).setdefault("applicant", {})
        for k in ("name", "dob", "gender", "pincode", "address"):
            v = getattr(req, k)
            if v:
                applicant[k] = v
        if req.dob:
            age = _age_from_dob(req.dob)
            if age is not None:
                applicant["age"] = age
        if req.pan:
            sig = bundle.setdefault("signals", {})
            pv = sig.setdefault("pan_verify", {})
            pv["pan"] = req.pan.upper()
            # A manually-typed PAN is NOT a verification result — it has not been through a
            # KYC verifier. Leave pan_status = None (and status unavailable) so R-002 does NOT
            # hard-DECLINE on an unverified value (R-002 only fires on an ASSERTED non-'valid'
            # status). DigiLocker / a real PAN verifier is what sets pan_status to valid/invalid.
            pv.setdefault("status", "unavailable")
            pv["pan_status"] = None
    _mutate_bundle(app, add)
    db.add(app)
    track_event(db, event_type="identity_manual_entry", application_id=app.id, actor="customer",
                detail={"fields": [k for k in ("name", "dob", "gender", "pan", "pincode")
                                   if getattr(req, k)]})
    return {"success": True}


@router.post("/prefill-retry/{app_id}")
def prefill_retry(app_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    """Re-run the Mobile->PAN prefill (the POC vendor flaps 502s; let the UW retry)."""
    from . import mobile_pan
    from .auth_routes import _merge_profile_into_bundle
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    mobile = app.bundle.get("application", {}).get("applicant", {}).get("mobile")
    if not (mobile and mobile_pan.configured()):
        return {"success": False, "message": "prefill unavailable"}
    t0 = time.time()
    try:
        data = mobile_pan.fetch_profile(mobile, insurer_slug=app.insurer_slug)
        _merge_profile_into_bundle(app, data)
        db.add(app)
        track_api_call(db, provider="mobile_pan", endpoint="mobile-to-pan (retry)", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000))
        track_event(db, event_type="profile_prefilled", application_id=app.id, detail={"retry": True})
        return {"success": True, "pan": (data.get("pan") or "")}
    except Exception as e:
        track_api_call(db, provider="mobile_pan", endpoint="mobile-to-pan (retry)", mode="real",
                       application_id=app.id, ok=False, error=str(e)[:200])
        return {"success": False, "message": "vendor unavailable — enter details manually"}


# ---------------------------------------------------------------------------
# Step 1 — Email intelligence
# ---------------------------------------------------------------------------
class EmailRequest(BaseModel):
    app_id: int
    email: str


@router.post("/email")
def set_email(req: EmailRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    # Email intel: real vendor client (sources/email adapter path). For now record the
    # email as a fact; a live email-intel fetch wires the same way as mobile_pan when its
    # creds land. We flag mock until then so tracking is honest.
    def add(bundle):
        bundle.setdefault("signals", {})["email_intel"] = {"status": "available", "email": req.email}
    _mutate_bundle(app, add)
    track_api_call(db, provider="email", endpoint="(email captured)", mode="mock",
                   application_id=app.id, ok=True, request_summary={"email": req.email})
    track_event(db, event_type="email_captured", application_id=app.id, actor="customer",
                detail={"email": req.email})
    db.add(app)
    return {"success": True}


# ---------------------------------------------------------------------------
# Step 1 — DigiLocker Aadhaar e-KYC (link -> user grants -> callback -> download+merge)
# ---------------------------------------------------------------------------
@router.get("/digilocker/start/{app_id}")
def digilocker_start(app_id: int, request: Request, db: Session = Depends(get_session)):
    """Kick the DigiLocker flow: call /link, stash accessRequestId, redirect the user
    to the DigiLocker consent URL. Falls back to a keyed mock if not configured."""
    app = _require_app(request, app_id, db)
    if app is None:
        return RedirectResponse("/journey", status_code=303)

    _record_consent(db, app, "aadhaar_ekyc", "Aadhaar_Act")

    if not digilocker.configured():
        # Mock fallback: merge a keyed Aadhaar record straight away, no redirect.
        _merge_mock_aadhaar(db, app)
        db.add(app)
        return RedirectResponse(f"/journey/app/{app_id}?step=1&aadhaar=mock", status_code=303)

    state = uuid.uuid4().hex
    t0 = time.time()
    try:
        resp = digilocker.link(oauth_state=state, case_id=app.proposal_id)
        access_id = resp.get("requestId") or (resp.get("result") or {}).get("requestId")
        link_url = (resp.get("result") or {}).get("link")
        # stash accessRequestId on the bundle so the callback can complete the pull
        def add(bundle):
            bundle.setdefault("_journey", {})["digilocker_access_id"] = access_id
        _mutate_bundle(app, add)
        db.add(app)
        track_api_call(db, provider="digilocker", endpoint="/link", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                       response_summary={"has_link": bool(link_url)})
        track_event(db, event_type="digilocker_link_created", application_id=app.id)
        if link_url:
            return RedirectResponse(link_url, status_code=303)
    except Exception as e:
        track_api_call(db, provider="digilocker", endpoint="/link", mode="real",
                       application_id=app.id, ok=False, error=str(e)[:200])
        track_event(db, event_type="digilocker_link_error", application_id=app.id,
                    detail={"error": str(e)[:200]})
    return RedirectResponse(f"/journey/app/{app_id}?step=1&aadhaar=error", status_code=303)


@callback_router.get("/digilocker/callback")
def digilocker_callback(request: Request, db: Session = Depends(get_session)):
    """DigiLocker returns the user here after they grant consent. Complete the pull:
    documents() -> download() -> merge parsed Aadhaar+PAN into the bundle."""
    # The session cookie identifies the application (DigiLocker preserves our cookie via
    # the redirect back to our host).
    sess = auth.resolve_session(db, request.cookies.get(auth.COOKIE_NAME))
    if sess is None:
        return RedirectResponse("/journey", status_code=303)
    app = db.get(Application, sess.application_id)
    if app is None:
        return RedirectResponse("/journey", status_code=303)

    access_id = (app.bundle.get("_journey") or {}).get("digilocker_access_id")
    if not access_id:
        return RedirectResponse(f"/journey/app/{app.id}?step=1&aadhaar=error", status_code=303)

    t0 = time.time()
    try:
        docs = digilocker.documents(access_request_id=access_id, case_id=app.proposal_id)
        uris = [d.get("uri") for d in (docs.get("result") or [])
                if d.get("doctype") in ("ADHAR", "PANCR") and d.get("uri")]
        dl = digilocker.download(access_request_id=access_id, uris=uris, case_id=app.proposal_id)
        aadhaar = digilocker.parse_aadhaar(dl)
        pan = digilocker.parse_pan(dl)
        _merge_aadhaar(db, app, aadhaar, pan)
        db.add(app)
        track_api_call(db, provider="digilocker", endpoint="/documents+/download", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                       response_summary={"aadhaar_name": aadhaar.get("name"), "pan": pan.get("pan")})
        track_event(db, event_type="digilocker_fetched", application_id=app.id,
                    detail={"xml_verified": aadhaar.get("xml_verified")})
        return RedirectResponse(f"/journey/app/{app.id}?step=1&aadhaar=ok", status_code=303)
    except Exception as e:
        track_api_call(db, provider="digilocker", endpoint="/documents+/download", mode="real",
                       application_id=app.id, ok=False, error=str(e)[:200])
        track_event(db, event_type="digilocker_error", application_id=app.id,
                    detail={"error": str(e)[:200]})
        return RedirectResponse(f"/journey/app/{app.id}?step=1&aadhaar=error", status_code=303)


def _merge_aadhaar(db: Session, app: Application, aadhaar: dict, pan: dict) -> None:
    def add(bundle):
        sig = bundle.setdefault("signals", {})
        sig["aadhaar_ekyc"] = {
            "status": "available",
            "name": aadhaar.get("name"), "dob": aadhaar.get("dob"),
            "address": aadhaar.get("address"), "photo": aadhaar.get("photo_present"),
        }
        # PAN from DigiLocker corroborates pan_verify if the mobile fetch didn't set it.
        if pan.get("pan") and not (sig.get("pan_verify") or {}).get("pan"):
            sig["pan_verify"] = {
                "status": "available", "pan": pan["pan"],
                "pan_status": "valid" if (pan.get("status") == "A") else "invalid",
            }
    _mutate_bundle(app, add)


def _merge_mock_aadhaar(db: Session, app: Application) -> None:
    """Keyed mock when DigiLocker isn't configured — mirrors the applicant already prefilled."""
    applicant = app.bundle.get("application", {}).get("applicant", {})

    def add(bundle):
        bundle.setdefault("signals", {})["aadhaar_ekyc"] = {
            "status": "available",
            "name": applicant.get("name") or "AADHAAR HOLDER",
            "dob": applicant.get("dob"), "address": applicant.get("address"),
            "photo": True,
        }
    _mutate_bundle(app, add)
    track_api_call(db, provider="digilocker", endpoint="(mock)", mode="mock",
                   application_id=app.id, ok=True)
    track_event(db, event_type="digilocker_fetched", application_id=app.id, detail={"mock": True})


# ---------------------------------------------------------------------------
# Step 2 — Product & Sum Insured + Riders (journey-only indicative premium)
# ---------------------------------------------------------------------------
class ProductRequest(BaseModel):
    app_id: int
    product_type: str = "term_life"
    plan: str = "term_protect"
    sum_assured: int = 0
    tenure_years: int = 1
    # riders carry an optional amount: [{"id","amount"}] (or legacy ["id"]) — priced in pricing.py
    riders: list[Any] = []


def _premium_for(app: Application, req: "ProductRequest") -> dict:
    from . import pricing
    applicant = app.bundle.get("application", {}).get("applicant", {})
    hd = app.bundle.get("application", {}).get("health_declaration", {})
    return pricing.compute_premium(
        age=int(applicant.get("age") or 0),
        sum_assured=req.sum_assured,
        product_type=req.product_type,
        plan=req.plan,
        tenure_years=req.tenure_years,
        tobacco=bool(hd.get("tobacco")),
        pincode=applicant.get("pincode"),
        riders=req.riders,
    )


@router.post("/quote")
def quote(req: ProductRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Live indicative-premium recompute as the underwriter toggles riders/SI. No persist."""
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    return {"success": True, "premium": _premium_for(app, req)}


@router.post("/product")
def set_product(req: ProductRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Persist the product/SI/tenure/riders + the indicative premium into the bundle."""
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    premium = _premium_for(app, req)

    def add(bundle):
        product = bundle.setdefault("application", {}).setdefault("product", {})
        product["type"] = req.product_type
        product["plan"] = req.plan
        product["sum_assured"] = req.sum_assured
        product["tenure_years"] = req.tenure_years
        product["riders"] = req.riders                 # extra="allow" on the engine side
        product["premium"] = premium["total_annual"]
        product["premium_breakdown"] = premium         # journey-only display detail
    _mutate_bundle(app, add)
    db.add(app)
    track_event(db, event_type="product_selected", application_id=app.id, actor="customer",
                detail={"sum_assured": req.sum_assured, "riders": req.riders,
                        "premium": premium["total_annual"]})
    return {"success": True, "premium": premium}


# ---------------------------------------------------------------------------
# Step 3 — Financial: declared income/source, + iAdore bank-statement upload
# ---------------------------------------------------------------------------
class FinancialRequest(BaseModel):
    app_id: int
    declared_annual_income: Optional[int] = None
    source_of_funds: Optional[str] = None
    purpose_of_cover: Optional[str] = None


@router.post("/financial")
def set_financial(req: FinancialRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    def add(bundle):
        fin = bundle.setdefault("application", {}).setdefault("financial", {})
        if req.declared_annual_income is not None:
            fin["declared_annual_income"] = req.declared_annual_income
        if req.source_of_funds:
            fin["source_of_funds"] = req.source_of_funds
        if req.purpose_of_cover:
            fin["purpose_of_cover"] = req.purpose_of_cover
    _mutate_bundle(app, add)
    db.add(app)
    track_event(db, event_type="financial_declared", application_id=app.id, actor="customer",
                detail={"income": req.declared_annual_income, "source": req.source_of_funds})
    return {"success": True}


@router.post("/bank-statement")
async def upload_bank_statement(request: Request, db: Session = Depends(get_session)):
    """Upload a PDF bank statement -> iAdore analyze (real, reusing repo bank_statement.py
    + adapter) -> merge into signals.account_aggregator + follow_up_observations.bank_statement.

    NOTE: this is a plain document upload, NOT an RBI Account Aggregator consent flow.
    `signals.account_aggregator` is the ENGINE's internal income-signal key (the BRE reads it
    for R-007/R-008) — the statement-derived income lands in that slot ("bank statement REPLACES
    AA", JOURNEY_PLAN §3). The consent recorded is a document-sharing consent, not RBI-AA.
    Falls back cleanly if iAdore is unreachable (§11)."""
    from fastapi import Form, UploadFile
    form = await request.form()
    app_id = int(form.get("app_id", 0))
    upload = form.get("file")
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    if upload is None:
        return {"success": False, "message": "no file"}

    # Plain document-sharing consent (the applicant hands over their own statement PDF).
    _record_consent(db, app, "bank_statement_upload", "DPDP_Act")

    # Save the upload to a temp path for the iAdore client (it takes a file path).
    import tempfile
    from pathlib import Path
    suffix = Path(getattr(upload, "filename", "stmt.pdf")).suffix or ".pdf"
    data = await upload.read()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()

    t0 = time.time()
    try:
        import bank_statement as iadore  # repo-root client (real 3-call flow)
        from underwriting.sources import bank_statement as adapter
        raw = iadore.analyze(tmp.name)
        aa = adapter.to_account_aggregator(raw)
        declared = (app.bundle.get("application", {}).get("financial", {})
                    .get("declared_annual_income"))
        follow = adapter.to_follow_up_observation(raw, declared_annual_income=declared)

        def add(bundle):
            bundle.setdefault("signals", {})["account_aggregator"] = aa
            bundle.setdefault("follow_up_observations", {})["bank_statement"] = follow
        _mutate_bundle(app, add)
        db.add(app)
        track_api_call(db, provider="iadore", endpoint="submit+poll+report", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                       response_summary={"imputed_income": aa.get("imputed_annual_income")})
        track_event(db, event_type="bank_statement_analyzed", application_id=app.id,
                    detail={"imputed_income": aa.get("imputed_annual_income")})
        return {"success": True, "account_aggregator": aa}
    except Exception as e:
        track_api_call(db, provider="iadore", endpoint="submit+poll+report", mode="real",
                       application_id=app.id, ok=False, latency_ms=int((time.time()-t0)*1000),
                       error=str(e)[:200])
        track_event(db, event_type="bank_statement_error", application_id=app.id,
                    detail={"error": str(e)[:200]})
        return {"success": False, "message": "Bank-statement analysis unavailable — you can proceed; income can be corroborated later (STEP_UP)."}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Step 4 — Health Declaration (DGH): screeners + conditions + vitals/lifestyle,
# + NuralX face scan trigger, + ABHA fetch (mock, keyed off PAN/mobile).
# ---------------------------------------------------------------------------
class HealthRequest(BaseModel):
    app_id: int
    conditions: list[str] = []
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    tobacco: Optional[bool] = None
    alcohol: Optional[bool] = None
    drugs: Optional[bool] = None
    ongoing_medication: Optional[str] = None
    past_medical_history: Optional[str] = None
    family_history: list[str] = []


@router.post("/health")
def set_health(req: HealthRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    bmi = None
    if req.height_cm and req.weight_kg and req.height_cm > 0:
        m = req.height_cm / 100.0
        bmi = round(req.weight_kg / (m * m), 1)

    def add(bundle):
        hd = bundle.setdefault("application", {}).setdefault("health_declaration", {})
        hd["conditions"] = req.conditions
        for k in ("height_cm", "weight_kg", "tobacco", "alcohol", "drugs",
                  "ongoing_medication", "past_medical_history", "family_history"):
            v = getattr(req, k)
            if v is not None and v != []:
                hd[k] = v
        if bmi is not None:
            hd["bmi"] = bmi
    _mutate_bundle(app, add)
    db.add(app)
    track_event(db, event_type="health_declared", application_id=app.id, actor="customer",
                detail={"conditions": req.conditions, "bmi": bmi, "tobacco": req.tobacco})
    return {"success": True, "bmi": bmi}


@router.post("/face-scan/start/{app_id}")
def face_scan_start(app_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    """Kick a NuralX scan session (real if NURALX_* set, else a mock completed result).
    Returns the scan URL (real) so the UI can show a QR / link. Vitals arrive via the
    NuralX webhook (already mounted) OR the mock fills them immediately."""
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    if not os.getenv("NURALX_BASE_URL"):
        # Mock fallback: inject a plausible clean vitals + liveness pass so the demo runs.
        _merge_mock_vitals(db, app)
        db.add(app)
        return {"success": True, "mode": "mock",
                "message": "NuralX not configured — mock vitals injected."}

    t0 = time.time()
    try:
        from underwriting import nuralx
        creds = nuralx.creds_from_env()
        token, _ = nuralx._generate_access_token(
            creds.base_url, *nuralx._generate_client_credentials(creds))
        applicant = app.bundle.get("application", {}).get("applicant", {})
        patient = nuralx.Patient(
            name=applicant.get("name") or "Applicant",
            client_transaction_id=uuid.uuid4().hex,
        )
        resp = nuralx.initiate_scan(creds, token, patient)
        # persist a FaceScanSession so the webhook can correlate + resolve
        from .models import FaceScanSession
        db.add(FaceScanSession(
            token=uuid.uuid4().hex, application_id=app.id,
            client_transaction_id=patient.client_transaction_id,
            status="IN_PROGRESS", scan_access_url=resp.scan_access_url,
        ))
        track_api_call(db, provider="nuralx", endpoint="patient-data", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                       response_summary={"has_scan_url": bool(resp.scan_access_url)})
        track_event(db, event_type="face_scan_initiated", application_id=app.id)
        return {"success": True, "mode": "real", "scan_url": resp.scan_access_url}
    except Exception as e:
        track_api_call(db, provider="nuralx", endpoint="patient-data", mode="real",
                       application_id=app.id, ok=False, error=str(e)[:200])
        track_event(db, event_type="face_scan_error", application_id=app.id,
                    detail={"error": str(e)[:200]})
        return {"success": False, "message": "Face scan unavailable — proceed; vitals optional."}


def _merge_mock_vitals(db: Session, app: Application) -> None:
    """Inject a realistic NuralX result and adapt it, so the mock demo shows the SAME
    rich vitals a live scan returns (decision vitals + display-only vitals_extra). Routing
    through the real adapter keeps the mock honest — it can't drift from the live shape."""
    from underwriting.sources import nuralx as nuralx_adapter
    # Full NuralX result shape (all ~30 params a live scan returns), clean-vitals mock.
    mock_results = {
        "prq": {"value": 3.4},
        "pulseRate": {"value": 74}, "respirationRate": {"value": 16},
        "oxygenSaturation": {"value": 98},
        "bloodPressure": {"value": {"systolic": 118, "diastolic": 76}},
        "meanArterialPressure": {"value": 90}, "pulsePressure": {"value": 42},
        "cardiacWorkload": {"value": 3.2},
        "hemoglobin": {"value": 14.2}, "hemoglobinA1c": {"value": 5.4},
        "stressIndex": {"value": 42}, "stressLevel": {"value": 1},
        "normalizedStressIndex": {"value": 12},
        "wellnessIndex": {"value": 7}, "wellnessLevel": {"value": 3},
        "sdnn": {"value": 58}, "rmssd": {"value": 44}, "meanRri": {"value": 812},
        "lfhf": {"value": 1.6}, "sd1": {"value": 31}, "sd2": {"value": 83},
        "pnsIndex": {"value": 0.4}, "snsIndex": {"value": -0.2},
        "pnsZone": {"value": 2}, "snsZone": {"value": 2},
        "highBloodPressureRisk": {"value": 0}, "highHemoglobinA1CRisk": {"value": 0},
        "highFastingGlucoseRisk": {"value": 0}, "highTotalCholesterolRisk": {"value": 0},
        "lowHemoglobinRisk": {"value": 0},
        "rri": [{"interval": v, "timestamp": i} for i, v in enumerate(
            [797, 837, 739, 772, 834, 759, 806, 704, 790, 745, 812, 763, 739, 723,
             845, 730, 725, 812, 799, 736, 808, 735, 763, 753, 727, 777, 769, 749])],
    }
    rppg = nuralx_adapter.to_rppg_scan({"status": "completed", "results": mock_results})

    def add(bundle):
        sig = bundle.setdefault("signals", {})
        sig["rppg_scan"] = rppg
        sig["liveness_facematch"] = {"status": "available", "liveness_pass": True,
                                     "liveness_score": 0.96, "face_match_score": 0.94,
                                     "deepfake_flag": False}
    _mutate_bundle(app, add)
    track_api_call(db, provider="nuralx", endpoint="(mock)", mode="mock",
                   application_id=app.id, ok=True)
    track_event(db, event_type="face_scan_completed", application_id=app.id, detail={"mock": True})


# ABHA linking follows the real ABDM handshake: the applicant provides their ABHA number
# (14-digit) or ABHA address, verifies via a Mobile-OTP or Aadhaar-OTP auth method, then
# approves a consent request (record types + date range + purpose) before records are
# pulled. The OTP is a demo formality (mock) — the RECORD is the keyed mock; the STEP
# (consent-gated request) is real (files/CLAUDE.md §3 "mock the response, not the step").
class AbhaOtpSendRequest(BaseModel):
    app_id: int
    abha_id: str                       # 14-digit ABHA number or an ABHA address
    auth_method: str = "mobile_otp"    # mobile_otp | aadhaar_otp


@router.post("/abha/otp/send")
def abha_otp_send(req: AbhaOtpSendRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Begin ABHA verification: stash a (mock) OTP + the entered ABHA id on the bundle,
    return the demo OTP so the UI can show it. A live ABDM deploy sends a real OTP here."""
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    if not (req.abha_id or "").strip():
        return {"success": False, "message": "Enter an ABHA number or address."}
    # Deterministic demo OTP from the ABHA id (no RNG — reproducible on stage, §11 pure).
    otp = f"{(abs(hash(req.abha_id.strip())) % 900000) + 100000:06d}"

    def add(bundle):
        j = bundle.setdefault("_journey", {})
        j["abha_otp"] = otp
        j["abha_id"] = req.abha_id.strip()
        j["abha_auth_method"] = req.auth_method
    _mutate_bundle(app, add)
    db.add(app)
    track_api_call(db, provider="abha", endpoint="otp/send (mock)", mode="mock",
                   application_id=app.id, ok=True,
                   request_summary={"auth_method": req.auth_method})
    track_event(db, event_type="abha_otp_sent", application_id=app.id,
                detail={"auth_method": req.auth_method})
    method = "mobile" if req.auth_method == "mobile_otp" else "Aadhaar-linked mobile"
    return {"success": True, "debug_otp": otp,
            "message": f"OTP sent to the applicant's {method} number."}


@router.post("/abha/fetch/{app_id}")
def abha_fetch(app_id: int, request: Request, otp: str = "", db: Session = Depends(get_session)) -> dict:
    """Verify the ABHA OTP, record consent, then fetch health records (mock, keyed off
    PAN/mobile). The OTP must match the one issued by /abha/otp/send (unless none was
    issued — the legacy no-OTP path stays working for the E2E test + any direct caller)."""
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    issued = (app.bundle.get("_journey") or {}).get("abha_otp")
    if issued and otp.strip() != issued:
        return {"success": False, "message": "Incorrect OTP — check and try again."}

    _record_consent(db, app, "abha", "ABDM")

    from underwriting import mock_abha
    applicant = app.bundle.get("application", {}).get("applicant", {})
    pan = (app.bundle.get("signals", {}).get("pan_verify", {}) or {}).get("pan")
    mobile = applicant.get("mobile")
    record = mock_abha.records_for(pan=pan, mobile=mobile)

    def add(bundle):
        bundle.setdefault("signals", {})["abha_health_records"] = record
    _mutate_bundle(app, add)
    db.add(app)
    track_api_call(db, provider="abha", endpoint="(mock keyed)", mode="mock",
                   application_id=app.id, ok=True,
                   response_summary={"diagnoses": len(record.get("diagnoses", []))})
    track_event(db, event_type="abha_fetched", application_id=app.id,
                detail={"diagnoses": record.get("diagnoses", [])[:5]})
    return {"success": True, "diagnoses": record.get("diagnoses", []),
            "icd_codes": record.get("icd_codes", [])}


# ---------------------------------------------------------------------------
# Right rail (Phase D) — "what the agent sees at this stage". Runs the SAME
# rules + scoring the final report uses, on the partial bundle collected so far,
# so the rail = report by construction. Each group's chip carries its live
# sub-score + the scorer's own `why` strings (real fields only — no theatre).
# ---------------------------------------------------------------------------

# group key -> (display label, the signal keys / bundle paths it reads). A group
# with NONE of its sources present in the bundle is "idle" (grey, not-yet-returned)
# rather than a green "clean" it hasn't earned (DESIGN.md §6 idle state). The label
# order here is the rail's render order (grouped 1:1 to config.SAFETY_SCORE_WEIGHTS).
_RAIL_GROUPS = [
    ("identity_kyc",        "Identity / KYC"),
    ("financial",           "Financial"),
    ("occupation_employer", "Occupation"),
    ("medical",             "Medical"),
    ("lifestyle",           "Lifestyle"),
    ("fraud_check",         "Fraud"),
    ("litigation_fir",      "Litigation"),
    ("velocity_graph",      "Velocity"),
    ("geography",           "Geography"),
    ("insurance_portfolio", "Insurance portfolio"),
    ("contactability",      "Contactability"),
]

# Which source groups each STEP feeds (JOURNEY_PLAN §2 right-rail notes). The rail
# shows only the current step's groups; Step 5 (decision) shows the full accumulated
# read. The running composite score is still computed over ALL groups — only the
# CHIP display is scoped, so the underwriter isn't shown a wall of idle chips.
_STEP_GROUPS = {
    1: ["identity_kyc", "contactability", "fraud_check", "litigation_fir", "occupation_employer"],
    2: ["financial", "insurance_portfolio"],
    3: ["financial"],
    4: ["medical", "lifestyle", "fraud_check", "velocity_graph", "geography"],
    5: [g[0] for g in _RAIL_GROUPS],   # decision — everything the agent weighed
    6: [g[0] for g in _RAIL_GROUPS],
    7: [g[0] for g in _RAIL_GROUPS],
}

# 0-100 sub-score -> risk LEVEL, the SAME map report._level uses (config.safety_band
# cutoffs). Inlined so the rail endpoint doesn't import report.py (pulls in dspy/LLM).
_LEVEL = {"Low Risk": "ok", "Moderate Risk": "warn", "High Risk": "bad"}


def _group_has_data(group_key: str, bundle: dict) -> bool:
    """True once ANY input the group's sub-scorer (scoring.py) reads is present in the
    bundle — signal OR application-declared fact. Mirrors each `_s_*` scorer's inputs so
    an assessed group never shows 'idle' and an untouched one never shows a green 'clean'.
    Keep this in lockstep with scoring.py's per-group inputs."""
    sig = bundle.get("signals", {}) or {}
    appn = bundle.get("application", {}) or {}
    hd = appn.get("health_declaration") or {}
    fin = appn.get("financial") or {}
    occ = appn.get("occupation") or {}

    def avail(key: str) -> bool:
        v = sig.get(key)
        return isinstance(v, dict) and v.get("status") != "unavailable" and bool(
            {k: x for k, x in v.items() if k not in ("status",) and x not in (None, "", [], {})})

    if group_key == "identity_kyc":
        return avail("pan_verify") or avail("aadhaar_ekyc") or avail("liveness_facematch") \
            or avail("ckyc")
    if group_key == "financial":
        # Assessed once income is declared (R-007/R-008 income-vs-SI) or a statement lands.
        # A chosen SI alone is NOT a financial assessment (no income to weigh it against).
        return bool(fin.get("declared_annual_income")) \
            or avail("account_aggregator") or avail("credit_bureau")
    if group_key == "occupation_employer":
        return bool(occ.get("declared_type")) or avail("mca_director") or avail("epfo") \
            or avail("gst") or avail("occupation_hazard")
    if group_key == "medical":
        return bool(hd) or avail("abha_health_records") or avail("pre_policy_medical")
    if group_key == "lifestyle":
        return bool(hd) or avail("facial_bmi_smoking") or avail("account_aggregator")
    if group_key == "fraud_check":
        return avail("email_intel") or avail("liveness_facematch") or avail("ml_scores")
    if group_key == "litigation_fir":
        return isinstance(sig.get("litigation_fir"), dict) and bool(sig["litigation_fir"])
    if group_key == "velocity_graph":
        return avail("velocity_graph")
    if group_key == "geography":
        return avail("geography")
    if group_key == "insurance_portfolio":
        return avail("iib")
    if group_key == "contactability":
        return avail("email_intel") or avail("mobile_intel")
    return False


@router.get("/app/{app_id}")
def get_app(app_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    """Read-only snapshot for the React console: the fetched applicant + KYC signals so
    the center panel can render pre-filled Step-1 data. No mutation, session-gated."""
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    bundle = dict(app.bundle)
    application = bundle.get("application", {}) or {}
    signals = bundle.get("signals", {}) or {}
    return {
        "success": True,
        "application_number": app.application_number,
        "current_step": app.current_step,
        "status": app.status,
        "applicant": application.get("applicant", {}) or {},
        "financial": application.get("financial", {}) or {},   # Step 3 pre-fill on revisit
        "signals": {
            "pan_verify": signals.get("pan_verify", {}) or {},
            "mobile_intel": signals.get("mobile_intel", {}) or {},
            "epfo": signals.get("epfo", {}) or {},
            "gst": signals.get("gst", {}) or {},
            "litigation_fir": signals.get("litigation_fir", {}) or {},
            "mca_director": signals.get("mca_director", {}) or {},
            "email_intel": signals.get("email_intel", {}) or {},
            "aadhaar_ekyc": signals.get("aadhaar_ekyc", {}) or {},
            "account_aggregator": signals.get("account_aggregator", {}) or {},  # Step 3 statement state
            "rppg_scan": signals.get("rppg_scan", {}) or {},                    # Step 4 face-scan vitals
            "liveness_facematch": signals.get("liveness_facematch", {}) or {},  # Step 4 liveness/deepfake
            "abha_health_records": signals.get("abha_health_records", {}) or {},# Step 4 ABHA fetch state
        },
    }


@router.get("/rail/{app_id}")
def rail(app_id: int, request: Request, step: int = 5,
         db: Session = Depends(get_session)) -> dict:
    """The live agent-signal rail: run rules+scoring on the partial bundle, return
    the current step's per-group chips (sub-score + severity + reason). Polled by the
    console JS with ?step=N. The composite score always spans ALL groups; only the
    chip DISPLAY is scoped to the step (JOURNEY_PLAN §2). Unknown step -> full read."""
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    from underwriting import config as C
    from underwriting.rules import run_bre
    from underwriting.scoring import safety_score
    from underwriting.schemas import ProposalInput

    raw = copy.deepcopy(dict(app.bundle))
    raw.pop("_journey", None)
    try:
        inp = ProposalInput(**raw)
        bre = run_bre(inp)
        ss, rows, _ = safety_score(inp, bre)
    except Exception as e:  # partial bundle not yet valid -> rail simply stays idle
        return {"success": True, "safety_score": None, "band": None, "groups": [], "note": str(e)[:120]}

    show = set(_STEP_GROUPS.get(step, [g[0] for g in _RAIL_GROUPS]))
    by_group = {r.source_group: r for r in rows}
    groups = []
    for key, label in _RAIL_GROUPS:
        if key not in show:
            continue
        r = by_group.get(key)
        if r is None:
            continue
        has_data = _group_has_data(key, raw)
        # severity/reason from the SAME scorer the report uses; unchecked group -> idle
        # so a not-yet-returned source never shows a green "clean" it hasn't earned.
        severity = _LEVEL[C.safety_band(r.risk_sub_score)] if has_data else "idle"
        g = {
            "key": key, "label": label,
            "sub_score": r.risk_sub_score,
            "severity": severity,          # ok | warn | bad | idle
            "why": r.why if has_data else "awaiting source",
        }
        # Financial group carries read-only context sub-items the underwriter cross-checks
        # against declared income (GST turnover is a real fetched fact; vehicle + imputed
        # income are backend-fed, blank/null until their bundle fields land — no theatre).
        if key == "financial":
            g["context"] = _financial_context(raw)
        groups.append(g)
    return {"success": True, "safety_score": ss.value, "band": ss.band, "groups": groups}


def _financial_context(bundle: dict) -> list[dict]:
    """Financial-group rail context: {label, value} rows. value=None renders as '—'
    (awaiting source). GST turnover is populated today; vehicle + imputed income are
    placeholders wired to their intended bundle paths (backend fills them later)."""
    sig = bundle.get("signals", {}) or {}
    gst = sig.get("gst") or {}
    # intended bundle paths for the not-yet-wired signals (blank until backend emits them):
    vehicle = sig.get("vehicle") or {}                 # signals.vehicle.* (RTO / vahan lookup)
    aa = sig.get("account_aggregator") or {}
    return [
        {"label": "GST turnover", "value": gst.get("turnover_slab")},
        {"label": "Vehicle", "value": vehicle.get("model") or vehicle.get("registration")},
        {"label": "Imputed income", "value": _inr(vehicle.get("imputed_annual_income")
                                                or aa.get("imputed_annual_income"))},
    ]


def _inr(n) -> Optional[str]:
    """₹ format an int amount for a rail value, or None (-> '—') when absent."""
    return ("₹{:,}".format(int(n))) if isinstance(n, (int, float)) and n else None


# ---------------------------------------------------------------------------
# Step 5 — THE DECISION: assemble bundle -> POST /underwrite (engine) -> persist
# ---------------------------------------------------------------------------
@router.post("/decide/{app_id}")
def decide(app_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    """Run the assembled bundle through the real engine and persist the decision.

    Reuses the engine's shared core `run_and_report` (same path the /underwrite
    endpoint uses). Non-grey-zone cases never touch the LLM; grey-zone cases do.
    """
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    from underwriting.api import _status, run_and_report
    from underwriting.schemas import ProposalInput

    # Strip our journey-internal scratch keys before validating against the engine schema.
    raw = copy.deepcopy(dict(app.bundle))
    raw.pop("_journey", None)

    t0 = time.time()
    try:
        inp = ProposalInput(**raw)
        report = run_and_report(inp)
        report_dict = report.model_dump()
        status = _status(report)
        verdict = (report.decision.verdict if report.decision else "REFER")
        waiting_on = report.decision.next_step if (status == "pending" and report.decision) else None
        safety = report.safety_score.value if report.safety_score else None
    except Exception as e:
        track_api_call(db, provider="underwrite", endpoint="run_and_report", mode="real",
                       application_id=app.id, ok=False, latency_ms=int((time.time()-t0)*1000),
                       error=str(e)[:300])
        track_event(db, event_type="decision_error", application_id=app.id,
                    detail={"error": str(e)[:300]})
        return {"success": False, "message": f"Underwriting failed: {str(e)[:200]}"}

    # Persist the decision + update the application status.
    from .models import DecisionRecord
    db.add(DecisionRecord(
        application_id=app.id, verdict=verdict, status=status,
        waiting_on=waiting_on, safety_score=safety, report=report_dict,
    ))
    app.status = "decided"
    db.add(app)
    track_api_call(db, provider="underwrite", endpoint="run_and_report", mode="real",
                   application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                   response_summary={"verdict": verdict, "safety_score": safety,
                                     "cost_usd": (report_dict.get("run_metadata") or {}).get("total_cost_usd")})
    track_event(db, event_type="decision_rendered", application_id=app.id,
                detail={"verdict": verdict, "status": status, "safety_score": safety})
    return {"success": True, "verdict": verdict, "status": status, "waiting_on": waiting_on}


def _mock_report() -> dict:
    """The canned Rohit ISSUE_WITH_LOADING report (journey/mock_report.json) — a full,
    rich ReportOutput for demoing the Step-5 render without running the live engine."""
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "mock_report.json").read_text(encoding="utf-8"))


@router.get("/decision/{app_id}")
def get_decision(app_id: int, request: Request, mock: int = 0,
                 db: Session = Depends(get_session)) -> dict:
    """Read-only fetch of the latest persisted decision + full report for the React
    center to render (the /decide POST only returns the verdict envelope; the full
    ReportOutput dict lives on DecisionRecord.report). Session-gated, no mutation.
    Returns {success:false, pending_decision:true} when the engine hasn't run yet.

    ?mock=1 -> serve the canned rich demo report (no session, no DB) so the full Step-5
    render can be shown/QA'd against a complete payload."""
    if mock:
        r = _mock_report()
        return {"success": True, "verdict": r["decision"]["verdict"], "status": "complete",
                "waiting_on": None, "safety_score": r["safety_score"]["value"], "report": r}
    app = _require_app(request, app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    from .models import DecisionRecord
    rec = db.exec(
        select(DecisionRecord).where(DecisionRecord.application_id == app_id)
        .order_by(DecisionRecord.created_at.desc())
    ).first()
    if rec is None:
        return {"success": False, "pending_decision": True}
    return {
        "success": True,
        "verdict": rec.verdict,
        "status": rec.status,
        "waiting_on": rec.waiting_on,
        "safety_score": rec.safety_score,
        "report": rec.report,
    }


# ---------------------------------------------------------------------------
# Step 6 — Nominee(s) (+ appointee if a nominee's DOB < 18, Insurance Act §39).
# Display-capture. Multiple nominees with a share split; the FIRST nominee is also
# stored as application.nominee (the single dict the engine reads — schemas.nominee,
# rules.py R-M2 relationship) so the underwriting contract is unchanged; the full list
# lands under application.nominees[] (bundle is extra="allow").
# ---------------------------------------------------------------------------
class NomineeItem(BaseModel):
    name: str
    dob: Optional[str] = None
    relationship: Optional[str] = None
    share_pct: Optional[int] = 100
    address: Optional[str] = None
    appointee_name: Optional[str] = None
    appointee_dob: Optional[str] = None
    appointee_relationship: Optional[str] = None


class NomineeRequest(BaseModel):
    app_id: int
    # Single-nominee body (name at top level) still accepted for back-compat; the console
    # sends nominees[] for the multi-nominee split. One of the two must carry a name.
    name: Optional[str] = None
    dob: Optional[str] = None
    relationship: Optional[str] = None
    share_pct: Optional[int] = 100
    address: Optional[str] = None
    appointee_name: Optional[str] = None
    appointee_dob: Optional[str] = None
    appointee_relationship: Optional[str] = None
    nominees: Optional[list[NomineeItem]] = None


def _nominee_dict(item: "NomineeItem | NomineeRequest") -> tuple[dict, bool]:
    """Build one stored nominee dict; returns (dict, minor). Adds an appointee sub-block
    (and requires its name) when the nominee's DOB makes them a minor (Insurance Act §39)."""
    minor = False
    if item.dob:
        age = _age_from_dob(item.dob)
        minor = age is not None and age < 18
    nominee = {
        "name": item.name, "dob": item.dob, "relationship": item.relationship,
        "share_pct": item.share_pct, "address": item.address,
    }
    if minor:
        nominee["appointee"] = {
            "name": item.appointee_name, "dob": item.appointee_dob,
            "relationship": item.appointee_relationship,
        }
    return nominee, minor


@router.post("/nominee")
def set_nominee(req: NomineeRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    # Normalise to a list: prefer nominees[], fall back to the single top-level body.
    items: list = list(req.nominees) if req.nominees else ([req] if req.name else [])
    items = [it for it in items if (it.name or "").strip()]
    if not items:
        return {"success": False, "message": "At least one nominee name is required."}

    shares = sum(int(it.share_pct or 0) for it in items)
    if len(items) > 1 and shares != 100:
        return {"success": False, "message": f"Nominee shares must total 100% (currently {shares}%)."}

    built = [_nominee_dict(it) for it in items]
    for (nominee, minor), it in zip(built, items):
        if minor and not it.appointee_name:
            return {"success": False,
                    "message": f"{it.name} is a minor — an appointee is required (Insurance Act §39)."}
    any_minor = any(m for _, m in built)

    def add(bundle):
        appn = bundle.setdefault("application", {})
        appn["nominees"] = [n for n, _ in built]
        appn["nominee"] = built[0][0]  # primary — the dict the engine reads
    _mutate_bundle(app, add)
    db.add(app)
    track_event(db, event_type="nominee_captured", application_id=app.id, actor="customer",
                detail={"count": len(items), "relationship": items[0].relationship, "minor": any_minor})
    return {"success": True, "minor": any_minor, "count": len(items)}


# ---------------------------------------------------------------------------
# Step 7 — Payment (display-only). §64VB: cover starts on payment success.
# ---------------------------------------------------------------------------
class PaymentRequest(BaseModel):
    app_id: int
    payment_mode: str = "upi"


@router.post("/payment")
def make_payment(req: PaymentRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Display-only mocked payment success. §64VB — risk cover starts only on premium
    payment success; here we simulate success and mark the policy issued + free-look open.
    NO real gateway (per the locked decision)."""
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}

    policy_no = "POL-" + uuid.uuid4().hex[:8].upper()

    def add(bundle):
        product = bundle.setdefault("application", {}).setdefault("product", {})
        product["payment_mode"] = req.payment_mode
        bundle.setdefault("_journey", {})["policy_number"] = policy_no
    _mutate_bundle(app, add)
    app.status = "issued"
    db.add(app)
    track_event(db, event_type="payment_success", application_id=app.id, actor="customer",
                detail={"mode": req.payment_mode, "policy_number": policy_no,
                        "note": "mock — no real gateway (§64VB cover on payment success)"})
    track_event(db, event_type="policy_issued", application_id=app.id,
                detail={"policy_number": policy_no})
    return {"success": True, "policy_number": policy_no}


# ---------------------------------------------------------------------------
# generic consent recorder (inline consents at point of use)
# ---------------------------------------------------------------------------
class ConsentRequest(BaseModel):
    app_id: int
    type: str
    framework: str = ""


@router.post("/consent")
def record_consent(req: ConsentRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    _record_consent(db, app, req.type, req.framework)
    db.add(app)
    track_event(db, event_type="consent_recorded", application_id=app.id,
                detail={"type": req.type})
    return {"success": True}
