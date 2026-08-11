"""sources/ — vendor adapters behind the internal contract (§3 adapter rule, §9).

The internal contract is the per-source signal shape the rest of the system reads
(the dicts under `ProposalInput.signals`, defined field-for-field in schemas.py).
Business logic (rules, scoring, judge) NEVER sees a raw vendor response — it only
ever sees the internal shape. A real vendor's raw payload is mapped to that shape
here, in an adapter. So "which vendor for PAN?" is a swap-in (register a different
adapter under the same source key), not a rewrite (§12 open decision #6, files/CLAUDE.md).

**Mock the RESPONSE, never the step** (§3, §11): in dev the raw payload is a canned
vendor fixture; the adapter that transforms it is real code and identical in dev,
staging, and prod. Swapping to a live vendor swaps only where the raw bytes come
from — the adapter and everything downstream are unchanged.

Only a couple of representative adapters ship here (PAN identity, Account Aggregator
income) — enough to prove the contract holds and the swap-in works. Adding a vendor
is one `@adapter("source_key")` function; the fixtures already conform to the
internal shape directly (they were authored against it), so the mocks keep passing
with or without an adapter in the path.
"""

from __future__ import annotations

from typing import Callable

# Registry: internal source key → the active adapter (raw vendor dict → internal dict).
# One entry per (source, chosen vendor). Swapping vendors = re-register, no code change
# anywhere downstream.
_REGISTRY: dict[str, Callable[[dict], dict]] = {}


def adapter(source_key: str) -> Callable[[Callable], Callable]:
    """Register a vendor adapter for an internal source key. Last registration wins
    (so a live adapter overrides the dev mock by import order)."""
    def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        _REGISTRY[source_key] = fn
        return fn
    return deco


def adapt(source_key: str, raw: dict) -> dict:
    """Map one vendor's raw response to the internal contract shape. Unregistered
    source → pass the raw through unchanged (it is assumed already-internal, which is
    how the current fixtures are authored — the adapter layer is opt-in per source)."""
    fn = _REGISTRY.get(source_key)
    return fn(raw) if fn else dict(raw)


def adapt_bundle(raw_signals: dict) -> dict:
    """Map a whole raw `signals` bundle (source_key → raw vendor dict) to internal
    shape, source by source. This is the seam a real ingestion layer calls before
    handing the bundle to `ProposalInput`."""
    return {k: adapt(k, v) if isinstance(v, dict) else v for k, v in raw_signals.items()}


def registered() -> list[str]:
    return sorted(_REGISTRY)


# Import the shipped adapters so their @adapter registrations run on package import.
from . import identity as _identity        # noqa: E402,F401
from . import income as _income            # noqa: E402,F401
from . import litigation as _litigation    # noqa: E402,F401
from . import email as _email              # noqa: E402,F401
from . import bank_statement as _bank_statement  # noqa: E402,F401
from . import nuralx as _nuralx            # noqa: E402,F401
