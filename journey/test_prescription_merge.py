"""test_prescription_merge.py — multiple-prescription-upload merge logic
(`_merge_prescription_ocr` in step_routes.py). Pure function, no HTTP/DB/network needed.
"""
from __future__ import annotations

from journey.step_routes import _merge_prescription_ocr


def test_first_upload_with_no_existing_record_is_used_as_is():
    new = {"status": "available", "raw_text": ["a"], "drug_names": ["Metformin"],
           "icd_codes": ["E11.9"], "diagnosis_notes": ["diabetes"]}
    assert _merge_prescription_ocr({}, new) == {**new, "uploads": 1}
    assert _merge_prescription_ocr({"status": "unavailable"}, new) == {**new, "uploads": 1}


def test_second_upload_concatenates_and_dedupes():
    first = {"status": "available", "raw_text": ["page1"], "drug_names": ["Metformin"],
             "icd_codes": ["E11.9"], "diagnosis_notes": ["diabetes note"], "uploads": 1}
    second = {"status": "available", "raw_text": ["page2"],
              "drug_names": ["Metformin", "Atorvastatin"],  # Metformin repeats -> dedup
              "icd_codes": ["I25.10"], "diagnosis_notes": ["cardiac note"]}
    merged = _merge_prescription_ocr(first, second)
    assert merged["raw_text"] == ["page1", "page2"]
    assert merged["drug_names"] == ["Metformin", "Atorvastatin"]
    assert merged["icd_codes"] == ["E11.9", "I25.10"]
    assert merged["diagnosis_notes"] == ["diabetes note", "cardiac note"]
    assert merged["status"] == "available"
    assert merged["uploads"] == 2


def test_a_later_failed_upload_does_not_erase_prior_successful_ones():
    first = {"status": "available", "raw_text": ["page1"], "drug_names": ["Metformin"],
             "icd_codes": [], "diagnosis_notes": [], "uploads": 1}
    failed = {"status": "unavailable"}
    assert _merge_prescription_ocr(first, failed) == first


def test_uploads_counter_increments_even_on_a_blank_extraction():
    """The bug this guards: a THIRD upload that legitimately reads zero new drugs (an
    unreadable image, or one repeating an already-known drug) must still be distinguishable
    from "hasn't landed yet" — the journey UI polls on `uploads`, not on drug_names growing,
    to avoid misreporting a real zero-drug result as a timeout (or misreading a stale
    still-available snapshot as this upload's result)."""
    first = {"status": "available", "raw_text": ["page1"], "drug_names": ["Metformin"],
             "icd_codes": [], "diagnosis_notes": [], "uploads": 1}
    blank = {"status": "available", "raw_text": [], "drug_names": [],
             "icd_codes": [], "diagnosis_notes": []}
    merged = _merge_prescription_ocr(first, blank)
    assert merged["drug_names"] == ["Metformin"]  # unchanged — nothing new extracted
    assert merged["uploads"] == 2                  # but the attempt still counted
