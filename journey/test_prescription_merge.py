"""test_prescription_merge.py — multiple-prescription-upload merge logic
(`_merge_prescription_ocr` in step_routes.py). Pure function, no HTTP/DB/network needed.
"""
from __future__ import annotations

from journey.step_routes import _merge_prescription_ocr


def test_first_upload_with_no_existing_record_is_used_as_is():
    new = {"status": "available", "raw_text": ["a"], "drug_names": ["Metformin"],
           "icd_codes": ["E11.9"], "diagnosis_notes": ["diabetes"]}
    assert _merge_prescription_ocr({}, new) == new
    assert _merge_prescription_ocr({"status": "unavailable"}, new) == new


def test_second_upload_concatenates_and_dedupes():
    first = {"status": "available", "raw_text": ["page1"], "drug_names": ["Metformin"],
             "icd_codes": ["E11.9"], "diagnosis_notes": ["diabetes note"]}
    second = {"status": "available", "raw_text": ["page2"],
              "drug_names": ["Metformin", "Atorvastatin"],  # Metformin repeats -> dedup
              "icd_codes": ["I25.10"], "diagnosis_notes": ["cardiac note"]}
    merged = _merge_prescription_ocr(first, second)
    assert merged["raw_text"] == ["page1", "page2"]
    assert merged["drug_names"] == ["Metformin", "Atorvastatin"]
    assert merged["icd_codes"] == ["E11.9", "I25.10"]
    assert merged["diagnosis_notes"] == ["diabetes note", "cardiac note"]
    assert merged["status"] == "available"


def test_a_later_failed_upload_does_not_erase_prior_successful_ones():
    first = {"status": "available", "raw_text": ["page1"], "drug_names": ["Metformin"],
             "icd_codes": [], "diagnosis_notes": []}
    failed = {"status": "unavailable"}
    assert _merge_prescription_ocr(first, failed) == first
