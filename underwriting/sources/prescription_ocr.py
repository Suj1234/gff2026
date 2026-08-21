"""prescription_ocr.py — Gemini-vision prescription/MER OCR adapter
(HEALTH_AGENT_PLAN.md §2).

The raw extraction comes from `prescription_ocr.extract(path)` at the repo root (a
single synchronous Gemini-vision call — no submit/poll/report vendor flow, unlike
bank_statement.py, because this "vendor" is a direct LLM call, not an async gateway).
This module is the ADAPTER: raw extraction dict -> internal `prescription_ocr` shape
(schemas.PrescriptionOcr). Mock the RESPONSE (a canned extraction dict in tests), never
the step — same discipline as every other adapter in this package (files/CLAUDE.md §3).

Boundary (§1.8): the OCR call transcribes what's on the page — drug names, dosage,
clinical notes verbatim — and is explicitly instructed (prescription_ocr.py's prompt)
NOT to diagnose or infer beyond what's written. This adapter carries those FACTS
through unchanged; the health-agent triage step (journey/health_agent/) is what reasons
over them to flag a condition bucket — never this adapter, never the OCR call itself.

Tolerant of a partial/garbage/failed extraction (§11): a missing field degrades to
absent, never a crash. An OCR call that errors (bad image, gateway down, no API key)
should be caught by the CALLER (the journey route / gatherer) and passed here as `None`
or `{}` — this adapter always returns `unavailable` for that case.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import adapter

log = logging.getLogger("underwriting.sources.prescription_ocr")


def _clean_str(v) -> Optional[str]:
    """A string field -> stripped string, or None if blank/garbage (§11)."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None


def _clean_list(v) -> list[str]:
    """A list-of-strings field, tolerant of a non-list or non-string entries."""
    if not isinstance(v, list):
        return []
    return [s.strip() for s in v if isinstance(s, str) and s.strip()]


def to_prescription_ocr(raw: Optional[dict]) -> dict:
    """Gemini-vision extraction dict -> internal `prescription_ocr` shape
    (schemas.PrescriptionOcr). `raw=None` or `{}` (no upload, or OCR failed upstream)
    -> `status: unavailable` — a partial bundle the engine reasons around, never a crash
    (§11, mirrors nuralx.py's `_is_failure` handling)."""
    if not isinstance(raw, dict) or not raw:
        return {"status": "unavailable"}

    raw_text = _clean_str(raw.get("raw_text"))
    diagnosis = _clean_str(raw.get("diagnosis_notes"))
    drugs_raw = raw.get("drugs")
    drug_names: list[str] = []
    if isinstance(drugs_raw, list):
        for d in drugs_raw:
            if isinstance(d, dict):
                name = _clean_str(d.get("name"))
            elif isinstance(d, str):
                name = _clean_str(d)
            else:
                name = None
            if name:
                drug_names.append(name)

    # A "successful" call that read nothing usable (blank/garbage image) is still a
    # completed OCR attempt, not an unavailable source — surface it as available with
    # empty facts so the caller can see "we tried, nothing legible" rather than
    # confusing it with "never attempted". Mirrors nuralx.py's absent-vitals handling.
    return {
        "status": "available",
        "raw_text": [raw_text] if raw_text else [],
        "drug_names": drug_names,
        "icd_codes": _clean_list(raw.get("icd_codes")),
        "diagnosis_notes": [diagnosis] if diagnosis else [],
    }


@adapter("prescription_ocr")
def from_vendor(raw: dict) -> dict:
    """Registry entry point: Gemini-vision extraction -> the internal
    `prescription_ocr` shape. The seam a raw-ingestion layer calls once OCR completes."""
    return to_prescription_ocr(raw)


# ===========================================================================
# The real gatherer — mirrors bank_statement.py's make_iadore_gatherer shape, but this
# is a synchronous vision-LLM call rather than an async submit/poll/report vendor flow.
# ===========================================================================
PRESCRIPTION_DOC_TYPE = "prescription"


def _prescription_path(inp) -> Optional[str]:
    """The uploaded prescription image/PDF path from `inp.documents` (a doc typed
    `prescription`, with a `path`/`file_path`/`url` field). None if not uploaded."""
    for doc in getattr(inp, "documents", None) or []:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("type") or "").strip().lower() == PRESCRIPTION_DOC_TYPE:
            return doc.get("path") or doc.get("file_path") or doc.get("url")
    return None


def run_ocr_for_signals(inp, *, extract_fn=None) -> dict:
    """Runs prescription OCR (if a document was uploaded) and returns the
    `{"prescription_ocr": {...}}` signal dict ready to merge into `ProposalInput.signals`.

    Inject `extract_fn` (defaults to the shipped repo-root `prescription_ocr.extract`,
    imported lazily so this module stays import-clean without `litellm`/GEMINI_API_KEY
    unless OCR is actually invoked). Fail-safe (§11): no upload, or the OCR call errors
    (bad image, gateway down, no key) -> `unavailable`, never a crash, never a silently
    empty-but-"available" record.
    """
    path = _prescription_path(inp)
    if not path:
        return {"prescription_ocr": {"status": "unavailable"}}
    _extract = extract_fn
    if _extract is None:
        from prescription_ocr import extract as _extract  # repo-root client, lazy import
    try:
        raw = _extract(path)
    except Exception as exc:  # noqa: BLE001 — vendor/gateway failure -> fail safe
        log.warning("prescription OCR failed (%s: %s) -> prescription_ocr unavailable, "
                    "triage reasons around it.", type(exc).__name__, exc)
        return {"prescription_ocr": {"status": "unavailable"}}
    return {"prescription_ocr": to_prescription_ocr(raw)}
