"""auth_routes.py — the landing gate: send/verify OTP -> customer + application + session.

Contract mirrors the pasted Next.js routes (request/response shapes, otp_ref_id,
next_step) but stateful in THIS repo's DB, not the Next.js persistence model.

Flow:
  POST /api/auth/send-otp    {mobile, insurer_slug, purpose}
     -> generate+hash+store OTP; try MSG91; return otp_ref_id (+ debug_otp iff debug).
  POST /api/auth/verify-otp  {mobile, otp, otp_ref_id, insurer_slug, ...}
     -> verify; upsert CustomerUser; create Application (status=otp_verified, step 1);
        set signed httpOnly session cookie; kick the Mobile->PAN prefill into the bundle.

Every send/verify + the profile fetch is written to event_log + api_call_log.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from . import auth, mobile_pan, msg91, otp
from .db import get_session, track_api_call, track_event
from .models import Application, CustomerUser
from .models import Session as SessionRow  # noqa: F401 (create via auth.create_session)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
_OTP_WINDOW_SECONDS = 3600
_OTP_MAX_PER_WINDOW = 50  # per mobile/hour (spec's generous demo limit)


def _debug_enabled() -> bool:
    return os.getenv("UW_DEBUG_OTP", "") == "1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# send-otp
# ---------------------------------------------------------------------------
class SendOtpRequest(BaseModel):
    mobile: str
    insurer_slug: str = "acme"
    purpose: str = "mobile_verification"
    application_id: Optional[str] = None


@router.post("/send-otp")
def send_otp(req: SendOtpRequest, db: Session = Depends(get_session)) -> dict:
    if not _MOBILE_RE.match(req.mobile):
        return {"success": False, "message": "Invalid mobile number"}

    # Rate limit: count recent OTPs for this mobile.
    from .models import OtpLog
    since = _now() - timedelta(seconds=_OTP_WINDOW_SECONDS)
    recent = db.exec(
        select(OtpLog).where(OtpLog.target == req.mobile).where(OtpLog.created_at >= since)
    ).all()
    if len(recent) >= _OTP_MAX_PER_WINDOW:
        track_event(db, event_type="otp_rate_limited", actor="customer",
                    detail={"mobile": req.mobile})
        return {"success": False, "message": "Too many OTP requests. Try later.", "rate_limited": True}

    plaintext, row = otp.create_otp(db, target=req.mobile, channel="sms", purpose=req.purpose)

    # MOBILE_PAN_MOCK_MODE=1 (journey testing/CI) must skip the REAL SMS send too, not just
    # the downstream Mobile->PAN prefill — otherwise "mock mode" still texts the applicant's
    # phone every time (found 2026-08-21: this exact gap sent 3 real OTPs during testing).
    # OTP_FIXED_CODE (demo env) skips the SMS the same way — the applicant already knows
    # the fixed code, so there's nothing useful to deliver.
    otp_mocked = mobile_pan.mock_mode_enabled() or bool(os.getenv("OTP_FIXED_CODE", "").strip())
    t0 = time.time()
    if otp_mocked:
        result = msg91.SendResult(sent=False, error="OTP mocked — real SMS skipped")
    else:
        result = msg91.send_sms_otp(req.mobile, plaintext)
    latency_ms = int((time.time() - t0) * 1000)
    track_api_call(
        db, provider="msg91", endpoint="/api/v5/flow",
        mode="mock" if otp_mocked else ("real" if msg91.creds_present() else "mock"),
        request_summary={"mobile": req.mobile, "purpose": req.purpose},
        response_summary={"sent": result.sent},
        ok=result.sent, http_status=result.http_status, latency_ms=latency_ms,
        error=result.error,
    )
    track_event(db, event_type="otp_sent", actor="customer",
                detail={"otp_ref_id": row.otp_ref_id, "channel": "sms",
                        "delivered": result.sent})

    out = {
        "success": True,
        "message": "OTP sent" if result.sent else "OTP generated (SMS not delivered)",
        "otp_ref_id": row.otp_ref_id,
        "expires_in_seconds": 600,
    }
    # Leak the OTP ONLY when explicitly in debug mode (UW_DEBUG_OTP=1) — never in prod.
    # In debug mode we surface it REGARDLESS of send status: MSG91 can "accept" a send
    # (200) yet deliver a blank/garbled SMS, which would otherwise strand the tester.
    if _debug_enabled():
        out["debug_otp"] = plaintext
    return out


# ---------------------------------------------------------------------------
# verify-otp
# ---------------------------------------------------------------------------
class VerifyOtpRequest(BaseModel):
    mobile: str
    otp: str
    otp_ref_id: str
    insurer_slug: str = "acme"
    initial_sum_insured: Optional[int] = None
    initial_members: Optional[int] = None
    initial_plan_type: Optional[str] = None


def _application_number() -> str:
    return f"GFF-{uuid.uuid4().hex[:6].upper()}"


@router.post("/verify-otp")
def verify_otp(req: VerifyOtpRequest, response: Response,
               db: Session = Depends(get_session)) -> dict:
    if not (req.otp.isdigit() and 4 <= len(req.otp) <= 6):
        return {"success": False, "message": "OTP must be 4-6 digits"}

    res = otp.verify_otp(db, otp_ref_id=req.otp_ref_id, target=req.mobile, otp=req.otp)
    if not res.ok:
        track_event(db, event_type="otp_verify_failed", actor="customer",
                    detail={"reason": res.reason})
        return {"success": False, "message": f"OTP verification failed ({res.reason})"}

    # Upsert customer.
    user = db.exec(select(CustomerUser).where(CustomerUser.mobile == req.mobile)).first()
    if user is None:
        user = CustomerUser(mobile=req.mobile)
        db.add(user)
        db.flush()

    # Create the application (bundle starts with the mobile-anchored proposal_id).
    proposal_id = f"prop-{uuid.uuid4().hex[:10]}"
    app = Application(
        application_number=_application_number(),
        proposal_id=proposal_id,
        insurer_slug=req.insurer_slug,
        customer_id=user.id,
        status="otp_verified",
        current_step=1,
        bundle={
            "proposal_id": proposal_id,
            "meta": {"insurer": req.insurer_slug, "received_at": _now().isoformat()},
            "application": {
                "applicant": {"name": "", "age": 0, "mobile": req.mobile},
                "product": {
                    "type": req.initial_plan_type or "individual_health",
                    "sum_assured": req.initial_sum_insured or 0,
                },
            },
            "consents": [{"type": "dpdp", "framework": "DPDP_Act", "granted": True,
                          "timestamp": _now().isoformat()}],
            "signals": {},
        },
    )
    db.add(app)
    db.flush()

    # DPDP consent taken at the gate must ALSO land in the Consent audit table (not only the
    # bundle dict) so the consent trail is queryable end-to-end (compliance tracking).
    from .models import Consent
    db.add(Consent(application_id=app.id, type="dpdp", framework="DPDP_Act", granted=True))

    track_event(db, event_type="otp_verified", actor="customer", application_id=app.id,
                detail={"mobile": req.mobile})
    track_event(db, event_type="consent_recorded", application_id=app.id,
                detail={"type": "dpdp", "framework": "DPDP_Act"})
    track_event(db, event_type="application_created", application_id=app.id,
                detail={"application_number": app.application_number})

    # Mobile -> PAN + profile prefill (real vendor; records the gap if not configured).
    _prefill_from_mobile(db, app, req.mobile)

    # Session cookie.
    cookie_value = auth.create_session(db, customer_id=user.id, application_id=app.id)
    response.set_cookie(
        key=auth.COOKIE_NAME, value=cookie_value,
        httponly=True, samesite="lax", max_age=7 * 24 * 3600,
    )

    return {
        "success": True,
        "application_id": app.id,
        "application_number": app.application_number,
        "next_step": 2,   # gate done; open the console at Step 1's content (step index 2 in the pasted contract)
    }


def _prefill_from_mobile(db: Session, app: Application, mobile: str) -> None:
    """Fetch the profile and merge it into the bundle. Records real vs unavailable.

    MOBILE_PAN_MOCK_MODE=1 (journey testing/CI, HEALTH_AGENT_PLAN.md Phase K) skips the
    real vendor call entirely and merges a canned profile instead — every mobile number
    resolves instantly, no network, no vendor timeout/502 risk. Off by default; the
    existing MOBILE_PAN_* creds path is completely unaffected when this isn't set."""
    if mobile_pan.mock_mode_enabled():
        data = mobile_pan.mock_profile_for(mobile)
        _merge_profile_into_bundle(app, data)
        db.add(app)
        db.flush()
        track_api_call(db, provider="mobile_pan", endpoint="(mock mode)", mode="mock",
                       application_id=app.id, ok=True,
                       request_summary={"mobile": mobile},
                       response_summary={"keys": sorted(data.keys())})
        track_event(db, event_type="profile_prefilled", application_id=app.id,
                    detail={"pan": (data.get("pan") or "")[:10], "mock": True})
        return
    if not mobile_pan.configured():
        track_api_call(db, provider="mobile_pan", endpoint="(unconfigured)", mode="mock",
                       application_id=app.id, ok=False,
                       error="MOBILE_PAN_* creds absent — prefill skipped",
                       response_summary={"configured": False})
        track_event(db, event_type="profile_prefill_skipped", application_id=app.id,
                    detail={"reason": "mobile_pan not configured"})
        return
    t0 = time.time()
    try:
        data = mobile_pan.fetch_profile(mobile, insurer_slug=app.insurer_slug)
        latency_ms = int((time.time() - t0) * 1000)
        _merge_profile_into_bundle(app, data)
        db.add(app)
        db.flush()
        track_api_call(db, provider="mobile_pan", endpoint="mobile-to-pan", mode="real",
                       application_id=app.id, ok=True, latency_ms=latency_ms,
                       request_summary={"mobile": mobile},
                       response_summary={"keys": sorted(data.keys())[:12]})
        track_event(db, event_type="profile_prefilled", application_id=app.id,
                    detail={"pan": (data.get("pan") or "")[:10]})
    except Exception as e:  # vendor error must not break the gate (§11)
        latency_ms = int((time.time() - t0) * 1000)
        track_api_call(db, provider="mobile_pan", endpoint="mobile-to-pan", mode="real",
                       application_id=app.id, ok=False, latency_ms=latency_ms,
                       error=str(e)[:200])
        track_event(db, event_type="profile_prefill_error", application_id=app.id,
                    detail={"error": str(e)[:200]})


def _merge_profile_into_bundle(app: Application, data: dict) -> None:
    """Map the vendor `data` block into the bundle's applicant + signals (facts only).

    Builds a NEW bundle dict and REASSIGNS it (a fresh object, so SQLAlchemy's JSON /
    MutableDict change-tracking flags the column dirty — mutating the loaded dict in
    place did NOT persist; the copy-then-reassign dance confused MutableDict). Deep-copy
    the base so we never share nested objects with the tracked column.
    """
    import copy

    bundle = copy.deepcopy(dict(app.bundle))
    ident = data.get("identity") or {}
    applicant = bundle.setdefault("application", {}).setdefault("applicant", {})
    if ident.get("name"):
        applicant["name"] = ident["name"]
    for k in ("dob", "gender"):
        if ident.get(k):
            applicant[k] = ident[k]
    if ident.get("dob"):
        from .step_routes import _age_from_dob
        age = _age_from_dob(ident["dob"])
        if age is not None:
            applicant["age"] = age
    addr = ident.get("address") or {}
    if addr:
        applicant["pincode"] = addr.get("pincode") or applicant.get("pincode")
        applicant["address"] = ", ".join(
            str(addr.get(f, "")) for f in ("buildingName", "streetName", "city", "state", "pincode")
            if addr.get(f)
        ) or applicant.get("address")

    signals = bundle.setdefault("signals", {})
    if data.get("pan"):
        signals["pan_verify"] = {
            "status": "available", "pan": data["pan"],
            "pan_status": "valid" if (ident.get("panStatus") or "").upper() == "ACTIVE" else "invalid",
            "name": ident.get("name"), "dob": ident.get("dob"),
            "aadhaar_seeded": ident.get("aadhaarLinked"),
        }
    if data.get("litigation"):
        # Route through the registered litigation adapter (vendor shape {type, firDetails}
        # -> internal {civil_criminal, firs_registered} the scorer/R-018 read). Copying raw
        # here would silently mis-score — the exact Phase-A "silent miss" to avoid.
        from underwriting.sources import adapt
        mapped = adapt("litigation_fir", data["litigation"])
        mapped.setdefault("status", "available")
        signals["litigation_fir"] = mapped
    sp = data.get("soleProprietor") or {}
    # B4: read activeAlerts INDEPENDENT of the gst[] presence (alerts can arrive alone).
    # B2: scan ALL gst[] entries, not just gst[0] — a CANCELLED GSTIN listed after an
    # ACTIVE one would otherwise be invisible (Paulson has exactly this).
    gst_list = sp.get("gst") or []
    active_alerts = sp.get("activeAlerts") or []
    if gst_list or active_alerts:
        # "any cancelled across the whole list" is the material fact R-019 cares about.
        any_cancelled = any("CANCEL" in str(g.get("status", "")).upper() for g in gst_list)
        first = gst_list[0] if gst_list else {}
        bp = sp.get("businessProfile") or {}
        signals["gst"] = {
            "status": "available",
            "gstin": first.get("gstin"),
            "gstin_count": len(gst_list),
            "any_cancelled": any_cancelled,           # derived across ALL entries (B2)
            "statuses": [g.get("status") for g in gst_list],
            "turnover_slab": _gst_turnover(first),
            # R-019 reads `activeAlerts` (camelCase) off model_extra — write that exact key
            # (a prior snake_case-only write meant R-019 never saw the alerts). Keep the
            # snake_case alias too for the rail/display.
            "activeAlerts": active_alerts,
            "active_alerts": active_alerts,
            # business-profile facts (income-stability + hazard inputs) — previously dropped.
            "registration_date": bp.get("dateOfIncorporation"),
            "nature_of_business": bp.get("natureOfBusiness") or [],
            "trade_name": bp.get("tradeName"),
        }
    emp = data.get("employment") or {}
    if emp:
        # B3: dateOfJoining is nested under history[], not at the top of employment.
        hist = emp.get("history") or []
        current = next((h for h in hist if h.get("isCurrentEmployer")), (hist[0] if hist else {}))
        signals["epfo"] = {
            "status": "available",
            "employer": emp.get("currentEmployer"),
            "uan": emp.get("uan"),
            "employment_type": "salaried" if ident.get("isSalaried") else None,
            "date_of_joining": current.get("dateOfJoining") or emp.get("dateOfJoining"),  # B3
            "job_count": len(hist),
        }
    # B1: director is a real moral-hazard signal with a rule already waiting for it
    # (scoring._s_occupation -35, R-012). It was dropped entirely. isDirector true (or a
    # directorProfile present) -> populate mca_director so that rule can actually fire.
    dp = data.get("directorProfile")
    if ident.get("isDirector") is True or dp:
        signals["mca_director"] = {
            "status": "available",
            "is_director": True,
            # director_default is the FACT the -35 rule reads; the vendor marks it in the
            # director profile when present. Absent/unknown -> False (no false penalty).
            "director_default": bool((dp or {}).get("isDefaulter") or (dp or {}).get("defaulter")),
            "entity": (dp or {}).get("companyName") or (dp or {}).get("entity"),
        }
    if data.get("mobileIntelligence"):
        mi = data["mobileIntelligence"]
        signals["mobile_intel"] = {
            "status": "available", "number": data.get("mobile"),
            "provider": mi.get("currentServiceProvider"),
            "ported_recently": (mi.get("isPorted") == "Yes"),
            # previously-dropped facts the scorer/rail should see:
            "vintage_months": _mobile_age_months(mi.get("mobileAge")),
            "number_valid": (str(mi.get("numberValid", "")).lower() == "yes"),
            "line_status": mi.get("status"),
            "region_shift": bool(mi.get("currentRegion") and mi.get("originalRegion")
                                 and mi.get("currentRegion") != mi.get("originalRegion")),
            "current_region": mi.get("currentRegion"),
            "original_region": mi.get("originalRegion"),
        }
    app.bundle = bundle


def _gst_turnover(gst_entry: dict):
    """Vendor turnovers arrive as either a list [{turnover}] (Paulson) or a dict
    {turnover, financialYear} (Sabarish). Return the turnover string, tolerating both."""
    t = gst_entry.get("turnovers")
    if isinstance(t, list):
        return (t[0] if t else {}).get("turnover")
    if isinstance(t, dict):
        return t.get("turnover")
    return None


def _mobile_age_months(age_label) -> Optional[int]:
    """Vendor mobileAge is a text band ("11 to 12 Years", "18 to 19 Years"). Parse the
    LOWER bound to months — conservative (younger) so a fraud threshold never under-reads.
    Unparseable -> None (absent, not a false-safe 0)."""
    if not isinstance(age_label, str):
        return None
    import re
    nums = re.findall(r"\d+", age_label)
    if not nums:
        return None
    low = int(nums[0])
    return low * 12 if "year" in age_label.lower() else low
