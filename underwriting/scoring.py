"""scoring.py — risk scores + the weighted Safety Score.  NO AI, NO random values.

IMPLEMENTATION_PLAN.md §5. Two products, both **real, explainable heuristics**:

1. `risk_scores(inp, bre)` — the interim fraud / anomaly / graph scores. Until a
   model is trained on labeled data (§5.1), these are a **documented deterministic
   feature-weighted function**: each score starts at 0 and adds a named, bounded
   contribution per triggering feature. The contributions ARE the attribution
   (`shap`-shaped map) — a stand-in for SHAP that is honest about being a heuristic.
   If the bundle already carries upstream model scores (`signals.ml_scores`), they
   are used as the base and the heuristic contributions explain/adjust them.

2. `safety_score(inp, bre)` — the composite 0-100 (higher = safer, §5.2):
   `Σ weight_i × sub_score_i` over the §4A source groups. Each source group starts
   at 100 (clean) and loses documented penalty points per adverse feature it owns.
   Every penalty is attributed in the row's `why`. Bands from config (§4A).

Boundary (§1.8): the scorer reads FACTS (lab values vs ref ranges, ML scores,
director_default, income mismatch) and the BRE's own judgments (soft flags,
loading) — it never invents a number. Penalty magnitudes are the only knobs and
are tagged `# TODO(underwriting-manual)` like every other threshold.
"""

from __future__ import annotations

import re
from typing import Optional

from . import config as C
from .schemas import (
    BreResult,
    ProposalInput,
    RiskScores,
    SafetyScore,
    ScoringBreakdownRow,
    Signals,
)


# ===========================================================================
# Helpers
# ===========================================================================
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _ml_base(sig: Signals) -> dict[str, float]:
    """Upstream model scores if the bundle carries them (else empty)."""
    ml = getattr(sig, "ml_scores", None)
    if isinstance(ml, dict):
        return ml
    extra = sig.model_extra or {}
    ml = extra.get("ml_scores")
    return ml if isinstance(ml, dict) else {}


def _lab_severity(test: str, result: Optional[float], ref: Optional[str]) -> Optional[str]:
    """Judge one lab value against its FACT reference range → severity we PRODUCE.

    Ref forms handled: '<200', '13.5-17.5', '70-99'. Out-of-range → 'high' if far,
    else 'low'. This is the judgment layer (§1.8): the value+range come in as facts.
    """
    if result is None or not ref:
        return None
    ref = ref.strip()
    try:
        if ref.startswith("<"):
            hi = float(ref[1:])
            return "high" if result > hi else None
        if ref.startswith(">"):
            lo = float(ref[1:])
            return "low" if result < lo else None  # below a '>N' floor is a LOW reading
        if "-" in ref:
            lo_s, hi_s = ref.split("-", 1)
            lo, hi = float(lo_s), float(hi_s)
            if lo <= result <= hi:
                return None
            # >15% outside the band = high, else low.
            span = max(hi - lo, 1.0)
            dist = (lo - result) if result < lo else (result - hi)
            return "high" if dist > 0.15 * span else "low"
    except ValueError:
        return None
    return None


# ===========================================================================
# 1. Risk scores — fraud / anomaly / graph  (real heuristic, attributed)
# ===========================================================================
# Per-feature contributions to each 0-1 score. Bounded; the sum is clamped to 1.
# TODO(underwriting-manual): all contribution weights below are heuristic knobs.
_FRAUD_FEATURES = {
    # feature key                 : (weight, human label)
    "income_declared_vs_bsa_mismatch": 0.22,
    "medical_misrepresentation":       0.19,
    "identity_field_mismatch":         0.14,
    "mca_director_default":            0.11,
    "ckyc_mismatch":                   0.06,
    "mobile_holder_mismatch":          0.05,
}
_ANOMALY_FEATURES = {
    "income_declared_vs_bsa_mismatch": 0.20,
    "lab_abnormalities":               0.18,
    "thin_file":                       0.12,
    "high_debt_to_income":             0.10,
}
_GRAPH_FEATURES = {
    "velocity_anomaly":     0.30,
    "shared_device":        0.20,
    "shared_bank":          0.15,
    "shared_nominee":       0.15,
    "cross_product_apps":   0.20,
}


def _fraud_features(inp: ProposalInput, bre: BreResult) -> dict[str, float]:
    sig = inp.signals
    flags = {f.flag_type for f in bre.soft_flags}
    out: dict[str, float] = {}

    # declared income vs bank-statement-imputed income mismatch (authenticity)
    declared = (inp.application.financial.declared_annual_income
                if inp.application.financial else None)
    imputed = sig.account_aggregator.imputed_annual_income
    if declared and imputed and abs(declared - imputed) / declared >= 0.05:
        out["income_declared_vs_bsa_mismatch"] = _FRAUD_FEATURES["income_declared_vs_bsa_mismatch"]

    if "non_disclosure_signal" in flags:
        out["medical_misrepresentation"] = _FRAUD_FEATURES["medical_misrepresentation"]
    if "identity_mismatch" in flags:
        out["identity_field_mismatch"] = _FRAUD_FEATURES["identity_field_mismatch"]
    if sig.mca_director.available and sig.mca_director.director_default is True:
        out["mca_director_default"] = _FRAUD_FEATURES["mca_director_default"]
    if "ckyc_mismatch" in flags:
        out["ckyc_mismatch"] = _FRAUD_FEATURES["ckyc_mismatch"]
    if "mobile_pan_mismatch" in flags:
        out["mobile_holder_mismatch"] = _FRAUD_FEATURES["mobile_holder_mismatch"]
    return out


def _anomaly_features(inp: ProposalInput, bre: BreResult) -> dict[str, float]:
    sig = inp.signals
    flags = {f.flag_type for f in bre.soft_flags}
    out: dict[str, float] = {}

    declared = (inp.application.financial.declared_annual_income
                if inp.application.financial else None)
    imputed = sig.account_aggregator.imputed_annual_income
    if declared and imputed and abs(declared - imputed) / declared >= 0.05:
        out["income_declared_vs_bsa_mismatch"] = _ANOMALY_FEATURES["income_declared_vs_bsa_mismatch"]

    if _count_high_labs(sig) >= 2:
        out["lab_abnormalities"] = _ANOMALY_FEATURES["lab_abnormalities"]
    if "thin_file" in flags:
        out["thin_file"] = _ANOMALY_FEATURES["thin_file"]

    cb = sig.credit_bureau
    if cb.available and cb.total_outstanding and imputed and cb.total_outstanding > imputed:
        out["high_debt_to_income"] = _ANOMALY_FEATURES["high_debt_to_income"]
    return out


def _graph_features(inp: ProposalInput, bre: BreResult) -> dict[str, float]:
    sig = inp.signals
    v = sig.velocity_graph
    out: dict[str, float] = {}
    if not v.available:
        return out
    if "velocity_anomaly" in {f.flag_type for f in bre.soft_flags}:
        out["velocity_anomaly"] = _GRAPH_FEATURES["velocity_anomaly"]
    if (v.shared_device_count or 0) > 0:
        out["shared_device"] = _GRAPH_FEATURES["shared_device"]
    if (v.shared_bank_count or 0) > 0:
        out["shared_bank"] = _GRAPH_FEATURES["shared_bank"]
    if (v.shared_nominee_count or 0) > 0:
        out["shared_nominee"] = _GRAPH_FEATURES["shared_nominee"]
    if (v.cross_product_count_45d or 0) >= C.VELOCITY_CROSS_PRODUCT_MIN:
        out["cross_product_apps"] = _GRAPH_FEATURES["cross_product_apps"]
    return out


def _count_high_labs(sig: Signals) -> int:
    n = 0
    ppm = sig.pre_policy_medical
    if ppm.available:
        for lab in ppm.lab:
            if _lab_severity(lab.test, lab.result, lab.ref):  # low or high both count
                n += 1
    return n


def _score_from(base: Optional[float], features: dict[str, float]) -> tuple[float, str]:
    """Combine an optional upstream base score with heuristic contributions.

    Returns (score, source) where source ∈ {"heuristic", "upstream_model"} — which
    input actually DROVE the reported score. With an upstream base present,
    contributions never *lower* it; the score is max(base, Σcontrib), clamped to
    [0,1]. Without a base, the score IS the summed (clamped) contributions.

    The `source` is what makes the attribution honest (§5.1, §11): the `shap` map
    only ever explains the score when the heuristic drove it. When the upstream
    model wins, the heuristic features merely *corroborate* — they are not claimed
    to sum to the model's number.
    """
    # ponytail: clamp caps the score at 1.0; raw contributions can sum past it. Moot for
    # attribution (heuristic-driven shap is rescaled to the score in _reconciled_attribution),
    # but revisit this silent ceiling if a case ever legitimately maxes out.
    contrib = sum(features.values())
    if base is None:
        return round(_clamp(contrib), 4), "heuristic"
    if base >= contrib:
        return round(_clamp(base), 4), "upstream_model"
    return round(_clamp(contrib), 4), "heuristic"


def risk_scores(inp: ProposalInput, bre: BreResult) -> RiskScores:
    """Fraud / anomaly / graph scores with per-feature attribution (§5.1)."""
    sig = inp.signals
    base = _ml_base(sig)

    ff = _fraud_features(inp, bre)
    af = _anomaly_features(inp, bre)
    gf = _graph_features(inp, bre)

    fraud, fraud_src = _score_from(base.get("fraud_score"), ff)
    anomaly, _ = _score_from(base.get("anomaly_score"), af)
    graph, _ = _score_from(base.get("graph_score"), gf)

    top = max(fraud, anomaly, graph)
    if top >= C.ML_SCORE_HIGH_MIN:
        band = "high"
    elif top >= C.ML_SCORE_CLEAN_MAX:
        band = "moderate"
    else:
        band = "low"

    # Attribution for the fraud score, made to RECONCILE with the reported number
    # (§5.1, §11 — an explanation that doesn't sum to the score is worse than none):
    #   - heuristic drove it  → scale the feature weights so Σ == fraud_score.
    #   - upstream model drove it → the features corroborate but do NOT sum to it;
    #     surface the model's number explicitly so the report is honest about it.
    shap = _reconciled_attribution(ff, fraud, fraud_src)

    return RiskScores(
        fraud_score=fraud, anomaly_score=anomaly, graph_score=graph,
        composite_band=band, shap=shap,
        score_source=fraud_src,
        attribution_note=(
            "shap weights sum to fraud_score (heuristic-driven)"
            if fraud_src == "heuristic" else
            "fraud_score is the upstream model's; shap lists corroborating heuristic "
            "features, which are NOT claimed to sum to it"
        ),
    )


def _reconciled_attribution(features: dict[str, float], score: float, source: str) -> dict[str, float]:
    """Return a `shap`-shaped map that is honest about the reported score.

    heuristic-driven: rescale so Σ weights == score (the clamp at 1.0 can otherwise
    make raw weights sum past the score). upstream-driven: leave raw feature weights
    as corroboration — `attribution_note`/`score_source` say they don't sum to it.
    """
    ordered = dict(sorted(features.items(), key=lambda kv: kv[1], reverse=True))
    if source != "heuristic" or not ordered:
        return ordered
    raw = sum(ordered.values())
    if raw <= 0:
        return ordered
    return {k: round(v / raw * score, 4) for k, v in ordered.items()}


# ===========================================================================
# 2. Safety Score — composite 0-100, higher = safer  (§5.2)
# ===========================================================================
# Each source group is scored by deducting documented penalties from 100.
# `_SOURCE_SCORERS[group]` returns (sub_score_0_100, [why_strings]).
# TODO(underwriting-manual): every penalty magnitude below is a heuristic knob.


def _flags_by_type(bre: BreResult) -> dict[str, list]:
    out: dict[str, list] = {}
    for f in bre.soft_flags:
        out.setdefault(f.flag_type, []).append(f)
    return out


# Sentinel sub-score for a group whose source(s) never arrived. NOT 100 (which
# means "assessed and clean") — an unassessed group is excluded from the composite
# by safety_score, so this value is only a placeholder for the row/report.
NOT_ASSESSED_SUB = 0.0
_NOT_ASSESSED_WHY = "source unavailable — not assessed (NOT a clean result)"


def _deduct(penalties: list[tuple[float, str]]) -> tuple[float, list[str]]:
    """Apply a list of (points, reason) penalties to a 100 base; clamp at 0."""
    score = 100.0
    whys: list[str] = []
    for pts, why in penalties:
        score -= pts
        whys.append(f"-{pts:g}: {why}")
    return max(0.0, round(score, 1)), whys


def _result(
    penalties: list[tuple[float, str]], assessed: bool, clean_text: str
) -> tuple[float, list[str], bool]:
    """Three-valued scorer return: (sub_score, whys, assessed).

    - penalties present            → (deducted score, whys, True)   [assessed, flagged]
    - no penalties, source present → (100.0, [clean_text], True)     [assessed, clean]
    - no penalties, source ABSENT  → (NOT_ASSESSED_SUB, [why], False)[NOT assessed]

    The absent case is the bug fix: previously it returned (100.0, clean_text) and
    was scored as a clean 'Low' — asserting a state never observed (e.g. 'labs in
    range' with zero labs). Now it is excluded from the composite (see safety_score).
    """
    if penalties:
        sub, whys = _deduct(penalties)
        return sub, whys, True
    if assessed:
        return 100.0, [clean_text], True
    return NOT_ASSESSED_SUB, [_NOT_ASSESSED_WHY], False


def _s_identity(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    p = []
    if "identity_mismatch" in flags:
        p.append((30, "name/DOB/address mismatch across sources"))
    if "ckyc_mismatch" in flags:
        p.append((28, "CKYC field mismatch (name/DOB/address)"))
    if "mobile_pan_mismatch" in flags:
        p.append((5, "mobile holder-name mismatch"))
    # PAN not Aadhaar-linked = weaker KYC completeness (mild). aadhaar_seeded is a fact
    # from pan_verify; only penalize when the source is present and explicitly not seeded.
    if sig.pan_verify.available and (sig.pan_verify.model_extra or {}).get("aadhaar_seeded") is False:
        p.append((C.AADHAAR_NOT_SEEDED_PENALTY, "PAN not Aadhaar-linked (weaker KYC)"))
    # Assessed if any identity source arrived (PAN / Aadhaar / liveness / CKYC).
    assessed = any(s.available for s in (
        sig.pan_verify, sig.aadhaar_ekyc, sig.liveness_facematch, sig.ckyc))
    return _result(p, assessed, "identity fields consistent")


def _email_contactability_penalties(em) -> list[tuple[float, str]]:
    """Email-deliverability penalties shared by the real composite (_s_contactability)
    and the Step-2 display-only chip (email_contactability). Deliberately excludes
    is_spam/fraud_risk_score — those stay fraud-chip-only (_s_fraud_check), so the
    two contactability/fraud readings never double-count the same signal."""
    p: list[tuple[float, str]] = []
    if em.available:
        if em.smtp_reachable is False or em.is_blocked is True or em.has_mx_records is False:
            p.append((C.EMAIL_UNREACHABLE_PENALTY, "email not reachable / no valid mail server"))
        if em.is_disposable is True:
            p.append((C.EMAIL_DISPOSABLE_PENALTY, "disposable email domain"))
        if em.name_match is False:
            p.append((C.EMAIL_NAME_MISMATCH_PENALTY, "email name does not match applicant (soft signal)"))
    return p


def _s_contactability(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    mi = sig.mobile_intel
    mx = mi.model_extra or {}
    em = sig.email_intel
    p = []
    if "mobile_pan_mismatch" in flags:
        p.append((10, "mobile holder-name mismatch"))
    if mi.available:
        # Invalid number = a real contactability failure (hard-ish).
        if mx.get("number_valid") is False:
            p.append((25, "mobile number not valid / unreachable"))
        # Very young number = synthetic-identity / mule signal (also a fraud signal below).
        vm = mi.vintage_months if mi.vintage_months is not None else mx.get("vintage_months")
        if isinstance(vm, (int, float)) and vm < C.MOBILE_RECENT_NUMBER_MAX_MONTHS:
            p.append((15, f"mobile number only {int(vm)}mo old (recent)"))
    p.extend(_email_contactability_penalties(em))
    assessed = mi.available or em.available
    return _result(p, assessed, "email/mobile clean")


def email_contactability(inp: ProposalInput) -> tuple[float, list[str], bool]:
    """Email-only contactability read (Step-2 rail chip, journey/step_routes.py).

    NOT part of the §4A composite groups (_SOURCE_SCORERS) — _s_contactability
    (mobile+email combined, same penalties via _email_contactability_penalties) still
    owns the Safety Score composite everywhere. This is a display-only score for the
    moment right after Step-1's email fetch, so the chip's number is honestly
    email-derived instead of borrowing mobile_intel's read (which is always available
    earlier, from OTP verification).
    """
    em = inp.signals.email_intel
    p = _email_contactability_penalties(em)
    return _result(p, em.available, "email valid, reachable, matches applicant")


def _s_occupation(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    p = []
    if sig.mca_director.available and sig.mca_director.director_default is True:
        p.append((35, "MCA director marked defaulter"))
    # Hazard class: explicit occupation_hazard source first, else DERIVE from the
    # self-employed's natureOfBusiness (previously dropped). Manufacturing/mining/etc.
    # map to a hazard class; unknown trade stays non_hazardous (never assume danger).
    haz = (sig.occupation_hazard.hazard_class or _hazard_from_nature(sig) or "non_hazardous").lower()
    haz_extra, _ = C.OCCUPATION_HAZARD_MODIFIER.get(haz, (0, None))
    if haz_extra:
        p.append((haz_extra, f"hazardous occupation class '{haz}'"))
    # GST active alerts (A2): cancelled GSTIN is the material case; delay is milder.
    if "gst_alert" in flags:
        ga = next((f for f in bre.soft_flags if f.flag_type == "gst_alert"), None)
        cancelled = bool(ga and ga.context.get("cancelled"))
        p.append((20 if cancelled else 10,
                  "GST cancelled" if cancelled else "GST filing/transaction delay"))
    # Short current-job tenure = income-stability risk (mild).
    if "short_tenure" in flags:
        p.append((8, "short current-employment tenure"))
    # Assessed if any employment/occupation source arrived (EPFO / MCA / GST / hazard).
    assessed = any(s.available for s in (
        sig.epfo, sig.mca_director, sig.gst, sig.occupation_hazard))
    return _result(p, assessed, "EPF verified, non-hazardous")


def _hazard_from_nature(sig) -> Optional[str]:
    """Derive a hazard class from the self-employed's natureOfBusiness strings (gst signal).
    Substring match against C.NATURE_OF_BUSINESS_HAZARD; most-severe hit wins. None if the
    gst source is absent or no trade matches (→ caller defaults to non_hazardous)."""
    gx = sig.gst.model_extra or {}
    trades = gx.get("nature_of_business") or []
    if not trades:
        return None
    order = {"non_hazardous": 0, "moderate": 1, "hazardous": 2, "extreme": 3}
    best = None
    for t in trades:
        tl = str(t).lower()
        for needle, cls in C.NATURE_OF_BUSINESS_HAZARD.items():
            if needle in tl and (best is None or order.get(cls, 0) > order.get(best, 0)):
                best = cls
    return best


def _s_financial(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
    p = []
    declared = (inp.application.financial.declared_annual_income
                if inp.application.financial else None)
    imputed = sig.account_aggregator.imputed_annual_income
    if declared and imputed:
        gap = abs(declared - imputed) / declared
        if gap >= 0.05:
            p.append((22, f"declared income ₹{declared:,} vs bank-statement ₹{imputed:,} "
                          f"— {gap*100:.0f}% apart"))
    if "income_thin_file" in flags:
        p.append((15, "requested cover is high for the income on record"))
    if "thin_file" in flags:
        p.append((10, "income backed only by bank statement (no ITR or salary records to confirm it)"))
    cb = sig.credit_bureau
    if cb.available and cb.total_outstanding and imputed and cb.total_outstanding > imputed:
        ratio = cb.total_outstanding / imputed
        p.append((min(round(10 + (ratio - 1) * 8, 1), 22), f"total outstanding {ratio:.1f}× annual income"))
    # The anomaly score (isolation-forest stand-in) is an authenticity signal here.
    an = risk_scores(inp, bre).anomaly_score
    if an and an >= C.ML_SCORE_HIGH_MIN:
        p.append((10, f"anomaly score {an} in high band"))
    # Brand-new business = thin income history (self-employed income-stability signal).
    if "new_business" in flags:
        p.append((10, "business recently incorporated (thin income history)"))
    # Assessed if we have any financial fact: a declared income, AA/ITR, or bureau.
    # A self-employed applicant's GST turnover is also a financial corroboration source.
    assessed = bool(declared) or sig.account_aggregator.available \
        or sig.itr.available or sig.credit_bureau.available or sig.gst.available
    return _result(p, assessed, "income corroborated, debt in range")


_LIFESTYLE_SEVERITY_PTS = {"high": 12, "moderate": 7, "low": 3}  # per indicator  # TODO(underwriting-manual)


def _s_lifestyle(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    p = []
    # facial/CV smoking estimate is a FACT; declared tobacco is a declared FACT.
    fb = sig.facial_bmi_smoking
    hd = inp.application.health_declaration
    declared_tobacco = bool(hd.tobacco)
    if fb.available and fb.smoking_estimate in ("likely", "yes") and not declared_tobacco:
        # Undeclared smoking is both a lifestyle risk and a concealment signal.
        p.append((20, "smoking indicated (CV estimate) but not declared"))
    elif declared_tobacco:
        p.append((10, "declared tobacco use"))

    # Categorized lifestyle spend indicators (FACTS from the AA statement analysis).
    aa_extra = sig.account_aggregator.model_extra or {}
    spends = (aa_extra.get("lifestyle_spends") or {}).get("risk_spends", {})
    for ind in spends.get("indicators", []):
        name = ind.get("indicator", "risk_spend")
        sev = ind.get("severity", "moderate")
        if name.startswith("smoking") and p:  # already counted via CV estimate
            continue
        p.append((_LIFESTYLE_SEVERITY_PTS.get(sev, 7), f"{name} spend ({sev})"))

    # Assessed if we have a health declaration (always present) OR a CV/AA source —
    # the declaration itself is a lifestyle assessment (tobacco/alcohol answered).
    assessed = hd is not None or fb.available or sig.account_aggregator.available
    return _result(p, assessed, "no adverse lifestyle indicators in facts")


# Family-history conditions that carry a mortality signal when onset is premature.
_HEREDITARY_MORTALITY = ("heart", "cardiac", "stroke", "cancer", "diabetes", "kidney")
_PREMATURE_ONSET_AGE = 55   # <55 = premature per standard fam-hx underwriting  # TODO(underwriting-manual)
# Phrases the prior-decline screener answer contains (past_medical_history free-text).
_PRIOR_ADVERSE = ("declin", "deferred", "postpon", "higher premium", "loaded", "rejected")


def _family_history_penalty(fh: list[str]) -> Optional[tuple[float, str]]:
    """Premature-onset (<55) family history of a mortality-relevant condition is a
    standard adverse signal. Entries look like 'Father: Diabetes @ 52' — parse the
    condition + optional '@ age' and flag only mortality conditions with early onset."""
    hits = []
    for e in fh:
        body = e.split(":", 1)[1] if ":" in e else e
        cond = body.split("@")[0].split("—")[0].strip().lower()
        if not any(h in cond for h in _HEREDITARY_MORTALITY):
            continue
        m = re.search(r"@\s*(\d{1,3})", body)
        # No age given → count the mortality-relevant hit but at the premature weight
        # only if an age proves it (unknown age is a milder signal, still worth noting).
        if m and int(m.group(1)) < _PREMATURE_ONSET_AGE:
            hits.append(f"{cond} onset age {m.group(1)}")
    if not hits:
        return None
    return (min(6 * len(hits), 16), "premature family history: " + "; ".join(hits))


def _prior_decline_penalty(pmh: Optional[str]) -> Optional[tuple[float, str]]:
    """A prior insurance decline/postpone/loading disclosed in the past-history free
    text is a standard adverse underwriting signal (another insurer already balked)."""
    if pmh and any(k in pmh.lower() for k in _PRIOR_ADVERSE):
        return (12, "prior insurance proposal declined/deferred/loaded (disclosed)")
    return None


# Deterministic per-condition mortality weight for a DECLARED condition (keyed on a
# distinctive substring of the exact UI label — HealthStep.MEDICAL_CONDITIONS). This makes
# a declaration score in REAL TIME on its own screen, not wait for ABHA. ABHA still runs to
# corroborate/catch non-disclosure separately. A "controlled" flag in the condition text
# halves the weight (managed disease is lower mortality). Magnitudes # TODO(underwriting-manual).
_DECLARED_CONDITION_PTS = {
    "cancer":        22, "tumour": 22, "tumor": 22,
    "heart":         18, "cardiac": 18,
    "stroke":        18, "tia": 18,
    "kidney":        16, "renal": 16,
    "liver":         14, "hepatitis": 14,
    "diabetes":      12,
    "epilepsy":      10, "neurolog": 10,
    "tuberculosis":  10,
    "high blood pressure": 9, "hypertension": 9,
    "depression":    8, "anxiety": 8,
    "asthma":        6, "respiratory": 6,
    "high cholesterol": 6, "cholesterol": 6,
    "thyroid":       4,
}


def _declared_condition_penalties(conditions: list[str]) -> list[tuple[float, str]]:
    """One deterministic penalty per declared condition. Disclosure is NOT free of a
    mortality signal — a declared diabetic IS higher risk than a clean life — but it's
    scored transparently (and never as harshly as a CONCEALED one, which R-010 catches).
    'controlled' in the text halves the weight."""
    out = []
    for raw in conditions or []:
        t = raw.lower()
        pts = next((v for k, v in _DECLARED_CONDITION_PTS.items() if k in t), None)
        if pts is None:
            continue
        controlled = "controlled" in t and "not controlled" not in t and "uncontrolled" not in t
        if controlled:
            pts = round(pts / 2, 1)
        # Echo the condition name (the part before the first em-dash/space-detail) for the chip.
        name = re.split(r"\s+[—-]\s+", raw, maxsplit=1)[0].strip()
        out.append((pts, f"{name} declared{' (controlled)' if controlled else ''}"))
    return out


def _s_medical(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    hd = inp.application.health_declaration
    p = []
    if "non_disclosure_signal" in flags:
        # Confirmed non-disclosure is the most severe medical signal (§ decision #2).
        nd = next((f for f in bre.soft_flags if f.flag_type == "non_disclosure_signal"), None)
        n_cond = len((nd.context.get("undisclosed") or [])) if nd else 1
        p.append((min(18 + 4 * n_cond, 38), f"{n_cond} undisclosed condition(s) with matching health evidence"))
    highs = 0
    lows = 0
    ppm = sig.pre_policy_medical
    if ppm.available:
        for lab in ppm.lab:
            sev = _lab_severity(lab.test, lab.result, lab.ref)
            if sev == "high":
                highs += 1
            elif sev == "low":
                lows += 1
    if highs:
        p.append((min(4 * highs, 16), f"{highs} lab value(s) high vs reference range"))
    if lows:
        p.append((min(3 * lows, 9), f"{lows} lab value(s) low vs reference range"))
    if bre.loading_pct and bre.loading_pct > 0:
        p.append((min(bre.loading_pct / 5, 10), f"BMI/age loading +{bre.loading_pct:g}%"))
    # DECLARED conditions score deterministically in real time (diabetes → −12, etc.),
    # so ticking a condition moves Medical on its own screen — not waiting for ABHA.
    p.extend(_declared_condition_penalties(hd.conditions))
    # Declared-fact medical signals the form collects but the engine used to ignore:
    # premature family history + a prior insurance decline. Both are standard adverse
    # markers. As penalties they make _result assess the group (a flagged declaration
    # IS a medical read); an empty declaration adds nothing and the absent-source gate
    # below still holds.
    for fn in (_family_history_penalty(hd.family_history),
               _prior_decline_penalty(hd.past_medical_history)):
        if fn:
            p.append(fn)
    # Assessed if any medical evidence arrived: a pre-policy exam, ABHA, or pharmacy, OR
    # a DECLARED condition (a declaration IS a medical read now that it scores). If the exam
    # is absent, the "labs in range" clean text must not be asserted (the core bug).
    assessed = (ppm.available or sig.abha_health_records.available or sig.pharmacy.available
                or bool(hd.conditions))
    clean = ("no undisclosed conditions; labs in range" if ppm.available
             else "no conditions declared; no adverse evidence on file")
    return _result(p, assessed, clean)


def _s_velocity(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    v = sig.velocity_graph
    p = []
    if "velocity_anomaly" in flags:
        p.append((25, "cross-product velocity with recent health signal"))
    # Graph score in the moderate/high band = some cover-stacking signal (floor 10
    # at the moderate boundary, scaling up). Below clean cutoff = no penalty.
    gs = max(v.velocity_score or 0.0, risk_scores(inp, bre).graph_score or 0.0)
    if gs >= C.ML_SCORE_CLEAN_MAX:
        p.append((min(round(10 + (gs - C.ML_SCORE_CLEAN_MAX) * 40, 1), 30),
                  f"graph/velocity score {gs} in moderate+ band"))
    return _result(p, v.available, "no cover-stacking pattern")


def _s_geography(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    g = sig.geography
    p = []
    if g.available and g.fraud_hotspot_flag is True:
        p.append((15, "pincode flagged fraud-hotspot (feeds score only, not a gate)"))
    if g.available and isinstance(g.morbidity_index, (int, float)) and g.morbidity_index > 0.5:
        p.append((10, f"elevated area morbidity index {g.morbidity_index}"))
    return _result(p, g.available, "pincode not a hotspot, morbidity in range")


def _s_fraud_check(inp, bre, flags) -> tuple[float, list[str], bool]:
    """Reflects the fraud risk score + authenticity flags (not identity-fraud gate)."""
    rs = risk_scores(inp, bre)
    p = []
    if rs.fraud_score and rs.fraud_score >= C.ML_SCORE_CLEAN_MAX:
        # Map a 0-1 fraud score onto a penalty (0.30→~15pts, 0.70→~40pts, 1.0→60pts).
        pts = min((rs.fraud_score - C.ML_SCORE_CLEAN_MAX) * 85 + 10, 60)
        p.append((round(pts, 1), f"fraud risk score {rs.fraud_score}"))
    # Email intelligence (A3): disposable/spam are hard flags; the inverted vendor
    # fraud score (0-1, higher=riskier) scales a penalty above the clean cutoff.
    em = inp.signals.email_intel
    if em.available:
        if em.is_disposable is True:
            p.append((20, "disposable email domain"))
        if em.is_spam is True:
            p.append((15, "email on spam record"))
        efs = em.fraud_risk_score
        if isinstance(efs, (int, float)) and efs >= C.ML_SCORE_CLEAN_MAX:
            p.append((min(round((efs - C.ML_SCORE_CLEAN_MAX) * 40 + 8, 1), 30),
                      f"email fraud score {efs} (vendor-inverted)"))
    # The fraud sub-score is always assessed: the heuristic fraud score is computed
    # from BRE flags that always run, and email is a bonus signal. Only if the entire
    # bundle carried nothing (no flags, no email) is it a bare "no signal" read — still
    # a real assessment (the rules ran), so assessed=True.
    return _result(p, True, "no tampering, low fraud score")


def _s_insurance_portfolio(inp, bre, flags) -> tuple[float, list[str], bool]:
    sig = inp.signals
    iib = sig.iib
    p = []
    if iib.available and (iib.num_policies or 0) >= 2:
        p.append((10, f"{iib.num_policies} existing policies (portfolio concentration)"))
    if iib.available and iib.claim_match is True:
        p.append((10, "IIB prior-claim match"))
    return _result(p, iib.available, "no adverse portfolio signal")


# group name → (weight source is config, scorer fn)
_SOURCE_SCORERS = {
    "identity_kyc": _s_identity,
    "contactability": _s_contactability,
    "occupation_employer": _s_occupation,
    "financial": _s_financial,
    "lifestyle": _s_lifestyle,
    "medical": _s_medical,
    "velocity_graph": _s_velocity,
    "geography": _s_geography,
    "fraud_check": _s_fraud_check,
    "insurance_portfolio": _s_insurance_portfolio,
}


def safety_score(inp: ProposalInput, bre: BreResult) -> tuple[SafetyScore, list[ScoringBreakdownRow], dict]:
    """Weighted composite 0-100 (higher = safer) + per-source breakdown (§5.2).

    UNASSESSED groups (source never arrived) are EXCLUDED from the composite and the
    weight is RENORMALIZED over the assessed groups — so an absent source no longer
    drags a case toward a false 100/"Low", and the composite is a true weighted mean
    of what was actually assessed. This also fixes the prior latent bug where the
    weight sum was computed but never divided by: the score now divides by the
    assessed weight, so it stays a proper 0-100 even if weights don't sum to 1.0.
    """
    flags = set(_flags_by_type(bre).keys())
    rows: list[ScoringBreakdownRow] = []
    assessed_total = 0.0   # Σ weight×sub over ASSESSED groups only
    assessed_w = 0.0       # Σ weight over ASSESSED groups only (the renorm divisor)

    # ponytail: risk_scores() is recomputed inside a few sub-scorers (financial/
    # velocity/fraud_check) instead of computed once here and passed down. Deterministic
    # so it's safe; hoist to a single call + inject if scoring ever shows up hot.
    for group, weight in C.SAFETY_SCORE_WEIGHTS.items():
        scorer = _SOURCE_SCORERS[group]
        sub, whys, assessed = scorer(inp, bre, flags)
        # `contribution` is the raw weight×sub for the row (reader-facing); the
        # composite below renormalizes, so contributions no longer sum to the value
        # when some groups are unassessed — the row carries `assessed` to explain why.
        contribution = round(weight * sub, 2)
        if assessed:
            assessed_total += weight * sub
            assessed_w += weight
        rows.append(ScoringBreakdownRow(
            source_group=group, weight=weight, risk_sub_score=sub,
            contribution=contribution, why="; ".join(whys), assessed=assessed,
        ))

    # Renormalize over assessed weight. If NOTHING was assessed (degenerate empty
    # bundle), there is no basis for a score → value 0.0, band from that (High Risk),
    # which is the safe direction (never a false clean).
    value = round(assessed_total / assessed_w, 1) if assessed_w > 0 else 0.0
    ss = SafetyScore(value=value, band=C.safety_band(value))
    scoring_total = {
        "sum_of_weights": round(assessed_w, 4),        # assessed weight (the divisor)
        "computed_safety_score": value,
        "assessed_groups": sum(1 for r in rows if r.assessed),
        "total_groups": len(rows),
    }
    return ss, rows, scoring_total


# ===========================================================================
# Self-check (§ ponytail: one runnable check on the money/security path)
# ===========================================================================
def _demo() -> None:
    import json
    from pathlib import Path

    from .rules import run_bre

    fix = Path(__file__).parent / "tests" / "fixtures" / "rohit_self_employed.json"
    data = json.loads(fix.read_text(encoding="utf-8"))
    inp = ProposalInput(**data["input"])
    bre = run_bre(inp)

    rs = risk_scores(inp, bre)
    ss, rows, tot = safety_score(inp, bre)

    assert rs.fraud_score >= C.ML_SCORE_HIGH_MIN, rs.fraud_score
    assert rs.composite_band == "high", rs.composite_band
    assert rs.shap, "attribution must be present"
    # attribution must be honest: heuristic-driven → shap sums to the score;
    # upstream-driven → the note says it doesn't (§5.1/§11).
    if rs.score_source == "heuristic":
        assert abs(sum(rs.shap.values()) - rs.fraud_score) < 0.001
    # Rohit's bundle has every group assessed, so the assessed weight is the full 1.0
    # and the composite equals the old Σ(weight×sub) — the =65 anchor is unchanged by
    # the absent-source fix (the fix only affects PARTIAL bundles).
    assert abs(tot["sum_of_weights"] - 1.0) < 1e-6, tot
    assert tot["assessed_groups"] == tot["total_groups"], "Rohit's bundle is fully assessed"
    assert ss.band == "High Risk", (ss.value, ss.band)
    assert 60 <= ss.value <= 70, f"Rohit safety score {ss.value} not ~65"
    # With all groups assessed and weights summing to 1.0, contributions reconstruct
    # the total (auditability). (This exact identity holds only when fully assessed.)
    assert abs(sum(r.contribution for r in rows) - ss.value) < 0.5

    # And PROVE the fix: an empty bundle must NOT score every group clean/Low.
    empty = ProposalInput(**{"proposal_id": "empty-check",
                             "application": {"applicant": {"name": "X", "age": 30},
                                             "product": {"sum_assured": 5_000_000}}})
    ess, erows, etot = safety_score(empty, run_bre(empty))
    n_unassessed = etot["total_groups"] - etot["assessed_groups"]
    assert n_unassessed > 0, "an empty bundle must have unassessed groups, not all-clean"
    assert not all(r.assessed for r in erows), "absent sources must be marked unassessed"
    print(f"Rohit safety_score={ss.value} band={ss.band} fraud={rs.fraud_score} OK; "
          f"empty bundle: {n_unassessed}/{etot['total_groups']} groups NOT assessed (fix works)")


if __name__ == "__main__":
    _demo()
