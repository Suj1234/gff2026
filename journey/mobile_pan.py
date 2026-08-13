"""mobile_pan.py — the Mobile -> PAN + full-profile fetch (vendor_apis.md §1).

One call from a mobile number returns mobile intelligence, PAN, identity, employment,
litigation, GST, director profile. This prefills Step 1 and seeds the bundle.

REAL client — contract VERIFIED live against the Perfios onboarding POC (2026-08-11):
  POST {MOBILE_PAN_BASE_URL}{MOBILE_PAN_ENDPOINT}
  header: x-api-key = MOBILE_PAN_API_KEY
  body:   {"mobile": "<10-digit>", "insurer_slug": "<slug>"}   # insurer_slug is REQUIRED
  -> 200 {"success": true, "data": { mobileIntelligence, pan, identity, employment,
          litigation, soleProprietor, directorProfile }}  (vendor_apis §1 shape)

Output: the raw vendor `data` block. Mapping raw -> internal `signals.*` happens via
the sources/ adapters (identity, litigation) — this module only FETCHES.
"""

from __future__ import annotations

import os

import requests


def configured() -> bool:
    return bool(os.getenv("MOBILE_PAN_BASE_URL") and os.getenv("MOBILE_PAN_API_KEY"))


def _headers() -> dict[str, str]:
    return {
        "x-api-key": os.getenv("MOBILE_PAN_API_KEY", ""),
        "Content-Type": "application/json",
    }


def fetch_profile(mobile: str, *, insurer_slug: str = "acme", timeout: int = 40) -> dict:
    """POST the mobile number -> the vendor `data` block (vendor_apis §1 response).

    Raises RuntimeError if not configured (caller decides how to surface the gap);
    returns the parsed `data` dict on success, or {} on a vendor error.
    """
    if not configured():
        raise RuntimeError("mobile_pan not configured (MOBILE_PAN_* env vars absent)")
    base = os.environ["MOBILE_PAN_BASE_URL"].rstrip("/")
    path = os.getenv("MOBILE_PAN_ENDPOINT", "/api/external/prefill/mobile")
    url = f"{base}{path}"
    payload = {"mobile": mobile, "insurer_slug": insurer_slug}  # insurer_slug REQUIRED
    # The POC gateway flaps 502/503 intermittently; one retry clears most transients.
    # ponytail: fixed 1-retry, no exponential backoff — POC-grade; add backoff if it flaps hard.
    last: Exception | None = None
    for attempt in range(2):
        r = requests.post(url, json=payload, headers=_headers(), timeout=timeout)
        if r.status_code in (502, 503, 504):
            last = requests.HTTPError(f"{r.status_code} transient", response=r)
            continue
        r.raise_for_status()
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else {}
    raise last  # both attempts hit a transient gateway error
