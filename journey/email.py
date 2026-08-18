"""email.py — the email-intelligence fetch (vendor_apis.md §3, same Perfios gateway).

One call from an email returns validity + fraud/spam/disposable signals. Wired into
Step 1: the raw vendor `data` block is mapped to `signals.email_intel` via the
sources/email adapter (which INVERTS the vendor's 1-100 fraud score to 0-1) — this
module only FETCHES.

Same transport as mobile_pan.py: POST {EMAIL_BASE_URL}{EMAIL_ENDPOINT}, x-api-key header,
body {"email": ...}. If EMAIL_* creds are absent the caller records the gap and the
journey keeps the typed email as a plain fact (never blocks, §11).
"""

from __future__ import annotations

import os

import requests


def configured() -> bool:
    return bool(os.getenv("EMAIL_BASE_URL") and os.getenv("EMAIL_API_KEY"))


def _headers() -> dict[str, str]:
    return {"x-api-key": os.getenv("EMAIL_API_KEY", ""), "Content-Type": "application/json"}


def fetch(email: str, *, insurer_slug: str = "acme", timeout: int = 30) -> dict:
    """POST the email -> the raw vendor `data` block (vendor_apis §3 response).
    Retries the POC gateway's transient 5xx once (mirrors mobile_pan)."""
    if not configured():
        raise RuntimeError("email intel not configured (EMAIL_* env vars absent)")
    path = os.getenv("EMAIL_ENDPOINT", "/api/external/prefill/email")
    url = f"{os.environ['EMAIL_BASE_URL'].rstrip('/')}{path}"
    payload = {"email": email, "insurer_slug": insurer_slug}
    # ponytail: fixed 1-retry, no backoff — POC-grade, same as mobile_pan.
    last: Exception | None = None
    for _ in range(2):
        r = requests.post(url, json=payload, headers=_headers(), timeout=timeout)
        if r.status_code in (502, 503, 504):
            last = requests.HTTPError(f"{r.status_code} transient", response=r)
            continue
        r.raise_for_status()
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else {}
    raise last  # both attempts hit a transient gateway error
