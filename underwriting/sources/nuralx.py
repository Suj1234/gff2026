"""nuralx.py — NuralX face-scan webhook adapter (docs/vendor_apis.md §5).

ONE async webhook body fans out into THREE internal signal shapes:

  1. `signals.liveness_facematch` — {liveness_pass, liveness_score, face_match_score,
     deepfake_flag}. The **R-003 DECLINE hard gate** (rules.r003_identity_fraud).
  2. `signals.rppg_scan.vitals` — {heart_rate, respiratory_rate, spo2, bp}. The R-017
     step-up trigger (rules.r017_rppg reads heart_rate / respiratory_rate / spo2).
  3. `signals.facial_bmi_smoking` — {bmi_estimate, smoking_estimate}. Lifestyle only —
     per the NuralX disclaimer, wellness estimates feed step-up triage, never a
     standalone loading/decline.

The webhook `results` object is inconsistent (docs §5 gotcha 6): some fields arrive as
`{value, confidenceLevel}`, some as plain numbers, BP is nested `{systolic, diastolic}`.
`_val` unwraps all three.

Unreachable / failure webhook (`status: "error"|"timeout"`, or no `results`): every
signal comes back `status: "unavailable"` (§11) — a partial bundle the engine reasons
around, NEVER a crash. Vitals are AGENT-ONLY (JOURNEY_PLAN.md §8).

Boundary (§1.8): NuralX may attach its own wellness/risk verdicts (stressIndex,
wellnessIndex labels); those are FACTS we may carry but not decisions — the liveness
pass/fail booleans and the deepfake flag are the deepfake DETECTOR's facts (R-003 reads
them), while the underwriting judgment stays ours.
"""

from __future__ import annotations

from typing import Any, Optional

from . import adapter


def _val(field: Any) -> Any:
    """Unwrap a NuralX vitals field. `{value: X, confidenceLevel: ...}` → X; a plain
    value → itself; None → None (docs §5 gotcha 6)."""
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def _num(v) -> Optional[float]:
    """A numeric vital → float; garbage / bool → None (never a crash, §11)."""
    v = _val(v)
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _is_failure(body: dict) -> bool:
    """A failure/timeout webhook or one with no results → no signal (docs §5)."""
    if not isinstance(body, dict):
        return True
    if str(body.get("status") or "").lower() in ("error", "timeout", "failed"):
        return True
    return not body.get("results")


def _results(body: dict) -> dict:
    r = body.get("results")
    return r if isinstance(r, dict) else {}


def to_rppg_scan(body: dict) -> dict:
    """Webhook body → internal `rppg_scan` shape (schemas.RppgScan). Vitals keyed
    heart_rate / respiratory_rate / spo2 / bp — the exact keys R-017 reads."""
    if _is_failure(body):
        return {"status": "unavailable"}
    r = _results(body)
    bp = _val(r.get("bloodPressure"))
    bp_out = None
    if isinstance(bp, dict):
        sys, dia = _num(bp.get("systolic")), _num(bp.get("diastolic"))
        if sys is not None or dia is not None:
            bp_out = {"systolic": sys, "diastolic": dia}
    vitals = {
        "heart_rate": _num(r.get("pulseRate")),
        "respiratory_rate": _num(r.get("respirationRate")),
        "spo2": _num(r.get("oxygenSaturation")),
        "bp": bp_out,
    }
    # Drop vitals the scan didn't return, so absent ≠ a spurious out-of-range reading.
    vitals = {k: v for k, v in vitals.items() if v is not None}
    return {
        "status": "available",
        "consented": True,  # a returned scan implies the applicant ran it
        "vitals": vitals,
    }


def to_liveness_facematch(body: dict) -> dict:
    """Webhook body → internal `liveness_facematch` shape (R-003 hard gate). NuralX
    attaches liveness/deepfake/facematch on the scan result when its liveness module is
    enabled; a vitals-only scan leaves them absent (R-003 no-ops on an absent source).
    """
    if _is_failure(body):
        return {"status": "unavailable"}
    r = _results(body)
    liveness = _val(r.get("liveness"))
    lv = liveness if isinstance(liveness, dict) else {}

    def pick(*keys):
        for src in (r, lv):
            for k in keys:
                if k in src and src[k] is not None:
                    return src[k]
        return None

    liveness_pass = pick("livenessPass", "isLive", "liveness_pass")
    deepfake_flag = pick("deepfakeFlag", "isDeepfake", "deepfake_flag")
    return {
        "status": "available",
        "liveness_pass": bool(liveness_pass) if liveness_pass is not None else None,
        "liveness_score": _num(pick("livenessScore", "liveness_score")),
        "face_match_score": _num(pick("faceMatchScore", "matchScore", "face_match_score")),
        "deepfake_flag": bool(deepfake_flag) if deepfake_flag is not None else None,
    }


def to_facial_bmi_smoking(body: dict) -> dict:
    """Webhook body → internal `facial_bmi_smoking` shape (lifestyle only)."""
    if _is_failure(body):
        return {"status": "unavailable"}
    r = _results(body)
    bmi = _num(r.get("bmi") if "bmi" in r else r.get("bmiEstimate"))
    smoking = _val(r.get("smokingEstimate") if "smokingEstimate" in r else r.get("smoking"))
    if bmi is None and smoking is None:
        return {"status": "unavailable"}  # a vitals-only scan estimated neither
    return {
        "status": "available",
        "bmi_estimate": bmi,
        "smoking_estimate": smoking if isinstance(smoking, str) else None,
    }


def to_signals(body: dict) -> dict:
    """One webhook body → the three internal signal dicts, ready to merge under
    `ProposalInput.signals`. The seam the NuralX route calls once a webhook lands."""
    return {
        "liveness_facematch": to_liveness_facematch(body),
        "rppg_scan": to_rppg_scan(body),
        "facial_bmi_smoking": to_facial_bmi_smoking(body),
    }


@adapter("rppg_scan")
def from_vendor(raw: dict) -> dict:
    """Registry entry point for the vitals signal (the R-017 source). Registered so a
    raw NuralX body routed through `adapt('rppg_scan', body)` yields the internal shape;
    the full fan-out (liveness + bmi too) is `to_signals`."""
    return to_rppg_scan(raw)
