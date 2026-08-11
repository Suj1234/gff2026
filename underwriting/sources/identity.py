"""identity.py — PAN identity vendor adapter (internal source key: `pan_verify`).

Maps a real PAN-verification vendor's raw response (shape modeled on the common
Indian KYC gateways — Karza / Signzy / IDfy style: a `result` envelope, `panStatus`
as a text label, camelCase fields) to the internal `PanVerify` contract shape
(schemas.py) the BRE reads for R-002.

Only the mapping is here; the raw bytes are the vendor's (mocked in dev via a canned
payload — see test_sources.py). Choosing a different PAN vendor = a different adapter
registered under the same `pan_verify` key; nothing downstream changes.
"""

from __future__ import annotations

from . import adapter

# The vendor labels its PAN validity in free text; normalize to the internal
# "valid"/"invalid" fact R-002 compares. Unknown label → "invalid" (fail safe:
# never treat an unrecognized status as a valid identity).
_PAN_STATUS = {
    "VALID": "valid",
    "EXISTING AND VALID": "valid",
    "ACTIVE": "valid",
    "INVALID": "invalid",
    "NOT FOUND": "invalid",
    "DEACTIVATED": "invalid",
    "FAKE": "invalid",
}


@adapter("pan_verify")
def from_vendor(raw: dict) -> dict:
    """Vendor raw → internal PanVerify shape. Tolerant of missing keys (partial
    responses are normal, §11): a field the vendor omitted maps to absent, not a crash."""
    r = raw.get("result", raw)  # vendors wrap the payload in `result`; tolerate both
    status_label = str(r.get("panStatus") or r.get("status") or "").strip().upper()
    return {
        "status": "available" if r else "unavailable",
        "pan": r.get("pan") or r.get("panNumber"),
        "pan_status": _PAN_STATUS.get(status_label, "invalid" if status_label else None),
        "name": r.get("fullName") or r.get("name"),
        "dob": r.get("dob") or r.get("dateOfBirth"),
        "gender": r.get("gender"),
        "masked_aadhaar": r.get("maskedAadhaar") or r.get("aadhaarLast4"),
        "aadhaar_seeded": r.get("aadhaarSeedingStatus") == "SEEDED"
        if "aadhaarSeedingStatus" in r else r.get("aadhaar_seeded"),
        "address": r.get("address"),
        "mobile_on_record": r.get("mobile") or r.get("mobileOnRecord"),
        "email_on_record": r.get("email") or r.get("emailOnRecord"),
    }
