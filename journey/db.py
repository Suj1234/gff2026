"""db.py — engine + session + the two tracking helpers.

SQLite by default (`DATABASE_URL=sqlite:///./journey.db`); swap to Postgres by
changing that one env var. `init_db()` runs `create_all()` on startup (§ agreed:
no Alembic for the demo — add it if the schema starts churning on Postgres).

`track_event()` / `track_api_call()` are the single funnel for the "each and every
tracking" requirement — every route and vendor client records through these, so the
timeline is never something a caller can forget to write in an ad-hoc way.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401 — registers tables on the metadata

_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./journey.db")

# check_same_thread=False: FastAPI serves requests across threads; safe here because
# each request opens its own short-lived Session (no shared connection object).
# ponytail: SQLite single-writer is fine for a demo; the URL swap to Postgres is the
# upgrade path when concurrency matters — no code change beyond DATABASE_URL.
_kwargs: dict = {}
if _DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
    # In-memory SQLite (sqlite://) gives each new connection a SEPARATE empty DB, so
    # create_all() on startup would be invisible to request connections. StaticPool
    # pins ONE connection so all sessions share it. Harmless for the file/Postgres path.
    if ":memory:" in _DATABASE_URL or _DATABASE_URL in ("sqlite://", "sqlite:///:memory:"):
        _kwargs["poolclass"] = StaticPool
engine = create_engine(_DATABASE_URL, **_kwargs)


def init_db() -> None:
    """Create all tables. Idempotent — safe to call every startup."""
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """A committed unit of work. Rolls back on error, always closes."""
    s = Session(engine)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency — one Session per request. Commits on success so writes
    (OTP rows, events, applications) persist for the NEXT request; rolls back on error."""
    s = Session(engine)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Tracking funnel — every event / vendor call goes through here
# ---------------------------------------------------------------------------
def track_event(
    session: Session,
    *,
    event_type: str,
    application_id: Optional[int] = None,
    actor: str = "system",
    detail: Optional[dict[str, Any]] = None,
) -> models.EventLog:
    row = models.EventLog(
        application_id=application_id, actor=actor,
        event_type=event_type, detail=detail or {},
    )
    session.add(row)
    session.flush()  # assign id without ending the caller's transaction
    return row


def track_api_call(
    session: Session,
    *,
    provider: str,
    endpoint: str,
    mode: str = "real",
    application_id: Optional[int] = None,
    request_summary: Optional[dict[str, Any]] = None,
    response_summary: Optional[dict[str, Any]] = None,
    ok: bool = True,
    http_status: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error: Optional[str] = None,
) -> models.ApiCallLog:
    row = models.ApiCallLog(
        application_id=application_id, provider=provider, endpoint=endpoint, mode=mode,
        request_summary=request_summary or {}, response_summary=response_summary or {},
        ok=ok, http_status=http_status, latency_ms=latency_ms, error=error,
    )
    session.add(row)
    session.flush()
    return row


def _demo() -> None:
    """Self-check: tables build, tracking rows persist + read back."""
    global engine, _DATABASE_URL
    _DATABASE_URL = "sqlite://"  # in-memory
    engine = create_engine(_DATABASE_URL, connect_args={"check_same_thread": False})
    init_db()
    with session_scope() as s:
        app = models.Application(application_number="GFF-TEST", proposal_id="p1")
        s.add(app)
        s.flush()
        track_event(s, event_type="step_entered", application_id=app.id, detail={"step": 1})
        track_api_call(s, provider="msg91", endpoint="/flow", mode="mock",
                       application_id=app.id, ok=True, latency_ms=12)
    with Session(engine) as s:
        from sqlmodel import select
        events = s.exec(select(models.EventLog)).all()
        calls = s.exec(select(models.ApiCallLog)).all()
        assert len(events) == 1 and events[0].event_type == "step_entered", events
        assert len(calls) == 1 and calls[0].mode == "mock", calls
    print("journey.db OK — tables build, event + api_call tracked and read back")


if __name__ == "__main__":
    _demo()
