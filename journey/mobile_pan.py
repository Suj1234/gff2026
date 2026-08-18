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


def _headers(key_env: str = "MOBILE_PAN_API_KEY") -> dict[str, str]:
    return {
        "x-api-key": os.getenv(key_env, ""),
        "Content-Type": "application/json",
    }


def _post_prefill(path: str, payload: dict, timeout: int, *,
                  base_env: str = "MOBILE_PAN_BASE_URL", key_env: str = "MOBILE_PAN_API_KEY") -> dict:
    """POST a prefill payload -> the vendor `data` block. Retries the flaky POC gateway's
    transient 5xx once (see ponytail note); raises on a hard error / both-attempts-transient.
    base_env/key_env let the PAN-prefill flow use its own PAN_PREFILL_* creds (§ flow B)."""
    base, key = os.getenv(base_env), os.getenv(key_env)
    if not (base and key):
        raise RuntimeError(f"prefill not configured ({base_env}/{key_env} absent)")
    url = f"{base.rstrip('/')}{path}"
    # The POC gateway flaps 502/503 intermittently; one retry clears most transients.
    # ponytail: fixed 1-retry, no exponential backoff — POC-grade; add backoff if it flaps hard.
    last: Exception | None = None
    for _ in range(2):
        r = requests.post(url, json=payload, headers=_headers(key_env), timeout=timeout)
        if r.status_code in (502, 503, 504):
            last = requests.HTTPError(f"{r.status_code} transient", response=r)
            continue
        r.raise_for_status()
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else {}
    raise last  # both attempts hit a transient gateway error


def fetch_profile(mobile: str, *, insurer_slug: str = "acme", timeout: int = 90) -> dict:
    """POST the mobile number -> the vendor `data` block (vendor_apis §1 response).
    The include* flags are REQUIRED to get litigation / GST / director back — without them
    the vendor returns identity + mobileIntelligence only (litigation would stay empty)."""
    path = os.getenv("MOBILE_PAN_ENDPOINT", "/api/external/prefill/mobile")
    # The `*alerts/charges` flags are REQUIRED to unlock the detail: without
    # `SoleProprietoralerts/charges` the vendor returns `soleProprietor: null` (GST/business
    # empty) even with includeSoleProprietordetails=true — verified live. Match Postman exactly.
    payload = {
        "mobile": mobile, "insurer_slug": insurer_slug,
        "includeLitigationdetails": True,
        "includeSoleProprietordetails": True,
        "SoleProprietoralerts/charges": True,
        "includeDirectordetails": True,
        "Directoralerts/charges": True,
    }
    return _post_prefill(path, payload, timeout)


def fetch_by_pan(pan: str, *, insurer_slug: str = "acme", timeout: int = 90) -> dict:
    """POST a PAN -> the SAME `data` block minus mobileIntelligence (vendor_apis §2).
    Used as the fallback (flow B) when the mobile lookup returns no PAN. Uses the
    PAN_PREFILL_* creds (its own key/base/endpoint — may be a different vendor than the
    mobile flow), falling back to the mobile-flow gateway when those aren't set."""
    path = os.getenv("PAN_PREFILL_ENDPOINT") or os.getenv(
        "MOBILE_PAN_PAN_ENDPOINT", "/api/external/prefill/pan")
    payload = {
        "pan": pan.strip().upper(), "insurer_slug": insurer_slug,
        "includeLitigationdetails": True,
        "includeSoleProprietordetails": True,
        "SoleProprietoralerts/charges": True,   # required to unlock soleProprietor detail
        "includeDirectordetails": True,
        "Directoralerts/charges": True,
    }
    # Prefer the dedicated PAN_PREFILL_* gateway; fall back to MOBILE_PAN_* if unset.
    base_env = "PAN_PREFILL_BASE_URL" if os.getenv("PAN_PREFILL_BASE_URL") else "MOBILE_PAN_BASE_URL"
    key_env = "PAN_PREFILL_API_KEY" if os.getenv("PAN_PREFILL_API_KEY") else "MOBILE_PAN_API_KEY"
    return _post_prefill(path, payload, timeout, base_env=base_env, key_env=key_env)
