"""models.py — every persisted table for the journey + full tracking trail.

SQLModel (SQLAlchemy + Pydantic). SQLite today; Postgres by changing DATABASE_URL.
JSON-shaped columns (the assembled bundle, report, request/response summaries) are
stored as TEXT via a JSON type so the same models work on SQLite and Postgres.

Tracking is first-class (user: "database for each and every tracking"):
  - ApiCallLog  — every OUTBOUND vendor call (real vs mock flagged).
  - EventLog    — every journey EVENT (step entered, otp sent, fetch done, decision…).
Both hang off an application so the whole timeline is reconstructable per applicant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

# JSON columns that get MUTATED in place after load (the bundle grows step by step,
# decision.report is built up) MUST use MutableDict so SQLAlchemy flags them dirty and
# emits an UPDATE. Plain JSON only detects whole-object REASSIGNMENT reliably, and even
# that missed a reassign-to-equal-shaped-dict once (the prefill bug). MutableDict.as_mutable
# tracks nested set/setdefault too.
_MutJSON = MutableDict.as_mutable(JSON)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core journey
# ---------------------------------------------------------------------------
class Application(SQLModel, table=True):
    """One applicant's onboarding. `bundle` is the ProposalInput being assembled
    step by step; it is what gets POSTed to the engine at Step 5."""

    id: Optional[int] = Field(default=None, primary_key=True)
    application_number: str = Field(index=True, unique=True)  # human ref, e.g. GFF-2481
    proposal_id: str = Field(index=True)                      # engine's proposal_id
    insurer_slug: str = "acme"
    customer_id: Optional[int] = Field(default=None, foreign_key="customeruser.id", index=True)
    status: str = "otp_verified"     # otp_verified -> in_progress -> decided -> issued
    current_step: int = 1
    bundle: dict[str, Any] = Field(default_factory=dict, sa_column=Column(_MutJSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CustomerUser(SQLModel, table=True):
    """Upserted on OTP verify — the verified mobile is the identity anchor."""

    id: Optional[int] = Field(default=None, primary_key=True)
    mobile: str = Field(index=True, unique=True)
    name: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Session(SQLModel, table=True):
    """DB-backed server session; its id is what the signed httpOnly cookie carries."""

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True)          # random opaque id in the cookie
    customer_id: int = Field(foreign_key="customeruser.id", index=True)
    application_id: int = Field(foreign_key="application.id", index=True)
    expires_at: datetime
    revoked: bool = False
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Auth / OTP
# ---------------------------------------------------------------------------
class OtpLog(SQLModel, table=True):
    """One OTP issuance. The OTP is stored ONLY as a SHA-256 hash (salt = target),
    never plaintext. `otp_ref_id` correlates send<->verify."""

    id: Optional[int] = Field(default=None, primary_key=True)
    otp_ref_id: str = Field(index=True, unique=True)
    target: str = Field(index=True)          # mobile or email (the salt)
    channel: str = "sms"                     # sms | email
    purpose: str = "mobile_verification"     # or payment_authorization
    otp_hash: str                            # sha256(otp + target)
    attempts: int = 0
    max_attempts: int = 5
    expires_at: datetime
    consumed: bool = False
    created_at: datetime = Field(default_factory=_now)


class Consent(SQLModel, table=True):
    """Each dedicated consent captured at its point of use (DPDP, Aadhaar, AA, ABHA)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: int = Field(foreign_key="application.id", index=True)
    type: str                                # dpdp | aadhaar_ekyc | bank_statement_upload | abha
    framework: Optional[str] = None          # DPDP_Act | Aadhaar_Act | ABDM
    granted: bool = False
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------
class DecisionRecord(SQLModel, table=True):
    """The engine's output for one application — verdict + the full report JSON."""

    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: int = Field(foreign_key="application.id", index=True)
    verdict: str
    status: str = "complete"                 # complete | pending (STEP_UP)
    waiting_on: Optional[str] = None
    safety_score: Optional[float] = None
    report: dict[str, Any] = Field(default_factory=dict, sa_column=Column(_MutJSON))
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Face-scan session (replaces the in-memory NuralX _SESSIONS)
# ---------------------------------------------------------------------------
class FaceScanSession(SQLModel, table=True):
    """Session state machine (docs/vendor_apis.md PART B):
    PENDING -(applicant taps Start)-> IN_PROGRESS -(webhook ok)-> COMPLETED;
    EXPIRED on either TTL, ERROR/TIMEOUT on a failure webhook. `expires_at` always holds
    the CURRENT deadline — set to the primary (QR) TTL at creation, then bumped to the
    secondary (abandonment) TTL when /begin flips PENDING -> IN_PROGRESS."""
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True)
    application_id: int = Field(foreign_key="application.id", index=True)
    client_transaction_id: str = Field(index=True)
    status: str = "PENDING"                  # PENDING -> IN_PROGRESS -> COMPLETED | ERROR | TIMEOUT
    scan_access_url: Optional[str] = None
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)


PENDING_TTL_MIN = 20     # QR/link is live for 20 min before the applicant must tap Start
IN_PROGRESS_TTL_MIN = 30  # once started, 30 min to finish before we call it abandoned


def face_scan_status(fss: "FaceScanSession") -> str:
    """Lazily resolves PENDING/IN_PROGRESS -> EXPIRED past `expires_at`; terminal statuses
    (COMPLETED/ERROR/TIMEOUT) are returned as-is. No background job — checked on read."""
    if fss.status in ("PENDING", "IN_PROGRESS") and fss.expires_at is not None:
        exp = fss.expires_at if fss.expires_at.tzinfo else fss.expires_at.replace(tzinfo=timezone.utc)
        if _now() >= exp:
            return "EXPIRED"
    return fss.status


# ---------------------------------------------------------------------------
# Tracking — "each and every"
# ---------------------------------------------------------------------------
class ApiCallLog(SQLModel, table=True):
    """Every OUTBOUND vendor/engine call. `mode` flags real vs mock so you can see
    exactly which calls hit a live API in any given run."""

    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: Optional[int] = Field(default=None, foreign_key="application.id", index=True)
    provider: str = Field(index=True)        # msg91 | mobile_pan | digilocker | email | iadore | nuralx | abha | underwrite
    endpoint: str
    mode: str = "real"                       # real | mock
    request_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    response_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    ok: bool = True
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class EventLog(SQLModel, table=True):
    """Every journey EVENT — the end-to-end tracking timeline per application."""

    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: Optional[int] = Field(default=None, foreign_key="application.id", index=True)
    actor: str = "system"                    # customer | system | agent
    event_type: str = Field(index=True)      # step_entered | otp_sent | otp_verified | digilocker_fetched | ...
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
