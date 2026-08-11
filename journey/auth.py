"""auth.py — DB-backed server sessions + the signed httpOnly cookie.

On OTP verify we create a Session row and hand the client a cookie carrying its
opaque token, signed with SESSION_SECRET (itsdangerous) so it can't be forged.
Simpler than JWT, revocable (flip Session.revoked), no token-payload sprawl.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, URLSafeSerializer
from sqlmodel import Session as DBSession
from sqlmodel import select

from .models import Session as SessionRow

COOKIE_NAME = "gff_session"
_SESSION_DAYS = 7


def _serializer() -> URLSafeSerializer:
    secret = os.getenv("SESSION_SECRET", "dev-only-change-me-in-prod")
    return URLSafeSerializer(secret, salt="gff-session")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(db: DBSession, *, customer_id: int, application_id: int) -> str:
    """Create a session row; return the SIGNED cookie value to set."""
    token = secrets.token_urlsafe(32)
    row = SessionRow(
        token=token, customer_id=customer_id, application_id=application_id,
        expires_at=_now() + timedelta(days=_SESSION_DAYS),
    )
    db.add(row)
    db.flush()
    return _serializer().dumps(token)


def resolve_session(db: DBSession, cookie_value: str | None) -> SessionRow | None:
    """Validate a cookie -> live Session row (or None if missing/forged/expired/revoked)."""
    if not cookie_value:
        return None
    try:
        token = _serializer().loads(cookie_value)
    except BadSignature:
        return None
    row = db.exec(select(SessionRow).where(SessionRow.token == token)).first()
    if row is None or row.revoked:
        return None
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if _now() > exp:
        return None
    return row


def _demo() -> None:
    from sqlmodel import SQLModel, create_engine
    from .models import Application, CustomerUser
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with DBSession(eng) as db:
        u = CustomerUser(mobile="9876543210"); db.add(u); db.flush()
        a = Application(application_number="GFF-1", proposal_id="p1", customer_id=u.id)
        db.add(a); db.flush()
        cookie = create_session(db, customer_id=u.id, application_id=a.id)
        assert resolve_session(db, cookie) is not None
        assert resolve_session(db, "tampered.value") is None
        assert resolve_session(db, None) is None
    print("journey.auth OK — session create, valid-resolve, forged-reject, none-reject")


if __name__ == "__main__":
    _demo()
