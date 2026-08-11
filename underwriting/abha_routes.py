"""abha_routes.py — the mock ABHA (health-records) endpoint for the demo journey.

Mount into api.py:

    from .abha_routes import router as abha_router
    app.include_router(abha_router)

Endpoint:
    POST /abha/records  {pan?, mobile?, consent_granted}  → the keyed ABHA record

Consent-gated (files/CLAUDE.md §22): even against the mock vendor we go through the
actual consent step — no consent → 403-style refusal, never the record. Mock the
RESPONSE (the keyed record), never the STEP. The record shape is exactly the fields the
engine reads (schemas.AbhaHealthRecords + rules.postpone_check); see mock_abha.py.

The returned record drops straight under `ProposalInput.signals.abha_health_records`,
which is where R-010 (non-disclosure) and POSTPONE read it.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import mock_abha

router = APIRouter(prefix="/abha", tags=["abha"])


class AbhaRequest(BaseModel):
    pan: Optional[str] = None
    mobile: Optional[str] = None
    consent_granted: bool = False  # DPDP consent — the step is real even against the mock


@router.post("/records")
def fetch_records(req: AbhaRequest) -> dict[str, Any]:
    """Consent-gated ABHA fetch. Returns the record keyed off PAN/mobile.

    Envelope: `{status, signal}` where `signal` is the internal `abha_health_records`
    shape to merge under `ProposalInput.signals`. Without consent, the record is never
    returned — the consent step is real even in the mock (compliance behaviour we want
    true in dev/staging/prod alike, files/CLAUDE.md §22)."""
    if not req.consent_granted:
        # Consent not granted → no record. Modeled as a source status, not an HTTP error,
        # so the engine treats it as a partial bundle (consent_declined) and reasons on.
        return {"status": "consent_required", "signal": {"status": "consent_declined"}}
    if not (req.pan or req.mobile):
        return {"status": "missing_identity", "signal": {"status": "unavailable"}}
    record = mock_abha.records_for(pan=req.pan, mobile=req.mobile)
    return {"status": "ok", "signal": record}
