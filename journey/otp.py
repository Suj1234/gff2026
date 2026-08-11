"""otp.py — OTP generation, hashing, storage, verification (channel-agnostic).

Matches the pasted contract's mechanics:
  - 6-digit, cryptographically random (`secrets.randbelow`, not `random`).
  - stored ONLY as SHA-256(otp + target) — plaintext never persisted (otp_log.otp_hash).
  - 10-minute TTL.
  - per-ref attempt cap; exceeding it invalidates the ref (consumed=True).

Both the real (MSG91) and mock paths call `create_otp` — the only difference is
whether the SMS actually goes out and whether the OTP is echoed as debug_otp.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from .models import OtpLog

_TTL_MINUTES = 10
_MAX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(otp: str, target: str) -> str:
    return hashlib.sha256(f"{otp}{target}".encode("utf-8")).hexdigest()


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def create_otp(session: Session, *, target: str, channel: str, purpose: str) -> tuple[str, OtpLog]:
    """Generate + persist a new OTP row. Returns (plaintext_otp, row). The plaintext
    is returned to the CALLER only so it can send the SMS / echo debug_otp — it is
    never stored."""
    otp = generate_otp()
    row = OtpLog(
        otp_ref_id=str(uuid.uuid4()),
        target=target,
        channel=channel,
        purpose=purpose,
        otp_hash=_hash(otp, target),
        max_attempts=_MAX_ATTEMPTS,
        expires_at=_now() + timedelta(minutes=_TTL_MINUTES),
    )
    session.add(row)
    session.flush()
    return otp, row


class VerifyResult:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason


def verify_otp(session: Session, *, otp_ref_id: str, target: str, otp: str) -> VerifyResult:
    """Check a submitted OTP against its ref. Enforces TTL + attempt cap; marks the
    row consumed on success or on exceeding attempts."""
    row = session.exec(select(OtpLog).where(OtpLog.otp_ref_id == otp_ref_id)).first()
    if row is None:
        return VerifyResult(False, "unknown_ref")
    if row.consumed:
        return VerifyResult(False, "already_used")
    # normalize aware/naive (SQLite reads back naive UTC)
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if _now() > exp:
        return VerifyResult(False, "expired")
    if row.target != target:
        return VerifyResult(False, "target_mismatch")

    row.attempts += 1
    if row.otp_hash == _hash(otp, target):
        row.consumed = True
        session.add(row)
        session.flush()
        return VerifyResult(True, "ok")

    if row.attempts >= row.max_attempts:
        row.consumed = True  # too many wrong tries -> burn the ref
        session.add(row)
        session.flush()
        return VerifyResult(False, "max_attempts")
    session.add(row)
    session.flush()
    return VerifyResult(False, "wrong_otp")


def _demo() -> None:
    """Self-check: create -> verify wrong -> verify right -> can't reuse."""
    from sqlmodel import SQLModel, create_engine
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        otp, row = create_otp(s, target="9876543210", channel="sms", purpose="mobile_verification")
        assert len(otp) == 6 and otp.isdigit()
        assert not verify_otp(s, otp_ref_id=row.otp_ref_id, target="9876543210", otp="000000").ok
        assert verify_otp(s, otp_ref_id=row.otp_ref_id, target="9876543210", otp=otp).ok
        # consumed -> cannot reuse
        assert not verify_otp(s, otp_ref_id=row.otp_ref_id, target="9876543210", otp=otp).ok
    print("journey.otp OK — generate, wrong-reject, right-accept, no-reuse")


if __name__ == "__main__":
    _demo()
