# bank_statement.py — Standalone iAdore bank-statement analysis.
# Submit a bank statement PDF -> poll until done -> return the JSON analysis report.
# No Minio, no external app config: everything is read from .env (see keys below).
#
# Usage (CLI):   python bank_statement.py path/to/statement.pdf
# Usage (code):  from bank_statement import analyze; report = analyze("statement.pdf")
import base64
import hashlib
import hmac
import os
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# --- tiny .env loader (same pattern as agent.py) ---
def _load_env():
    p = Path(__file__).parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# --- config (read from .env; the IADORE_* keys) ---
BASE_URL         = os.environ["IADORE_BASE_URL"]
ORG              = os.environ["IADORE_ORG"]
ORGANISATION_KEY = os.environ["IADORE_ORGANISATION_KEY"]   # HMAC key
PASSPHRASE       = os.environ["IADORE_PASSPHRASE"]
HMAC_PREFIX      = "PERFIOS-HMACSHA256 "
X_SECURE_ID      = os.environ["IADORE_X_SECURE_ID"]
X_SECURE_CRED    = os.environ["IADORE_X_SECURE_CRED"]
X_ORG_ID_HEADER  = os.environ["IADORE_X_ORG_ID"]
CALLBACK_URL     = os.environ.get("IADORE_CALLBACK_URL", "https://webhook.site/placeholder")

SUBMIT_TIMEOUT = 120
POLL_TIMEOUT   = 30
POLL_MAX       = 60      # attempts
POLL_INTERVAL  = 3       # seconds -> ~3 minutes total


# --- auth: Base64( HMAC_SHA256( key=ORGANISATION_KEY, msg="PERFIOS-HMACSHA256 "+PASSPHRASE ) ) ---
def _signature() -> str:
    digest = hmac.new(
        ORGANISATION_KEY.encode(),
        (HMAC_PREFIX + PASSPHRASE).encode(),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode()


def _headers() -> dict:
    return {
        "accept":            "application/json",
        "signature":         _signature(),
        "x-secure-cred":     X_SECURE_CRED,
        "x-secure-id":       X_SECURE_ID,
        "x-organization-id": X_ORG_ID_HEADER,
    }


# --- Step 1: submit the bank statement file ---
def submit(file_path: str, client_txn_id: str = "bankstmt-001") -> dict:
    p = Path(file_path)
    mime = "application/pdf" if p.suffix.lower() == ".pdf" else "image/jpeg"
    multipart = {
        "clientTransactionId": (None, client_txn_id),
        "callbackUrl":         (None, CALLBACK_URL),
        "processType":         (None, "FINANCIAL"),
        "bank":                (p.name, p.read_bytes(), mime),   # field name MUST be "bank"
    }
    url = f"{BASE_URL}/api/v1/iadore/{ORG}/consolidatedProcess"
    r = requests.post(url, headers=_headers(), files=multipart,
                      timeout=SUBMIT_TIMEOUT, verify=False)
    r.raise_for_status()
    return r.json()


def _tx_id(resp: dict):
    for k in ("perfiosTransactionId", "transactionId", "txnId", "txId", "id", "jobId"):
        if resp.get(k):
            return resp[k]
    data = resp.get("data") or {}
    for k in ("perfiosTransactionId", "transactionId", "txnId"):
        if data.get(k):
            return data[k]
    return None


# --- Step 2: poll status until terminal ---
def poll(tx_id: str) -> dict:
    url = f"{BASE_URL}/api/v1/iadore/{ORG}/{tx_id}/status"
    terminal = {"COMPLETED", "SUCCESS", "ERROR", "ERROR_SYSTEM", "FAILED"}
    for _ in range(POLL_MAX):
        r = requests.get(url, headers=_headers(), timeout=POLL_TIMEOUT, verify=False)
        r.raise_for_status()
        data = r.json()
        if data.get("status") in terminal:
            return data
        time.sleep(POLL_INTERVAL)
    return {"status": "TIMEOUT", "tx_id": tx_id}


# --- Step 3: fetch the JSON analysis report (no Minio) ---
def report(tx_id: str) -> dict:
    # NOTE: path order differs from submit/poll — "iadore" comes BEFORE "api/v1" here.
    url = f"{BASE_URL}/iadore/api/v1/{ORG}/{tx_id}/json/report"
    r = requests.get(url, headers=_headers(), timeout=SUBMIT_TIMEOUT, verify=False)
    r.raise_for_status()
    return r.json()


# --- full flow: submit -> poll -> report ---
def analyze(file_path: str) -> dict:
    submit_resp = submit(file_path)
    tx_id = _tx_id(submit_resp)
    if not tx_id:
        raise RuntimeError(f"No transaction id in submit response: {submit_resp}")
    status = poll(tx_id)
    if status.get("status") not in ("COMPLETED", "SUCCESS"):
        raise RuntimeError(f"Analysis ended with status: {status.get('status')}")
    return report(tx_id)


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: python bank_statement.py path/to/statement.pdf")
        sys.exit(1)
    print(json.dumps(analyze(sys.argv[1]), indent=2))
