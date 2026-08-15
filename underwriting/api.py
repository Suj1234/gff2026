"""api.py — the single endpoint (§9, Phase 4).

    POST /underwrite   {ProposalInput bundle}  →  the full Appendix-A report

One function behind one endpoint (§2): deterministic rules + the narrow LLM are
steps *inside* `pipeline.run`, not separate services. This module is only the
HTTP shell + the STEP_UP `pending` handling.

STEP_UP (§2 async note): the synchronous core returns the report with a `pending`
status and *what it is waiting on* (`decision.next_step`). Durable pause/resume
(Temporal) is a later phase — v1 surfaces the pending state, it does not block.

Run:  uvicorn underwriting.api:app --reload
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI

from . import pipeline
from .abha_routes import router as abha_router
from .nuralx_routes import router as nuralx_router
from .report import build_report
from .schemas import ProposalInput, ReportOutput

# Served behind the gateway at /demo/life. root_path lets every route (/api/...,
# /health, the SPA) answer under that prefix without hardcoding it in each router.
# Local dev leaves it empty → app runs at root. nginx passes the full path through.
ROOT_PATH = os.getenv("ROOT_PATH", "")

app = FastAPI(title="Onboarding Risk Assessment", version="phase4", root_path=ROOT_PATH)

# Data-collection endpoints the journey calls before the single /underwrite (Phase B):
#   - NuralX face-scan session + webhook  (rppg_scan / liveness_facematch / facial_bmi_smoking)
#   - mock ABHA health-records fetch       (abha_health_records → R-010 / POSTPONE)
app.include_router(nuralx_router)
app.include_router(abha_router)

# Phase C — the journey (DB-backed UI + tracking) mounted into the same app so the
# whole demo runs on ONE command. This is web plumbing only; the engine (rules/
# scoring/decision) is untouched. The journey package wraps pipeline.run/build_report.
try:
    from journey.db import init_db
    from journey.auth_routes import router as journey_auth_router
    from journey.step_routes import callback_router as journey_callback_router
    from journey.step_routes import router as journey_step_router
    from journey.ui_routes import mount_static
    from journey.ui_routes import router as journey_ui_router

    @app.on_event("startup")
    def _journey_startup() -> None:
        init_db()  # create_all — idempotent

    app.include_router(journey_auth_router)
    app.include_router(journey_step_router)
    app.include_router(journey_callback_router)
    app.include_router(journey_ui_router)
    mount_static(app)
except ImportError:
    # journey/ deps not installed → the engine API still runs standalone.
    pass

# Core 6 outcomes that leave the case waiting on a customer/vendor action.
_PENDING_VERDICTS = {"STEP_UP"}


def run_and_report(inp: ProposalInput, gather=None) -> ReportOutput:
    """Intake → pipeline → assembled report. The shared core for the endpoint
    and the tests (one code path, §11 idempotency: same input → same report)."""
    result = pipeline.run(inp, gather=gather)
    return build_report(result)


def _status(report: ReportOutput) -> str:
    """`pending` when the decision is waiting on evidence (STEP_UP), else `complete`."""
    verdict = report.decision.verdict if report.decision else None
    return "pending" if verdict in _PENDING_VERDICTS else "complete"


@app.post("/underwrite")
def underwrite(inp: ProposalInput) -> dict:
    """Underwrite one proposal → the full report object.

    Response envelope: `{status, waiting_on?, report}`. `status="pending"` with
    `waiting_on` set to the next step for STEP_UP; `status="complete"` otherwise.
    FastAPI validates the body against `ProposalInput` at the trust boundary; a
    partial bundle is normal (optional fields) and is reasoned around, not rejected.
    """
    report = run_and_report(inp)
    status = _status(report)
    out = {"status": status, "report": report.model_dump()}
    if status == "pending":
        out["waiting_on"] = report.decision.next_step if report.decision else None
    return out


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ── Serve the built React journey-ui (SPA) ─────────────────────────────────────
# The Dockerfile copies the Vite build to ./journey-ui/dist. Registered LAST so all
# API routes above win; only unmatched GETs fall through to the SPA.
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_UI_DIST = Path(__file__).resolve().parent.parent / "journey-ui" / "dist"

if (_UI_DIST / "index.html").exists():
    # Hashed JS/CSS/images built by Vite under /assets.
    app.mount("/assets", StaticFiles(directory=str(_UI_DIST / "assets")), name="ui-assets")

    _INDEX = _UI_DIST / "index.html"

    @app.get("/")
    def _spa_root() -> FileResponse:
        return FileResponse(_INDEX)

    # SPA fallback: any non-API GET returns index.html so client-side routing /
    # page refreshes work. API paths are already matched by the routers above.
    @app.get("/{full_path:path}")
    def _spa_catch_all(full_path: str) -> FileResponse:
        candidate = _UI_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)  # favicon.svg, etc. at the dist root
        return FileResponse(_INDEX)
