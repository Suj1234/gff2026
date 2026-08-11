"""msg91.py — SMS OTP via the MSG91 Flow API (Python port of the pasted TS contract).

    POST https://control.msg91.com/api/v5/flow
    header: authkey = MSG91_AUTH_KEY ; template = MSG91_TEMPLATE_ID ; mobile prefixed 91.

If either env var is blank -> we DON'T call MSG91; the caller runs the mock path
(the OTP is still generated + hashed + stored; it's only surfaced to the client as
`debug_otp` when UW_DEBUG_OTP=1 — see auth_routes). This module only knows how to
SEND; generation/hashing/storage live in otp.py so the mock and real paths share them.
"""

from __future__ import annotations

import os

import requests


class SendResult:
    def __init__(self, sent: bool, http_status: int | None = None, error: str | None = None):
        self.sent = sent
        self.http_status = http_status
        self.error = error


def creds_present() -> bool:
    return bool(os.getenv("MSG91_AUTH_KEY") and os.getenv("MSG91_TEMPLATE_ID"))


def send_sms_otp(mobile: str, otp: str, *, timeout: int = 15) -> SendResult:
    """Fire the OTP SMS. Returns SendResult(sent=...) — never raises to the caller;
    a failure just means the route falls back to the mock path."""
    if not creds_present():
        return SendResult(sent=False, error="MSG91 creds absent")
    url = "https://control.msg91.com/api/v5/flow"
    headers = {
        "authkey": os.environ["MSG91_AUTH_KEY"],
        "Content-Type": "application/json",
    }
    payload = {
        "template_id": os.environ["MSG91_TEMPLATE_ID"],
        "recipients": [{"mobiles": f"91{mobile}", "OTP": otp}],
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        ok = r.status_code == 200
        return SendResult(sent=ok, http_status=r.status_code,
                          error=None if ok else r.text[:200])
    except requests.RequestException as e:
        return SendResult(sent=False, error=str(e)[:200])
