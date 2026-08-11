"""email.py — email-intelligence vendor adapter (internal key: `email_intel`).

Maps the raw email-intel response (docs/vendor_apis.md §3) to the internal
`EmailIntel` contract the fraud sub-score reads (scoring._s_fraud_check).

THE POLARITY INVERSION (docs §3): the vendor's `fraud.risk.score` is 1-100 where
HIGHER = SAFER (83 = "Very Low" risk). The engine's scores are 0-1 where HIGHER =
RISKIER. The adapter inverts: `fraud_risk_score = 1 - score/100`. Missing score →
absent (partial responses are normal, §11), NOT 0 (which would falsely read "safe").

Boundary (§1.8): the vendor's `fraudRisk` label / `riskLevel` are ITS verdicts and
are dropped; we keep the underlying facts (disposable, spam, name-match) + the
inverted numeric, and produce the fraud judgment ourselves.
"""

from __future__ import annotations

from . import adapter


def _invert(score) -> float | None:
    """Vendor 1-100 (higher=safer) → 0-1 (higher=riskier). None-safe; tolerates a
    stringified number ("83") since vendors are inconsistent about JSON types."""
    if isinstance(score, str):
        try:
            score = float(score)
        except ValueError:
            return None
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    return round(max(0.0, min(1.0, 1 - score / 100.0)), 4)


def _dict(x) -> dict:
    """A malformed vendor field that should be an object → an empty dict, never a crash.
    The adapter is a trust boundary (§11): type-confused vendor JSON must not throw."""
    return x if isinstance(x, dict) else {}


@adapter("email_intel")
def from_vendor(raw: dict) -> dict:
    """Vendor raw → internal EmailIntel shape. FACTS only; polarity inverted.

    Robust to malformed input (§11): a missing `data` payload, or any nested field
    arriving as the wrong type, degrades to absent — never an exception."""
    if not isinstance(raw, dict):
        return {"status": "unavailable"}
    # "available" requires a real payload: an envelope like {"success": true} with no
    # `data` is NOT an assessed email (absent ≠ assessed-clean, per report.py limitation).
    d = _dict(raw.get("data")) if "data" in raw else _dict(raw)
    if not d:
        return {"status": "unavailable"}
    ver = _dict(d.get("verification"))
    validity = _dict(ver.get("validity"))
    spam = _dict(ver.get("spamRecord"))
    indv = ver.get("individualMatch")
    first_match = indv[0] if isinstance(indv, list) and indv and isinstance(indv[0], dict) else None
    fraud = _dict(_dict(d.get("fraud")).get("risk"))
    return {
        "status": "available",
        "email": d.get("email"),
        "is_disposable": validity.get("isDisposable"),
        "is_spam": spam.get("isSpam"),
        "name_match": bool(first_match.get("match")) if first_match else None,
        "fraud_risk_score": _invert(fraud.get("score")),
    }
