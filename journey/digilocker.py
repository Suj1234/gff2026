"""digilocker.py — Aadhaar e-KYC via the Perfios DigiLocker API (vendor_apis §6).

Contract VERIFIED live (2026-08-11): x-api-key auth; 3 calls in succession —
  link()      POST /kyc/api/v1/digilocker/link      -> {requestId, result.link}
  documents() POST /kyc/api/v1/digilocker/documents  -> {result: [{uri, doctype, ...}]}
  download()  POST /kyc/api/v1/digilocker/download    -> parsed ADHAR + PANCR data

`requestId` from link() == the `accessRequestId` fed to documents()/download().
Flow: user opens result.link on DigiLocker, grants Aadhaar consent, returns to our
redirectUrl; then we list + download. This module only talks to the vendor; mapping the
parsed Aadhaar -> signals.aadhaar_ekyc happens in the route.
"""

from __future__ import annotations

import os

import requests

_TIMEOUT = 30


def configured() -> bool:
    return bool(os.getenv("DIGILOCKER_BASE_URL") and os.getenv("DIGILOCKER_API_KEY"))


def _url(path: str) -> str:
    return os.environ["DIGILOCKER_BASE_URL"].rstrip("/") + path


def _headers() -> dict[str, str]:
    return {"x-api-key": os.getenv("DIGILOCKER_API_KEY", ""), "Content-Type": "application/json"}


def callback_url() -> str:
    """The DigiLocker return URL. Precedence:
      1. DIGILOCKER_REDIRECT_URL — explicit override. PROD sets this to the exact
         /demo/life/digilocker/callback URL; that value wins so prod is never touched.
      2. UI_ORIGIN — LOCAL dev only: the browser must return to the SAME origin the
         session cookie lives on (the Vite UI, :5173), not the backend (:8899), or the
         cookie isn't sent and the KYC pull loses the session. Vite proxies the callback
         path back to :8899 (vite.config.ts). Prod has ONE origin, so UI_ORIGIN is unset
         there and this branch never runs.
      3. PUBLIC_API_URL — same-origin fallback (also correct in prod's single-origin case).
    """
    explicit = os.getenv("DIGILOCKER_REDIRECT_URL")
    if explicit:
        return explicit
    origin = (os.getenv("UI_ORIGIN") or os.getenv("PUBLIC_API_URL")
              or "http://localhost:5173").rstrip("/")
    return f"{origin}/digilocker/callback"


def link(*, oauth_state: str, case_id: str, redirect_url: str | None = None) -> dict:
    """Call 1 — get the DigiLocker consent link + accessRequestId (returned as requestId)."""
    redirect_url = redirect_url or callback_url()
    body = {
        "redirectUrl": redirect_url,
        "oAuthState": oauth_state,
        "aadhaarFlowRequired": True,
        "pinlessAuth": True,
        "customDocList": "ADHAR,PANCR",
        "consent": "Y",
        "clientData": {"caseId": case_id},
    }
    r = requests.post(_url("/kyc/api/v1/digilocker/link"), json=body, headers=_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def documents(*, access_request_id: str, case_id: str) -> dict:
    """Call 2 — list the docs the user shared (after they grant on DigiLocker)."""
    body = {"accessRequestId": access_request_id, "consent": "Y", "clientData": {"caseId": case_id}}
    r = requests.post(_url("/kyc/api/v1/digilocker/documents"), json=body, headers=_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def download(*, access_request_id: str, uris: list[str], case_id: str) -> dict:
    """Call 3 — pull parsed data for the chosen document URIs (ADHAR + PANCR)."""
    files = [{"uri": u, "pdfB64": False, "parsed": True, "xml": True} for u in uris]
    body = {"accessRequestId": access_request_id, "consent": "Y", "files": files,
            "clientData": {"caseId": case_id}}
    r = requests.post(_url("/kyc/api/v1/digilocker/download"), json=body, headers=_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def parse_aadhaar(download_response: dict) -> dict:
    """Pull the Aadhaar `issuedTo` fields from a download() response into a flat dict:
    {name, dob, gender, address, pincode, photo_present, xml_verified}. Empty if no ADHAR."""
    for item in (download_response.get("result") or []):
        uri = item.get("documentUri", "")
        if "ADHAR" not in uri:
            continue
        pf = item.get("parsedFile") or {}
        data = pf.get("data") or {}
        it = data.get("issuedTo") or {}
        addr = it.get("address") or {}
        return {
            "name": it.get("name"),
            "dob": it.get("dob"),
            "gender": (it.get("gender") or "").lower() or None,
            "pincode": addr.get("pin"),
            "address": ", ".join(
                str(addr.get(f, "")) for f in ("house", "locality", "vtc", "district", "state", "pin")
                if addr.get(f)
            ) or None,
            "photo_present": bool((it.get("photo") or {}).get("content")),
            "xml_verified": bool(pf.get("xmlSignatureVerified")),
        }
    return {}


def parse_pan(download_response: dict) -> dict:
    """Pull PAN number + status from a download() response. Empty if no PANCR."""
    for item in (download_response.get("result") or []):
        if "PANCR" not in item.get("documentUri", ""):
            continue
        data = (item.get("parsedFile") or {}).get("data") or {}
        return {"pan": data.get("number"), "status": data.get("status")}  # status "A" = active
    return {}
