"""mock_abha.py — the mock ABHA (health-records) API for the demo (JOURNEY_PLAN.md §5).

A live applicant's real ABHA record is unavailable on stage, so ABHA is **scripted and
keyed** off the entered PAN/mobile via a small lookup table — specific test identities
return specific records, so the non-disclosure demo feels live in a walk-through.

Mock the RESPONSE, never the step (files/CLAUDE.md §3): the pipeline still goes through
the real consent-gated ABHA request (`request_abha_consent()` in the decision table);
only the RECORD returned here is canned. This module is exactly and only the vendor
response — it returns the fields the engine reads and nothing else:

    schemas.AbhaHealthRecords : {status, diagnoses[], icd_codes[], prescriptions[],
                                 unstructured_notes[]}
    rules.postpone_check      : {days_since_acute_event, active_pregnancy}

It powers R-010 (declared-clean vs ABHA-evidence non-disclosure) and POSTPONE (recent
acute event / active pregnancy). An unknown identity returns a CLEAN record (status
available, empty lists) — an ABHA lookup that found nothing adverse, which is different
from `unavailable` (a lookup that could not run). Pass `status="unavailable"` explicitly
via `records_for(..., found=False)` to model an ABHA outage (§11 partial bundle).
"""

from __future__ import annotations

from typing import Any, Optional

# A CLEAN ABHA record — the common case (nothing adverse on file). Not `unavailable`:
# the lookup ran and returned no conditions (absent ≠ assessed-clean is respected by the
# scorer's own source-status handling; here `available` + empty means "checked, clean").
_CLEAN: dict[str, Any] = {
    "status": "available",
    "diagnoses": [],
    "icd_codes": [],
    "prescriptions": [],
    "unstructured_notes": [],
    "days_since_acute_event": None,
    "active_pregnancy": False,
}


def _record(**overrides) -> dict[str, Any]:
    """A record = the clean baseline with specific fields overridden. Keeps every keyed
    identity to exactly the fields the engine reads."""
    return {**_CLEAN, **overrides}


# ---------------------------------------------------------------------------
# ABHA-ID keying (journey UI, step_routes.py): the journey's ABHA-link step keys the
# record off the ABHA NUMBER the applicant enters — never off their login mobile/PAN.
# One memorable demo ABHA number returns a multi-condition record; every other ABHA
# number (real or made up) is clean. This is intentionally separate from `_BY_IDENTITY`
# below (PAN/mobile keying), which several engine fixtures/tests still depend on.
# ---------------------------------------------------------------------------
_SICK_ABHA_ID = "99999999999999"  # 99-9999-9999-9999 — the one demo "sick" ABHA number

_ABHA_ID_SICK_RECORD: dict[str, Any] = {
    "icd_codes": ["E11.9", "I25.10", "I10"],  # diabetes, ischaemic heart disease, hypertension
    "diagnoses": ["E11.9", "I25.10", "I10"],
    "prescriptions": ["metformin", "atorvastatin", "amlodipine"],
    "unstructured_notes": [
        "Pt reviewed in cardiology OPD; advised to continue anti-anginal therapy and "
        "monitor blood sugar. Known diabetic on oral hypoglycaemics, hypertensive on "
        "amlodipine.",
    ],
}


def _normalize_abha_id(abha_id: Optional[str]) -> str:
    """A 14-digit ABHA number normalizes to just its digits; an ABHA address (contains
    '@') normalizes lowercased/stripped. Either way, formatting (dashes/spaces) never
    changes the lookup key."""
    s = (abha_id or "").strip()
    if "@" in s:
        return s.lower()
    return "".join(ch for ch in s if ch.isdigit())


def records_for_abha_id(abha_id: Optional[str]) -> dict[str, Any]:
    """The ABHA record for an ABHA number/address, keyed ONLY off that id — never off
    the applicant's mobile/PAN. The one demo sick id returns a multi-condition record;
    any other id (unknown, made up, real-shaped) returns clean."""
    if _normalize_abha_id(abha_id) == _SICK_ABHA_ID:
        return _record(**_ABHA_ID_SICK_RECORD)
    return dict(_CLEAN)


# ---------------------------------------------------------------------------
# The keyed lookup. Keys are the entered PAN (uppercased) or mobile (digits only).
# Real demo identities from docs/vendor_apis.md §1 are reused so the ABHA record lines
# up with the live identity/litigation half of the journey.
#
# NOTE: this PAN/mobile keying stays for the underwriting engine's own fixtures/tests
# (test_phase_b.py) which exercise R-010/POSTPONE against these PANs directly. The
# journey UI's ABHA-link step (step_routes.py) does NOT use this — it uses
# `records_for_abha_id` above, keyed only by the ABHA number the applicant enters.
# ---------------------------------------------------------------------------
_BY_IDENTITY: dict[str, dict[str, Any]] = {
    # Paulson (self-employed, high litigation) — undisclosed diabetes + cardiac. The
    # headline R-010 non-disclosure case. Its STRUCTURED evidence (ICD codes + drugs)
    # fires R-010 on the deterministic path (no LLM); the free-text note ALSO carries the
    # same conditions, so with an extractor the messy-ABHA path corroborates it — but the
    # structured path alone is sufficient here (see MESSY01A for the free-text-ONLY case).
    "BHYPM4927Q": _record(
        icd_codes=["E11.9", "I25.10"],                 # type-2 diabetes, ischaemic heart disease
        diagnoses=["E11.9", "I25.10"],
        prescriptions=["metformin", "atorvastatin"],   # diabetes + dyslipidemia drugs
        unstructured_notes=[
            "Pt reviewed in cardiology OPD; advised to continue anti-anginal therapy "
            "and monitor blood sugar. Known diabetic on oral hypoglycaemics.",
        ],
    ),
    "9739780007": None,  # Paulson's mobile → same record as his PAN (resolved below)

    # Sabarish (salaried, clean) — genuinely clean ABHA, backs an ISSUE demo.
    "EKOPS9572K": _record(),
    "8884609090": _record(),

    # Messy-ABHA demo (§4.2): the ONLY evidence is a free-text note — no ICD codes, no
    # coded diagnoses, no drugs. R-010 is silent on the deterministic path and fires ONLY
    # when the LLM `extract_condition` reads the note. Exercises the free-text path end
    # to end (the path BHYPM4927Q's structured evidence would otherwise mask).
    "MESSY01A": _record(
        unstructured_notes=[
            "Discharge summary (scanned): patient admitted with chest pain, diagnosed "
            "coronary artery disease, started on anti-anginals. Advised cardiology review.",
        ],
    ),

    # A POSTPONE demo identity — recent acute event inside the 90-day window.
    "POSTPONE01A": _record(
        icd_codes=["S72.0"],  # fracture of neck of femur — recent surgery
        diagnoses=["S72.0"],
        days_since_acute_event=21,
    ),
    # A pregnancy POSTPONE demo identity.
    "PREGNANT01A": _record(active_pregnancy=True),
}
# Paulson's mobile resolves to his PAN record (one person, two identity keys).
_BY_IDENTITY["9739780007"] = _BY_IDENTITY["BHYPM4927Q"]


def _key(identity: Optional[str]) -> Optional[str]:
    """Normalize an entered PAN or mobile to a lookup key. PAN → uppercased; a mobile →
    digits only (strips +91 / spaces / dashes so `+91 97397 80007` matches `9739780007`)."""
    if not identity:
        return None
    s = str(identity).strip()
    if s.isdigit() or any(ch.isdigit() for ch in s):
        digits = "".join(ch for ch in s if ch.isdigit())
        # A 12-digit +91-prefixed mobile → last 10 digits.
        if len(digits) > 10:
            digits = digits[-10:]
        if len(digits) == 10:
            return digits
    return s.upper()


def records_for(
    pan: Optional[str] = None, mobile: Optional[str] = None, *, found: bool = True
) -> dict[str, Any]:
    """The ABHA record for an identity, keyed off PAN then mobile.

    - A keyed identity returns its scripted record.
    - An unknown identity returns a CLEAN record (lookup ran, nothing adverse).
    - `found=False` models an ABHA outage → `status: "unavailable"` (§11 partial bundle,
      the engine reasons around it, never a crash).
    """
    if not found:
        return {"status": "unavailable"}
    for ident in (pan, mobile):
        rec = _BY_IDENTITY.get(_key(ident) or "")
        if rec is not None:
            return dict(rec)  # copy — callers must not mutate the table
    return dict(_CLEAN)


def known_identities() -> list[str]:
    """The keyed demo identities (for a scenario picker / tests)."""
    return sorted(_BY_IDENTITY)
