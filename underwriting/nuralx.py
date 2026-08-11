"""nuralx.py — NuralX face-vitals scan integration (ported from the Kenya Life platform).

NuralX is a 4-step async flow, not a single request:

    1. POST {base_url}generate-credentials   → client_id / client_secret   (auth: email+password)
    2. POST {base_url}token                   → access_token                (auth: client_id/secret)
    3. POST {base_url}patient-data            → scan_access_url             (auth: the token, as-is)
    4. NuralX POSTs the vitals to your call_back_URL (webhook — see api additions)

Env vars (see .env additions):
    NURALX_BASE_URL          MUST end with '/'
    NURALX_EMAIL
    NURALX_PASSWORD
    NURALX_CALLBACK_SECRET   any random string; NuralX echoes it back as ?key=<secret>
    PUBLIC_API_URL           public base URL of THIS service, used to build the callback URL

Three gotchas baked in (do not "fix" them):
    1. base_url must end with '/', endpoints are concatenated directly.
    2. the token's access_token already includes the literal "Bearer " prefix — sent as-is
       in a LOWERCASE `authorization` header. Do not prepend "Bearer" again.
    3. the webhook body may arrive with an off Content-Type — read the raw bytes and parse
       JSON yourself, never rely on framework auto-parsing.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

# ── Module-level token cache — one process serves many requests; avoids 2 round-trips
#    per scan. Fine for a single instance; each instance caches independently.
_cred_cache: dict[str, Any] | None = None   # {client_id, client_secret, expires_at}
_token_cache: dict[str, Any] | None = None  # {token, expires_at}


@dataclass
class NuralXCredentials:
    base_url: str      # MUST end with '/'
    email: str
    password: str
    callback_url: str  # NuralX POSTs vitals here; must include your ?key=<secret>


@dataclass
class Patient:
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None


@dataclass
class ScanResponse:
    scan_id: str
    scan_url: str  # open this URL on the scan device / send to the customer
    source: str = "REAL"


def creds_from_env() -> NuralXCredentials:
    """Assemble credentials + the webhook callback URL from environment variables."""
    public = (os.environ.get("PUBLIC_API_URL") or "").rstrip("/")
    secret = os.environ.get("NURALX_CALLBACK_SECRET", "")
    return NuralXCredentials(
        base_url=os.environ["NURALX_BASE_URL"],  # must end with '/'
        email=os.environ["NURALX_EMAIL"],
        password=os.environ["NURALX_PASSWORD"],
        callback_url=f"{public}/nuralx/callback?key={secret}",
    )


def _generate_client_credentials(creds: NuralXCredentials) -> tuple[str, str]:
    r = httpx.post(
        f"{creds.base_url}generate-credentials",
        json={"email": creds.email, "password": creds.password},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"NuralX generate-credentials failed ({r.status_code}): {r.text}")
    data = r.json()["data"]
    return data["client_id"], data["client_secret"]


def _generate_access_token(base_url: str, client_id: str, client_secret: str) -> tuple[str, int]:
    r = httpx.post(
        f"{base_url}token",
        json={"client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"NuralX token failed ({r.status_code}): {r.text}")
    data = r.json()
    # access_token already includes the literal "Bearer " prefix — pass it through as-is.
    expires_in = int(data.get("expires_in") or 3600)
    return data["access_token"], expires_in


def _add_patient_data(
    base_url: str, access_token: str, session_token: str, patient: Patient, callback_url: str
) -> tuple[str, str]:
    r = httpx.post(
        f"{base_url}patient-data",
        # NuralX expects a LOWERCASE `authorization` header — confirmed intentional.
        headers={"Content-Type": "application/json", "authorization": access_token},
        json={
            "name": patient.name,
            "email": patient.email,
            "phone": patient.phone,
            "client_transaction_ID": session_token,
            "call_back_URL": callback_url,
        },
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"NuralX patient-data failed ({r.status_code}): {r.text}")
    data = r.json()["data"]
    scan_id = (
        (data.get("license_usage") or {}).get("id")
        or (data.get("device_user") or {}).get("id")
        or session_token
    )
    return scan_id, data["scan_access_url"]


def initiate_scan(creds: NuralXCredentials, session_token: str, patient: Patient) -> ScanResponse:
    """Authenticate (cached) and register a patient. Returns the scan URL to open."""
    global _cred_cache, _token_cache
    now = time.time()
    buffer = 60  # seconds

    if _cred_cache is None or _cred_cache["expires_at"] <= now + buffer:
        cid, csec = _generate_client_credentials(creds)
        _cred_cache = {"client_id": cid, "client_secret": csec, "expires_at": now + 3600}

    if _token_cache is None or _token_cache["expires_at"] <= now + buffer:
        token, ttl = _generate_access_token(
            creds.base_url, _cred_cache["client_id"], _cred_cache["client_secret"]
        )
        _token_cache = {"token": token, "expires_at": now + ttl}

    scan_id, scan_url = _add_patient_data(
        creds.base_url, _token_cache["token"], session_token, patient, creds.callback_url
    )
    return ScanResponse(scan_id=scan_id, scan_url=scan_url, source="REAL")


# ── Webhook result mapping ───────────────────────────────────────────────────
# Some fields arrive wrapped as {"value": ..., "confidenceLevel": ...}, others as plain
# numbers. NuralX can also add/omit fields per scan.
def _raw_val(field: Any) -> Any:
    if field is None:
        return None
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def map_callback_vitals(results: dict[str, Any]) -> dict[str, Any]:
    """Flatten the webhook `results` object into core vitals. Keep `results` itself too
    if you want every other parameter (HRV, SDNN, SpO2, risk scores)."""
    bp = _raw_val(results.get("bloodPressure")) or {}
    return {
        "heartRate": _raw_val(results.get("pulseRate")),
        "respiratoryRate": _raw_val(results.get("respirationRate")),
        "bloodPressureSystolic": bp.get("systolic") if isinstance(bp, dict) else None,
        "bloodPressureDiastolic": bp.get("diastolic") if isinstance(bp, dict) else None,
        "stressIndex": _raw_val(results.get("stressIndex")),
        "wellnessIndex": _raw_val(results.get("wellnessIndex")),
    }
