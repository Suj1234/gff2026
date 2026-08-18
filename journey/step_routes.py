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
from fastapi.responses import HTMLResponse, RedirectResponse
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


def _popup_close(status: str) -> HTMLResponse:
    """The DigiLocker flow runs in a POPUP window (the console stays visible behind it).
    On completion the popup can't redirect the app — it messages the opener console to
    refresh its snapshot, then closes itself. `status` ∈ ok|error|mock. If there's no
    opener (flow opened in a full tab as a fallback), fall back to the old redirect."""
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8>
<title>DigiLocker</title>
<body style="font:14px system-ui;display:grid;place-items:center;height:100vh;margin:0;color:#555">
<p>Aadhaar e-KYC {'complete' if status == 'ok' else status}. You can close this window.</p>
<script>
  try {{
    if (window.opener && !window.opener.closed) {{
      window.opener.postMessage({{type:"digilocker",status:"{status}"}}, "*");
      window.close();
    }} else {{
      location.replace("/?step=console&aadhaar={status}");  // opened in a full tab, not a popup
    }}
  }} catch (e) {{ location.replace("/?step=console&aadhaar={status}"); }}
</script>""")


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


class PanPrefillRequest(BaseModel):
    app_id: int
    pan: str


@router.post("/prefill-by-pan")
def prefill_by_pan(req: PanPrefillRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Flow B (vendor_apis §2): mobile returned no PAN -> user typed their PAN -> fetch the
    SAME full profile from it and merge (identity/pan/employment/litigation/gst)."""
    from . import mobile_pan
    from .auth_routes import _merge_profile_into_bundle
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    pan = (req.pan or "").strip().upper()
    if len(pan) != 10:
        return {"success": False, "message": "Enter a valid 10-character PAN."}
    if not mobile_pan.configured():
        return {"success": False, "message": "prefill unavailable"}
    t0 = time.time()
    try:
        data = mobile_pan.fetch_by_pan(pan, insurer_slug=app.insurer_slug)
        _merge_profile_into_bundle(app, data)
        db.add(app)
        track_api_call(db, provider="mobile_pan", endpoint="pan-to-profile", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                       response_summary={"keys": sorted((data or {}).keys())[:12]})
        track_event(db, event_type="profile_prefilled", application_id=app.id, detail={"via": "pan"})
        return {"success": True, "pan": (data.get("pan") or pan)}
    except Exception as e:
        track_api_call(db, provider="mobile_pan", endpoint="pan-to-profile", mode="real",
                       application_id=app.id, ok=False, latency_ms=int((time.time()-t0)*1000),
                       error=str(e)[:200])
        return {"success": False, "message": "vendor unavailable — try again or enter details manually"}


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

    # Real email-intel fetch (vendor_apis §3, same Perfios gateway) -> sources/email
    # adapter (validity + inverted fraud/spam/disposable) -> signals.email_intel, which
    # the fraud sub-score + grey-zone judge already consume. Falls back to the plain
    # typed-email fact if EMAIL_* creds are absent or the vendor is down (never blocks, §11).
    from . import email as email_client
    intel = {"status": "available", "email": req.email}  # fallback fact
    mode = "mock"
    if email_client.configured():
        t0 = time.time()
        try:
            from underwriting.sources import adapt
            raw = email_client.fetch(req.email, insurer_slug=app.insurer_slug)
            intel = adapt("email_intel", raw)
            intel.setdefault("email", req.email)
            mode = "real"
            track_api_call(db, provider="email", endpoint="email-intel", mode="real",
                           application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                           request_summary={"email": req.email},
                           response_summary={"fraud_risk_score": intel.get("fraud_risk_score"),
                                             "is_disposable": intel.get("is_disposable")})
        except Exception as e:  # vendor error must not break Step 1 (§11)
            track_api_call(db, provider="email", endpoint="email-intel", mode="real",
                           application_id=app.id, ok=False, latency_ms=int((time.time()-t0)*1000),
                           error=str(e)[:200])
    else:
        track_api_call(db, provider="email", endpoint="(unconfigured — email captured)", mode="mock",
                       application_id=app.id, ok=True, request_summary={"email": req.email})

    def add(bundle):
        bundle.setdefault("signals", {})["email_intel"] = intel
    _mutate_bundle(app, add)
    track_event(db, event_type="email_captured", application_id=app.id, actor="customer",
                detail={"email": req.email, "mode": mode})
    db.add(app)
    return {"success": True}


# ---------------------------------------------------------------------------
# Step 1 — DigiLocker Aadhaar e-KYC (link -> user grants -> callback -> download+merge)
# ---------------------------------------------------------------------------
@router.get("/digilocker/start/{app_id}")
def digilocker_start(app_id: int, request: Request, db: Session = Depends(get_session)):
    """Kick the DigiLocker flow in a POPUP: call /link, stash accessRequestId, redirect
    the popup to the DigiLocker consent URL. On mock/error/unauth we render the popup-close
    page (message the console + close) instead of redirecting the popup to the app."""
    app = _require_app(request, app_id, db)
    if app is None:
        return _popup_close("error")

    _record_consent(db, app, "aadhaar_ekyc", "Aadhaar_Act")

    if not digilocker.configured():
        # Mock fallback: merge a keyed Aadhaar record straight away, then close the popup.
        _merge_mock_aadhaar(db, app)
        db.add(app)
        return _popup_close("mock")

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
    return _popup_close("error")


@callback_router.get("/digilocker/callback")
def digilocker_callback(request: Request, db: Session = Depends(get_session)):
    """DigiLocker returns the user here after they grant consent. Complete the pull:
    documents() -> download() -> merge parsed Aadhaar+PAN into the bundle."""
    # The session cookie identifies the application (DigiLocker preserves our cookie via
    # the redirect back to our host).
    sess = auth.resolve_session(db, request.cookies.get(auth.COOKIE_NAME))
    if sess is None:
        return _popup_close("error")
    app = db.get(Application, sess.application_id)
    if app is None:
        return _popup_close("error")

    access_id = (app.bundle.get("_journey") or {}).get("digilocker_access_id")
    if not access_id:
        return _popup_close("error")

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
        return _popup_close("ok")
    except Exception as e:
        track_api_call(db, provider="digilocker", endpoint="/documents+/download", mode="real",
                       application_id=app.id, ok=False, error=str(e)[:200])
        track_event(db, event_type="digilocker_error", application_id=app.id,
                    detail={"error": str(e)[:200]})
        return _popup_close("error")


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
        # Live edits must be able to RETRACT, not just add — so lists/text overwrite even when
        # emptied (answering No clears the conditions/family it had). Booleans + numbers still
        # only write when provided (None = "field not in this partial", leave as-is).
        hd["conditions"] = req.conditions
        hd["family_history"] = req.family_history
        for k in ("ongoing_medication", "past_medical_history"):
            hd[k] = getattr(req, k)   # may be None → cleared
        for k in ("height_cm", "weight_kg", "tobacco", "alcohol", "drugs"):
            v = getattr(req, k)
            if v is not None:
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
        # NuralX echoes `client_transaction_ID` back in the webhook — key the session on it
        # so /face-scan/callback can correlate the vitals to THIS application. It IS the
        # session_token passed to initiate_scan (nuralx.py sends session_token as the id).
        ctid = uuid.uuid4().hex
        # Point the webhook at the JOURNEY callback (writes into app.bundle), not the
        # standalone in-memory /nuralx/callback. Same shared ?key= secret.
        creds.callback_url = f"{os.getenv('PUBLIC_API_URL', '').rstrip('/')}" \
                             f"/api/journey/face-scan/callback?key={os.getenv('NURALX_CALLBACK_SECRET', '')}"
        applicant = app.bundle.get("application", {}).get("applicant", {})
        patient = nuralx.Patient(name=applicant.get("name") or "Applicant")
        resp = nuralx.initiate_scan(creds, session_token=ctid, patient=patient)
        # persist a FaceScanSession so the webhook can correlate + resolve
        from .models import FaceScanSession
        db.add(FaceScanSession(
            token=uuid.uuid4().hex, application_id=app.id,
            client_transaction_id=ctid,
            status="IN_PROGRESS", scan_access_url=resp.scan_url,
        ))
        track_api_call(db, provider="nuralx", endpoint="patient-data", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                       response_summary={"has_scan_url": bool(resp.scan_url)})
        track_event(db, event_type="face_scan_initiated", application_id=app.id)
        return {"success": True, "mode": "real", "scan_url": resp.scan_url}
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


@callback_router.post("/api/journey/face-scan/callback")
async def face_scan_callback(request: Request, key: str = "", db: Session = Depends(get_session)):
    """PUBLIC webhook — NuralX POSTs the completed scan here. No session cookie (server-to-
    server); auth is the shared ?key= secret. Correlate by client_transaction_ID -> the
    FaceScanSession -> merge the adapted vitals/liveness into that application's bundle so
    the UI's snapshot poll sees rppg_scan.status == 'available'. Always ACK 200 (§11)."""
    secret = os.getenv("NURALX_CALLBACK_SECRET", "")
    if not secret or key != secret:
        return {"received": True}  # ACK but ignore a bad/missing secret

    import json
    from underwriting.sources import nuralx as nuralx_adapter
    from .models import FaceScanSession

    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"received": True}

    ctid = body.get("client_transaction_ID") or body.get("client_transaction_id")
    fss = db.exec(
        select(FaceScanSession).where(FaceScanSession.client_transaction_id == ctid)
    ).first() if ctid else None
    if fss is None:
        return {"received": True}  # unknown/expired session — ACK, nothing to write

    app = db.get(Application, fss.application_id)
    if app is None:
        return {"received": True}

    is_failure = body.get("status") in ("timeout", "error") or not body.get("results")
    signals = nuralx_adapter.to_signals(body)  # {rppg_scan, liveness_facematch, facial_bmi_smoking}

    def add(bundle):
        sig = bundle.setdefault("signals", {})
        for k, v in signals.items():
            sig[k] = v
    _mutate_bundle(app, add)
    fss.status = "TIMEOUT" if body.get("status") == "timeout" else ("ERROR" if is_failure else "COMPLETED")
    fss.result = body if isinstance(body, dict) else {}
    db.add(fss)
    db.add(app)
    track_api_call(db, provider="nuralx", endpoint="/callback", mode="real",
                   application_id=app.id, ok=not is_failure,
                   response_summary={"status": fss.status})
    track_event(db, event_type="face_scan_completed", application_id=app.id,
                detail={"status": fss.status, "mock": False})
    return {"received": True}


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
    # Step 2 (Product & Cover) collects only the cover CHOICE, which produces no scorer
    # signal of its own — the SI-ceiling reaction is a synthetic "Cover" chip built from
    # R-006 (see _cover_chip, prepended in rail()). The one thing that DID just return is
    # the email intel fetched on the Step-1→2 Continue, so its two groups (fraud_check +
    # contactability) belong here. Financial/insurance_portfolio removed: financial's inputs
    # (declared income / statement) arrive in Step 3, and no IIB source is fetched in the
    # journey at all — both were structurally guaranteed "awaiting source" here.
    2: ["fraud_check", "contactability"],
    3: ["financial"],
    # velocity_graph + geography removed: no journey step fetches those signals, so both
    # are structurally guaranteed "awaiting source" here (same reason financial/IIB were
    # dropped from Step 2). The composite score still spans all groups — only the chips are scoped.
    4: ["medical", "lifestyle", "fraud_check"],
    5: [g[0] for g in _RAIL_GROUPS],   # decision — everything the agent weighed
    6: [g[0] for g in _RAIL_GROUPS],
    7: [g[0] for g in _RAIL_GROUPS],
}

# Step 4 has THREE sub-steps; the rail scopes chips to the ACTIVE sub-step the same way
# _STEP_GROUPS scopes chips to a step. Mapped to what each sub-step collects + the scorer
# reads (see the per-sub-step analysis): Health decl → Medical (conditions / prior-decline);
# Vitals → Lifestyle (tobacco) + Medical (BMI + family history both land in _s_medical);
# Face/ABHA → Face&deepfake (liveness/R-003) + Medical (ABHA corroboration). Medical spans
# all three because it accumulates. An idle chip still hides (rail() drops idle step-4 chips).
_STEP4_SUB_GROUPS = {
    0: ["medical"],                        # Health declaration
    1: ["lifestyle", "medical"],           # Vitals & lifestyle
    2: ["fraud_check", "medical"],         # Face scan & ABHA
}

# 0-100 sub-score -> risk LEVEL, the SAME map report._level uses (config.safety_band
# cutoffs). Inlined so the rail endpoint doesn't import report.py (pulls in dspy/LLM).
_LEVEL = {"Low Risk": "ok", "Moderate Risk": "warn", "High Risk": "bad"}


def _email_why(bundle: dict, kind: str) -> Optional[str]:
    """Email-specific reason text for the Step-2 fraud/contactability chips, built from the
    real email_intel facts so the chip reads as an EMAIL check (not a generic verdict).
    kind ∈ 'fraud' | 'contact'. None if email intel isn't present (chip keeps its default)."""
    em = (bundle.get("signals", {}) or {}).get("email_intel") or {}
    if not em or em.get("status") == "unavailable":
        return None
    addr = em.get("email")
    if not addr:
        return None
    bad = []
    if em.get("is_disposable") is True:
        bad.append("disposable domain")
    if em.get("is_spam") is True:
        bad.append("on spam record")
    efs = em.get("fraud_risk_score")
    if kind == "fraud":
        if bad:
            return f"{addr}: " + ", ".join(bad)
        if isinstance(efs, (int, float)) and efs >= 0.3:
            return f"{addr}: flagged as higher-risk"
        return f"{addr}: genuine domain, no risk flags"
    # contactability
    if em.get("name_match") is False:
        return f"{addr}: does not match the applicant's name"
    return f"{addr}: valid and matches the applicant"


def _inr_cover(n) -> str:
    """₹ short-form for a sum insured (₹2 Cr / ₹50 L), used in the Cover chip reason."""
    n = int(n or 0)
    if n >= 10_000_000:
        cr = n / 10_000_000
        return f"₹{cr:.0f} Cr" if cr == int(cr) else f"₹{cr:.1f} Cr"
    if n >= 100_000:
        return f"₹{n // 100_000} L"
    return f"₹{n:,}"


def _cover_chip(sum_insured: int) -> Optional[dict]:
    """Step-2 synthetic 'Cover' chip: the agent's read on the CHOSEN sum insured.

    Not a scorer group — the cover choice produces no Safety-Score sub-score. The one
    underwriting reaction it drives is R-006 (SI above the STP auto-issue ceiling → manual
    referral). Severity comes from calling the REAL R-006 rule on the SI, so it can never
    contradict the Step-5 verdict — and the ceiling lives ONLY in config.STP_SI_CEILING
    (via the rule), no threshold duplicated in the UI. Calling the rule directly (not
    reading bre.hard_gate) lets the chip react to the live-selected SI before it's saved.
    Returns None if no SI is chosen yet (chip simply doesn't render)."""
    from underwriting import config as C
    from underwriting.rules import r006_si_ceiling
    from underwriting.schemas import RuleOutcome
    if not sum_insured:
        return None
    ceiling = C.STP_SI_CEILING
    referred = r006_si_ceiling(int(sum_insured)).outcome == RuleOutcome.HARD_REFER
    if referred:
        return {
            "key": "cover", "label": "Cover", "gate": True,
            "sub_score": 0, "severity": "warn",   # amber: goes to a human, not a decline
            "why": f"{_inr_cover(sum_insured)} is above the {_inr_cover(ceiling)} "
                   f"auto-issue limit — needs a manual underwriter",
        }
    return {
        "key": "cover", "label": "Cover", "gate": True,
        "sub_score": 100, "severity": "ok",
        "why": f"{_inr_cover(sum_insured)} can be auto-issued · income check in the next step",
    }


def _persona(bundle: dict) -> str:
    """Classify the applicant from the prefill facts so the rail can adapt (an underwriter
    looks at EPFO for a salaried employee, GST/business for an owner). Returns
    'salaried' | 'self_employed' | 'both' | 'unknown'. Salaried = EPFO present; self-employed
    = GST present. Both = both. Drives which Step-1 chips show and whether GST is prominent."""
    sig = bundle.get("signals", {}) or {}
    epfo = sig.get("epfo") or {}
    gst = sig.get("gst") or {}
    has_epfo = epfo.get("status") == "available" and (epfo.get("employer") or epfo.get("uan"))
    has_gst = gst.get("status") == "available" and (gst.get("gstin") or gst.get("gstin_count"))
    if has_epfo and has_gst:
        return "both"
    if has_gst:
        return "self_employed"
    if has_epfo:
        return "salaried"
    return "unknown"


# Which Step-1 groups show per persona (an underwriter's view differs by applicant type).
# The self-employed / both personas surface occupation prominently (it carries GST/business);
# salaried keeps the lean identity-first view. Composite score still spans ALL groups.
_STEP1_GROUPS_BY_PERSONA = {
    "salaried":      ["identity_kyc", "contactability", "fraud_check", "litigation_fir", "occupation_employer"],
    "self_employed": ["identity_kyc", "occupation_employer", "litigation_fir", "fraud_check", "contactability"],
    "both":          ["identity_kyc", "occupation_employer", "contactability", "fraud_check", "litigation_fir"],
    "unknown":       ["identity_kyc", "contactability", "fraud_check", "litigation_fir", "occupation_employer"],
}


def _group_has_data(group_key: str, bundle: dict, step: int = 5) -> bool:
    """True once ANY input the group's sub-scorer (scoring.py) reads is present in the
    bundle — signal OR application-declared fact. Mirrors each `_s_*` scorer's inputs so
    an assessed group never shows 'idle' and an untouched one never shows a green 'clean'.
    Keep this in lockstep with scoring.py's per-group inputs.

    `step` scopes the STEP-4 chips to evidence produced ON step 4 (the health screen):
    Lifestyle→vitals answered here (not the Step-3 bank statement), Face→a scan run here
    (not a prior liveness read). So landing on Health with all 'No' shows NO chips, and
    each lights only as its own sub-step's evidence lands. The composite score is unaffected
    (it always spans every group via safety_score) — this gates the CHIP display only."""
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
        # Declared facts the scorer now reads deterministically (real-time, no ABHA needed):
        # a declared CONDITION, family history, or a prior insurance decline. Any of them
        # lights the Medical chip on its own screen; else wait for ABHA/labs.
        declared_adverse = bool(hd.get("conditions")) or bool(hd.get("family_history")) \
            or bool(hd.get("past_medical_history"))
        return declared_adverse or avail("abha_health_records") or avail("pre_policy_medical")
    if group_key == "lifestyle":
        # Lifestyle IS assessed by the declaration — but only once tobacco/alcohol is
        # actually answered (an empty hd hasn't answered anything).
        answered = any(hd.get(k) is not None for k in ("tobacco", "alcohol", "drugs"))
        if step == 4:
            # On the health screen the lifestyle chip is the VITALS answer, not the
            # Step-3 bank-statement spend read — don't carry AA over as "assessed here".
            return answered or avail("facial_bmi_smoking")
        return answered or avail("facial_bmi_smoking") or avail("account_aggregator")
    if group_key == "fraud_check":
        if step == 4:
            # Step-4 fraud chip = the FACE SCAN (liveness/deepfake) run here, not the
            # email intel from Step 1 (that already showed on Steps 1/2).
            return avail("liveness_facematch")
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
        "created_at": app.created_at.isoformat() if app.created_at else None,  # UTC; UI -> IST
        "applicant": application.get("applicant", {}) or {},
        "financial": application.get("financial", {}) or {},   # Step 3 pre-fill on revisit
        "product": application.get("product", {}) or {},       # Step 7 amount-due fallback (?start=7 / refresh)
        "health_declaration": application.get("health_declaration", {}) or {},  # Step 4 pre-fill on revisit
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
def rail(app_id: int, request: Request, step: int = 5, si: int = 0, sub: int = 0,
         db: Session = Depends(get_session)) -> dict:
    """The live agent-signal rail: run rules+scoring on the partial bundle, return
    the current step's per-group chips (sub-score + severity + reason). Polled by the
    console JS with ?step=N. The composite score always spans ALL groups; only the
    chip DISPLAY is scoped to the step (JOURNEY_PLAN §2). Unknown step -> full read.

    `si` (Step-2 only): the sum insured currently SELECTED in the product picker, before
    Continue persists it. When present it overrides the bundle's saved SI so the Cover chip
    (R-006) reacts live as the underwriter toggles the cover, without a premature persist.

    `sub` (Step-4 only): the active health sub-step (0 Health · 1 Vitals · 2 Face/ABHA), so
    the chip list is scoped to that sub-step (_STEP4_SUB_GROUPS) — the same scoping Steps 1–3
    get per step, now applied per sub-step. Score gauge stays the full accumulated read."""
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

    # Step 1 adapts to WHO the applicant is (salaried vs self-employed vs both); later
    # steps use the fixed step->group map. The occupation chip is relabeled + carries
    # business/GST context for the self-employed personas.
    persona = _persona(raw)
    if step == 1:
        show = set(_STEP1_GROUPS_BY_PERSONA.get(persona, _STEP1_GROUPS_BY_PERSONA["unknown"]))
    elif step == 4:
        # Scope to the ACTIVE health sub-step, not all of Step 4 (so Vitals/Face chips don't
        # show on the Health screen). Unknown sub → full Step-4 set as a safe fallback.
        show = set(_STEP4_SUB_GROUPS.get(sub, _STEP_GROUPS[4]))
    else:
        show = set(_STEP_GROUPS.get(step, [g[0] for g in _RAIL_GROUPS]))
    by_group = {r.source_group: r for r in rows}
    groups = []
    for key, label in _RAIL_GROUPS:
        if key not in show:
            continue
        r = by_group.get(key)
        if r is None:
            continue
        has_data = _group_has_data(key, raw, step)
        # Step 4 reveals chips AS their evidence lands (no cold placeholders): Lifestyle when
        # tobacco/BMI is answered, Medical + Face/deepfake after the face-scan & ABHA fetch.
        # An idle Step-4 chip is a source not yet triggered on this page -> don't render it.
        if step == 4 and not has_data:
            continue
        # Step-4 fraud IS the face-scan / deepfake check (email fraud already showed on Step 1/2).
        if step == 4 and key == "fraud_check":
            label = "Face & deepfake"
        # For a self-employed / both applicant, the occupation group IS the "Business & GST"
        # view — relabel it so the underwriter reads it as such (an owner has no employer chip).
        if key == "occupation_employer" and persona in ("self_employed", "both"):
            label = "Business & GST"
        # Step 2's fraud/contactability chips are driven ONLY by the email intel that just
        # returned (not the full fraud/contactability read — that accumulates by Step 5).
        # Relabel so the underwriter reads them as the EMAIL check, not a generic verdict.
        if step == 2 and key == "fraud_check":
            label = "Email fraud"
        if step == 2 and key == "contactability":
            label = "Email contactability"
        # severity/reason from the SAME scorer the report uses; unchecked group -> idle
        # so a not-yet-returned source never shows a green "clean" it hasn't earned.
        severity = _LEVEL[C.safety_band(r.risk_sub_score)] if has_data else "idle"
        why = r.why if has_data else "awaiting source"
        # Step 2: replace the generic scorer `why` with EMAIL-specific text (the chip is the
        # email check here, not the accumulated fraud/contactability read).
        if step == 2 and has_data and key in ("fraud_check", "contactability"):
            ew = _email_why(raw, "fraud" if key == "fraud_check" else "contact")
            if ew:
                why = ew
        g = {
            "key": key, "label": label,
            "sub_score": r.risk_sub_score,
            "severity": severity,          # ok | warn | bad | idle
            "why": why,
        }
        # Financial group carries read-only context sub-items the underwriter cross-checks
        # against declared income (GST turnover is a real fetched fact; vehicle + imputed
        # income are backend-fed, blank/null until their bundle fields land — no theatre).
        if key == "financial":
            g["context"] = _financial_context(raw)
        # Occupation for the self-employed shows the business/GST facts as context rows.
        if key == "occupation_employer" and persona in ("self_employed", "both"):
            g["context"] = _business_context(raw)
        groups.append(g)
    # Step 2 (Product & Cover): prepend the synthetic Cover chip — the agent's read on the
    # sum insured the applicant just chose (R-006 SI-ceiling). It leads the rail because it's
    # the one signal THIS step's own input produces. Uses the live-selected `si` override
    # when present (picker not yet saved), else the bundle's saved SI.
    if step == 2:
        product = (raw.get("application", {}) or {}).get("product", {}) or {}
        eff_si = si or product.get("sum_assured") or 0
        cover = _cover_chip(eff_si)
        if cover is not None:
            groups.insert(0, cover)
    # Provisional-score context: how many of ALL source groups have actually returned data
    # (not just the current step's chips). The UI uses this to label an early score
    # "provisional — N of M sources in" so a 100 at Step 1 isn't read as a final verdict.
    assessed_count = sum(1 for k, _ in _RAIL_GROUPS if _group_has_data(k, raw))
    total_count = len(_RAIL_GROUPS)
    return {"success": True, "safety_score": ss.value, "band": ss.band, "groups": groups,
            "persona": persona,
            "assessed_count": assessed_count, "total_count": total_count}


def _business_context(bundle: dict) -> list[dict]:
    """Business & GST rail context for the self-employed: {label, value} rows.
    value=None renders as '—'. All real fetched facts from the mobile/PAN prefill."""
    gst = (bundle.get("signals", {}) or {}).get("gst") or {}
    statuses = gst.get("statuses") or []
    gst_status = ("Cancelled" if gst.get("any_cancelled")
                  else (statuses[0] if statuses else gst.get("status")))
    nature = gst.get("nature_of_business") or []
    reg = gst.get("registration_date")
    return [
        {"label": "GST status", "value": gst_status if gst_status not in (None, "available") else None},
        {"label": "GSTINs", "value": (str(gst.get("gstin_count")) if gst.get("gstin_count") else None)},
        {"label": "Turnover", "value": gst.get("turnover_slab")},
        {"label": "Business since", "value": reg},
        {"label": "Nature", "value": (", ".join(nature) if nature else None)},
    ]


def _financial_context(bundle: dict) -> list[dict]:
    """Financial-group rail context: {label, value} rows. value=None renders as '—'
    (awaiting source). GST turnover + imputed income are real fetched facts; vehicle is a
    not-yet-wired placeholder. The cover/income + declared/imputed rows make the two
    comparisons the scorer already checks (R-006/R-007 multiple, declared-vs-statement gap)
    visible as facts, not just penalty lines."""
    sig = bundle.get("signals", {}) or {}
    appn = bundle.get("application", {}) or {}
    gst = sig.get("gst") or {}
    vehicle = sig.get("vehicle") or {}                 # signals.vehicle.* (RTO / vahan lookup)
    aa = sig.get("account_aggregator") or {}
    imputed = aa.get("imputed_annual_income")
    declared = (appn.get("financial") or {}).get("declared_annual_income")
    si = (appn.get("product") or {}).get("sum_assured")
    income = imputed or declared                       # best income basis for the cover multiple
    # Only real, non-duplicated facts. The comparison rows carry the raw ₹ figures, so no
    # standalone imputed/declared rows. All ₹ values are short words (₹50 L / ₹2.18 Cr).
    balance = aa.get("avg_monthly_balance")
    e2i = aa.get("expense_to_income")            # 0.0 is a real value ("0% obligations"), not absent
    rows = [
        {"label": "GST turnover", "value": gst.get("turnover_slab")},
        {"label": "Assets", "value": vehicle.get("model") or vehicle.get("registration")
                                     or aa.get("physical_assets")},
        {"label": "Avg balance", "value": _inr(balance)},
        {"label": "Obligations", "value": (f"{e2i*100:.0f}% of income"
                                           if isinstance(e2i, (int, float)) else None)},
    ]
    if si and income:
        rows.append({"label": "Cover / income", "value": f"{_inr(si)} → {si/income:.1f}× income"})
    if declared and imputed:
        rows.append({"label": "Declared vs actual",
                     "value": f"{_inr(declared)} vs {_inr(imputed)} · {abs(declared-imputed)/declared*100:.0f}% gap"})
    elif imputed:  # no declared income to compare against yet — show the raw figure
        rows.append({"label": "Imputed income", "value": _inr(imputed)})
    return rows


def _inr(n) -> Optional[str]:
    """₹ amount as short Indian words for a rail value ("₹50 L", "₹2.18 Cr"), or None
    (-> '—') when absent. Reads at a glance; the rail has no room for full digit strings."""
    if not (isinstance(n, (int, float)) and n):
        return None
    n = float(n)
    if n >= 1e7:
        v = n / 1e7
        return f"₹{v:.2f}".rstrip("0").rstrip(".") + " Cr"
    if n >= 1e5:
        v = n / 1e5
        return f"₹{v:.2f}".rstrip("0").rstrip(".") + " L"
    return "₹{:,}".format(int(n))


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


def _issue_policy(db: Session, app: Application, mode: str, note: str) -> str:
    """Mark the app issued + stamp a policy number (§64VB: cover on payment success).
    Shared by the mocked path and the verified-Razorpay path."""
    policy_no = "POL-" + uuid.uuid4().hex[:8].upper()

    def add(bundle):
        product = bundle.setdefault("application", {}).setdefault("product", {})
        product["payment_mode"] = mode
        bundle.setdefault("_journey", {})["policy_number"] = policy_no
    _mutate_bundle(app, add)
    app.status = "issued"
    db.add(app)
    track_event(db, event_type="payment_success", application_id=app.id, actor="customer",
                detail={"mode": mode, "policy_number": policy_no, "note": note})
    track_event(db, event_type="policy_issued", application_id=app.id,
                detail={"policy_number": policy_no})
    return policy_no


@router.post("/payment")
def make_payment(req: PaymentRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Mocked payment success (fallback / demo without a live gateway). §64VB — risk cover
    starts only on premium payment success; here we simulate success and mark issued."""
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    policy_no = _issue_policy(db, app, req.payment_mode, "mock — no real gateway")
    return {"success": True, "policy_number": policy_no}


# ---- Razorpay (real, test-mode): create order -> Checkout.js -> verify signature -------
def _rzp_keys() -> tuple[str, str]:
    """(key_id, key_secret) for the active Razorpay mode (test|live)."""
    if (os.getenv("RAZORPAY_MODE") or "test").lower() == "live":
        return os.getenv("RAZORPAY_LIVE_KEY_ID", ""), os.getenv("RAZORPAY_LIVE_KEY_SECRET", "")
    return os.getenv("RAZORPAY_TEST_KEY_ID", ""), os.getenv("RAZORPAY_TEST_KEY_SECRET", "")


class PaymentOrderRequest(BaseModel):
    app_id: int


@router.post("/payment/order")
def payment_order(req: PaymentOrderRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Create a real Razorpay order for the premium due. Returns {order_id, amount, key_id}
    the browser hands to Checkout.js. Amount is the persisted product.premium, in paise."""
    import httpx
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    key_id, key_secret = _rzp_keys()
    if not (key_id and key_secret):
        return {"success": False, "message": "Payment gateway not configured."}

    premium = (app.bundle.get("application", {}).get("product", {}) or {}).get("premium")
    if not (isinstance(premium, (int, float)) and premium > 0):
        return {"success": False, "message": "No premium on file — complete the product step first."}
    amount_paise = int(round(float(premium) * 100))

    t0 = time.time()
    try:
        r = httpx.post(
            "https://api.razorpay.com/v1/orders",
            auth=(key_id, key_secret),
            json={"amount": amount_paise, "currency": "INR",
                  "receipt": f"app-{app.id}", "notes": {"application_id": str(app.id)}},
            timeout=30,
        )
        r.raise_for_status()
        order = r.json()
        track_api_call(db, provider="razorpay", endpoint="/v1/orders", mode="real",
                       application_id=app.id, ok=True, latency_ms=int((time.time()-t0)*1000),
                       response_summary={"order_id": order.get("id"), "amount": amount_paise})
        return {"success": True, "order_id": order["id"], "amount": amount_paise,
                "currency": "INR", "key_id": key_id}
    except Exception as e:
        track_api_call(db, provider="razorpay", endpoint="/v1/orders", mode="real",
                       application_id=app.id, ok=False, latency_ms=int((time.time()-t0)*1000),
                       error=str(e)[:200])
        return {"success": False, "message": "Could not start payment — try again."}


class PaymentVerifyRequest(BaseModel):
    app_id: int
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/payment/verify")
def payment_verify(req: PaymentVerifyRequest, request: Request, db: Session = Depends(get_session)) -> dict:
    """Verify the Checkout.js success payload (HMAC-SHA256 of order_id|payment_id with the
    key secret) BEFORE issuing. A forged/tampered signature is rejected — never trust the
    client's word that payment succeeded."""
    import hashlib
    import hmac
    app = _require_app(request, req.app_id, db)
    if app is None:
        return {"success": False, "message": "unauthorized"}
    _, key_secret = _rzp_keys()
    expected = hmac.new(
        key_secret.encode(), f"{req.razorpay_order_id}|{req.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, req.razorpay_signature):
        track_api_call(db, provider="razorpay", endpoint="verify", mode="real",
                       application_id=app.id, ok=False, error="signature mismatch")
        track_event(db, event_type="payment_verify_failed", application_id=app.id,
                    detail={"payment_id": req.razorpay_payment_id})
        return {"success": False, "message": "Payment verification failed."}

    policy_no = _issue_policy(db, app, "razorpay", f"razorpay — {req.razorpay_payment_id}")
    track_api_call(db, provider="razorpay", endpoint="verify", mode="real",
                   application_id=app.id, ok=True,
                   response_summary={"payment_id": req.razorpay_payment_id})
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
