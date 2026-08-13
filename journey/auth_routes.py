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

    t0 = time.time()
    result = msg91.send_sms_otp(req.mobile, plaintext)
    latency_ms = int((time.time() - t0) * 1000)
    track_api_call(
        db, provider="msg91", endpoint="/api/v5/flow",
        mode="real" if msg91.creds_present() else "mock",
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
    """Fetch the profile and merge it into the bundle. Records real vs unavailable."""
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
    if data.get("soleProprietor") and (data["soleProprietor"] or {}).get("gst"):
        # GST + activeAlerts -> the gst signal the engine reads (R-019).
        sp = data["soleProprietor"]
        gst_list = sp.get("gst") or []
        first = gst_list[0] if gst_list else {}
        signals["gst"] = {
            "status": "available",
            "gstin": first.get("gstin"),
            "turnover_slab": ((first.get("turnovers") or [{}])[0]).get("turnover"),
            "active_alerts": sp.get("activeAlerts") or [],
        }
    if data.get("mobileIntelligence"):
        mi = data["mobileIntelligence"]
        signals["mobile_intel"] = {
            "status": "available", "number": data.get("mobile"),
            "provider": mi.get("currentServiceProvider"),
            "ported_recently": (mi.get("isPorted") == "Yes"),
        }
    app.bundle = bundle
