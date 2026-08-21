# prescription_ocr.py — Standalone Gemini-vision prescription/MER OCR client.
# Read a prescription image/PDF page -> structured extraction (drug names, dosage,
# diagnosis notes, doctor/clinic details) via Gemini vision. No submit/poll/report vendor
# flow (unlike bank_statement.py) — this is a single synchronous vision-LLM call, so the
# "vendor" here is the Gemini API itself, called directly with litellm.
#
# Usage (CLI):   python prescription_ocr.py path/to/prescription.png
# Usage (code):  from prescription_ocr import extract; result = extract("prescription.png")
#
# Model choice (HEALTH_AGENT_PLAN.md §2, live-verified 2026-08-21): gemini-2.5-flash —
# tested against gemini-3.5-flash-lite, gemini-3.5-flash, gemini-2.5-flash-lite,
# gemini-2.5-pro; all authenticate and respond with this key. 2.5-flash is the default
# for accuracy on messy/handwritten Indian prescriptions at still-cheap pricing; override
# via PRESCRIPTION_OCR_MODEL if a different tier is preferred.
from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Optional


# --- tiny .env loader (same pattern as bank_statement.py / agent.py) ---
def _load_env() -> None:
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

DEFAULT_MODEL = os.environ.get("PRESCRIPTION_OCR_MODEL", "gemini/gemini-2.5-flash")
TIMEOUT_S = 60  # fail fast, same discipline as judge.py's LLM_TIMEOUT_S

_EXTRACTION_PROMPT = """You are extracting structured facts from a photo/scan of an Indian
medical prescription for insurance underwriting. Extract ONLY what is legibly written on
the document — never guess, infer, or complete a drug name/dosage you cannot actually
read. If a field is illegible or absent, use null / an empty list.

Do NOT diagnose, do NOT infer a condition beyond what the document states, do NOT add
clinical opinion. This is transcription, not interpretation.

Return ONLY a JSON object with this exact shape, no other text:
{
  "clinic_or_doctor": "string or null — clinic/doctor name if visible",
  "patient_name": "string or null",
  "date": "string or null — as written on the document",
  "diagnosis_notes": "string or null — any clinical/diagnosis note text, verbatim",
  "drugs": [
    {"name": "string — drug name + strength as written", "dosage": "string or null",
     "duration": "string or null"}
  ],
  "raw_text": "string — best-effort full transcription of all visible text on the page"
}"""


def _b64_image(path: str) -> tuple[str, str]:
    p = Path(path)
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "image/png"
    return base64.b64encode(p.read_bytes()).decode(), mime


def extract(file_path: str, *, model: Optional[str] = None) -> dict:
    """One prescription image -> structured extraction dict (see _EXTRACTION_PROMPT for
    shape). Raises on a missing GEMINI_API_KEY or an unreachable gateway (fail fast, same
    as judge.py) — the caller (the adapter / journey route) is responsible for catching
    and degrading to `status: "unavailable"` (§11 partial-bundle discipline), never a crash
    surfaced to the applicant.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    import litellm  # lazy import: this module stays import-clean without litellm installed

    b64, mime = _b64_image(file_path)
    resp = litellm.completion(
        model=model or DEFAULT_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _EXTRACTION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        timeout=TIMEOUT_S,
    )
    raw = resp.choices[0].message.content or "{}"
    # Gemini sometimes wraps JSON in ```json fences despite the "ONLY JSON" instruction —
    # strip them defensively rather than trust prompt compliance.
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        # Model didn't return clean JSON — fail safe to an empty-but-valid shape rather
        # than crash; raw_text carries whatever it said, for a human to inspect.
        data = {"clinic_or_doctor": None, "patient_name": None, "date": None,
                 "diagnosis_notes": None, "drugs": [], "raw_text": raw}
    return data


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python prescription_ocr.py path/to/prescription.png")
        sys.exit(1)
    print(json.dumps(extract(sys.argv[1]), indent=2))
