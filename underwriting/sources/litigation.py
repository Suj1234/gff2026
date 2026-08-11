"""litigation.py — litigation / court-case vendor adapter (internal key: `litigation_fir`).

Maps the raw vendor `litigation` block (docs/vendor_apis.md §1 — the mobile→PAN
enrichment call's `data.litigation`) to the internal `litigation_fir` shape the
Safety-Score scorer reads (`scoring._s_litigation`) and the R-018 rule keys on:

    {status, total_cases, pending_cases, criminal_cases, firs_registered, confidence,
     cases: [{type, civil_criminal, severity, status, cheque_bounce}]}

Boundary (§1.8): the vendor's `severity`/`riskTags` are ITS labels, kept only as
FACTS carried through per case; WE (rules.py / scoring.py) produce the judgment.
Without this adapter the raw shape (`criminalCases`, `cases[].type`, `firDetails[]`)
scores as "no adverse litigation" — a silent miss (Paulson's 10 criminal cases).
"""

from __future__ import annotations

from . import adapter

# NI Act §138 = cheque bounce; a distinct financial-liability signal worth carrying.
_CHEQUE_BOUNCE_MARKERS = ("138", "cheque bounce", "cheque_bounce", "ni act", "negotiable instruments")


def _is_criminal(case: dict) -> bool:
    t = str(case.get("type") or case.get("civil_criminal") or "").strip().lower()
    return "crim" in t


def _list(x) -> list:
    """A vendor field that should be a list → a list, never a crash. The adapter is a
    trust boundary (§11): type-confused vendor JSON (string where a list is expected)
    must degrade to empty, not throw / miscount (len('x') would falsely count 1)."""
    return x if isinstance(x, list) else []


def _cheque_bounce(case: dict) -> bool:
    hay = " ".join(str(x).lower() for x in (
        _list(case.get("riskTags")) + _list(case.get("acts")) + _list(case.get("sections"))
    ))
    return any(m in hay for m in _CHEQUE_BOUNCE_MARKERS)


def _firs_in(case: dict) -> int:
    return len(_list(case.get("firDetails")))


@adapter("litigation_fir")
def from_vendor(raw: dict) -> dict:
    """Vendor `litigation` block → internal `litigation_fir` shape. FACTS only.

    Tolerant of a missing/empty block (no litigation is the common clean case) and of
    malformed input — a non-list `cases`, or a case entry that isn't a dict, yields
    zero counts, not a crash (§11)."""
    if not isinstance(raw, dict) or not raw:
        return {"status": "unavailable"}

    cases = []
    firs_registered = 0
    for c in _list(raw.get("cases")):
        if not isinstance(c, dict):
            continue  # skip a garbage case entry rather than crash on c.get
        cases.append({
            "type": c.get("type"),
            "civil_criminal": "criminal" if _is_criminal(c) else "civil",
            "severity": c.get("severity"),
            "status": c.get("status"),
            "cheque_bounce": _cheque_bounce(c),
        })
        firs_registered += _firs_in(c)

    return {
        "status": "available",
        "total_cases": raw.get("totalCases", len(cases)),
        "pending_cases": raw.get("pendingCases"),
        "criminal_cases": raw.get("criminalCases", sum(1 for c in cases if c["civil_criminal"] == "criminal")),
        "firs_registered": raw.get("firsRegistered", firs_registered),
        "confidence": (raw.get("filter") or {}).get("pincode_matched"),
        "cases": cases,
    }
