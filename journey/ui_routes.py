"""ui_routes.py — the server-rendered journey pages (Shell A, DESIGN.md §5).

Pages:
  GET /journey                    -> the landing gate (Acme-branded mobile/OTP)
  GET /journey/app/{app_id}       -> the console shell; ?step=N picks the active step
The landing gate POSTs to /api/auth/* (auth_routes) via fetch(); on verify it redirects
into the console. Step content is server-rendered per step (Phases C2-C7 fill each in).

Templates live in journey/templates, static (design-tokens.css + app.js) in journey/static.
All styling flows through design-tokens.css — no per-page hex/type/spacing (DESIGN.md §10).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from . import auth
from .db import get_session, track_event
from .models import Application

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))

router = APIRouter(tags=["journey-ui"])

# The 7 steps (main stepper). id -> (short label, sub-steps).
STEPS = [
    (1, "Identity", ["Profile", "Aadhaar", "Consent"]),
    (2, "Product", ["Type", "Sum insured", "Tenure", "Riders"]),
    (3, "Financial", ["Income", "Source", "Bank statement"]),
    (4, "Health", ["Screeners", "Conditions", "Vitals", "Face scan", "ABHA"]),
    (5, "Decision", ["Verdict", "Report"]),
    (6, "Nominee", ["Details"]),
    (7, "Payment", ["Pay"]),
]


def mount_static(app) -> None:
    """Mount journey/static at /journey/static (called from api.py)."""
    app.mount("/journey/static", StaticFiles(directory=str(_HERE / "static")), name="journey-static")


@router.get("/journey", response_class=HTMLResponse)
def landing(request: Request):
    """The mobile-verification gate — before the stepper."""
    return templates.TemplateResponse(request, "landing.html", {"insurer": "Acme Insurance"})


@router.get("/journey/app/{app_id}", response_class=HTMLResponse)
def console(request: Request, app_id: int, step: int = 1, db: Session = Depends(get_session)):
    """The Shell-A console. Auth-gated by the session cookie -> back to landing if invalid."""
    sess = auth.resolve_session(db, request.cookies.get(auth.COOKIE_NAME))
    if sess is None or sess.application_id != app_id:
        return RedirectResponse("/journey", status_code=303)

    application = db.get(Application, app_id)
    if application is None:
        return RedirectResponse("/journey", status_code=303)

    # Latest decision (for Step 5 report render), if any.
    from sqlmodel import select
    from .models import DecisionRecord
    decision = db.exec(
        select(DecisionRecord).where(DecisionRecord.application_id == app_id)
        .order_by(DecisionRecord.id.desc())
    ).first()

    step = max(1, min(step, 7))
    if step != application.current_step:
        application.current_step = step
        db.add(application)
    # Advance status past the initial gate value once collection is underway (so the
    # status field reflects real progress; decide/payment set decided/issued later).
    if step > 1 and application.status == "otp_verified":
        application.status = "in_progress"
        db.add(application)
    track_event(db, event_type="step_entered", application_id=app_id,
                actor="customer", detail={"step": step})

    return templates.TemplateResponse(
        request,
        "console.html",
        {
            "app": application,
            "steps": STEPS,
            "active_step": step,
            "applicant": application.bundle.get("application", {}).get("applicant", {}),
            "signals": application.bundle.get("signals", {}),
            "product": application.bundle.get("application", {}).get("product", {}),
            "riders_catalog": _riders_catalog(),
            "decision": decision,
            "report": (decision.report if decision else None),
            "nominee": application.bundle.get("application", {}).get("nominee", {}),
            "policy_number": (application.bundle.get("_journey", {}) or {}).get("policy_number"),
            "qs": dict(request.query_params),
        },
    )


def _riders_catalog() -> list[dict]:
    """Rider id + label for Step 2 (from the journey-only pricing table)."""
    from .pricing import RIDERS
    return [{"id": rid, "label": spec[0]} for rid, spec in RIDERS.items()]
