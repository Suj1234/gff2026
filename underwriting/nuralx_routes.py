"""nuralx_routes.py — FastAPI routes for the NuralX face-vitals scan.

Mount into your app (in api.py):

    from .nuralx_routes import router as nuralx_router
    app.include_router(nuralx_router)

Endpoints:
    POST /nuralx/sessions          create a scan → returns { token, scan_url }
    GET  /nuralx/sessions/{token}  poll status + vitals
    POST /nuralx/callback          PUBLIC webhook NuralX POSTs vitals to (auth via ?key=)

Persistence: this project is a stateless underwriting pipeline with no database, so
sessions are held in an in-memory dict — good enough for a demo / single process. Swap
`_SESSIONS` for a real store (Redis/Postgres) before relying on it in production; an
in-memory dict is lost on restart and not shared across instances.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from fastapi import APIRouter, Request

from .nuralx import Patient, creds_from_env, initiate_scan, map_callback_vitals
from .sources import nuralx as nuralx_adapter

router = APIRouter(prefix="/nuralx", tags=["nuralx"])

# token -> session dict. Replace with a durable store for production (see module docstring).
_SESSIONS: dict[str, dict[str, Any]] = {}


@router.post("/sessions")
def create_session(body: dict[str, Any]) -> dict[str, Any]:
    """Start a scan. Body: {name, email?, phone?}. Returns the scan_url to open."""
    token = str(uuid.uuid4())  # == NuralX client_transaction_ID, echoed back in the webhook
    scan = initiate_scan(
        creds_from_env(),
        session_token=token,
        patient=Patient(
            name=body.get("name") or "Applicant",
            email=body.get("email"),
            phone=body.get("phone"),
        ),
    )
    _SESSIONS[token] = {
        "token": token,
        "status": "PENDING",
        "scan_url": scan.scan_url,
        "vitals": None,
        "raw_results": None,
        "signals": None,  # internal engine shape (liveness_facematch / rppg_scan / facial_bmi_smoking)
    }
    return {"token": token, "scan_url": scan.scan_url, "status": "PENDING"}


@router.get("/sessions/{token}")
def get_session(token: str) -> dict[str, Any]:
    """Poll a session's current status + vitals (once the webhook has landed)."""
    session = _SESSIONS.get(token)
    if not session:
        return {"status": "NOT_FOUND"}
    return {
        "status": session["status"],
        "vitals": session.get("vitals"),
        "raw_results": session.get("raw_results"),
        # The internal signal shapes the engine reads — merge under ProposalInput.signals.
        # `.get` so a session dict built without the key (older store / other writer)
        # degrades to None instead of a KeyError.
        "signals": session.get("signals"),
    }


@router.post("/callback")
async def callback(request: Request) -> dict[str, Any]:
    """PUBLIC webhook. NuralX POSTs the vitals here. Auth via a shared ?key= secret.

    Read the RAW body and parse JSON ourselves — NuralX may send an off Content-Type,
    and relying on framework auto-parsing would silently drop the body. Always ACK 200.
    """
    secret = os.environ.get("NURALX_CALLBACK_SECRET")
    if not secret or request.query_params.get("key") != secret:
        return {"received": True}  # ACK but ignore — bad/missing secret

    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"received": True}

    token = body.get("client_transaction_ID")
    session = _SESSIONS.get(token) if token else None
    if not session:
        return {"received": True}

    is_failure = body.get("status") in ("timeout", "error") or not body.get("results")
    # Fan the raw webhook body out to the three internal signal shapes. On a
    # failure/timeout body the adapter yields all-`unavailable` (§11) — a partial
    # bundle the engine reasons around, never a crash.
    session["signals"] = nuralx_adapter.to_signals(body)
    if is_failure:
        session["status"] = "TIMEOUT" if body.get("status") == "timeout" else "ERROR"
    else:
        results = body["results"]
        session["status"] = "COMPLETED"
        session["vitals"] = map_callback_vitals(results)
        session["raw_results"] = results

    return {"received": True}
