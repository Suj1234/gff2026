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
    # MSG91 Flow binds template variables by the variable NAME used in the DLT template
    # (case-sensitive), as top-level keys in each recipient object. Templates vary
    # (##OTP##, ##otp##, ##var##, ##var1##). We populate every common spelling so whichever
    # the template actually uses gets the value — the SMS came through blank because only
    # "OTP" was sent and the template used a different var name.
    # Override via MSG91_OTP_VAR if you know the exact variable name.
    var = os.getenv("MSG91_OTP_VAR")
    recipient = {"mobiles": f"91{mobile}"}
    if var:
        recipient[var] = otp
    else:
        for k in ("OTP", "otp", "var", "var1", "VAR1"):
            recipient[k] = otp
    payload = {"template_id": os.environ["MSG91_TEMPLATE_ID"], "recipients": [recipient]}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        # MSG91 returns 200 with {"type":"success"} on accept; a 200 with type!=success is a fail.
        ok = r.status_code == 200 and '"type":"error"' not in r.text.replace(" ", "")
        return SendResult(sent=ok, http_status=r.status_code,
                          error=None if ok else r.text[:300])
    except requests.RequestException as e:
        return SendResult(sent=False, error=str(e)[:200])
