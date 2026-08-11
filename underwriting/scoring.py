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


def _deduct(penalties: list[tuple[float, str]]) -> tuple[float, list[str]]:
    """Apply a list of (points, reason) penalties to a 100 base; clamp at 0."""
    score = 100.0
    whys: list[str] = []
    for pts, why in penalties:
        score -= pts
        whys.append(f"-{pts:g}: {why}")
    return max(0.0, round(score, 1)), whys


def _s_identity(inp, bre, flags) -> tuple[float, list[str]]:
    p = []
    if "identity_mismatch" in flags:
        p.append((30, "name/DOB/address mismatch across sources"))
    if "ckyc_mismatch" in flags:
        p.append((28, "CKYC field mismatch (name/DOB/address)"))
    if "mobile_pan_mismatch" in flags:
        p.append((5, "mobile holder-name mismatch"))
    return _deduct(p) if p else (100.0, ["facematch/liveness ok, identity fields consistent"])


def _s_contactability(inp, bre, flags) -> tuple[float, list[str]]:
    p = []
    if "mobile_pan_mismatch" in flags:
        p.append((10, "mobile holder-name mismatch"))
    return _deduct(p) if p else (100.0, ["email/mobile clean"])


def _s_occupation(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
    p = []
    if sig.mca_director.available and sig.mca_director.director_default is True:
        p.append((35, "MCA director marked defaulter"))
    haz = (sig.occupation_hazard.hazard_class or "non_hazardous").lower()
    haz_extra, _ = C.OCCUPATION_HAZARD_MODIFIER.get(haz, (0, None))
    if haz_extra:
        p.append((haz_extra, f"hazardous occupation class '{haz}'"))
    # GST active alerts (A2): cancelled GSTIN is the material case; delay is milder.
    if "gst_alert" in flags:
        ga = next((f for f in bre.soft_flags if f.flag_type == "gst_alert"), None)
        cancelled = bool(ga and ga.context.get("cancelled"))
        p.append((20 if cancelled else 10,
                  "GST cancelled" if cancelled else "GST filing/transaction delay"))
    return _deduct(p) if p else (100.0, ["EPF verified, non-hazardous"])


def _s_financial(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
    p = []
    declared = (inp.application.financial.declared_annual_income
                if inp.application.financial else None)
    imputed = sig.account_aggregator.imputed_annual_income
    if declared and imputed:
        gap = abs(declared - imputed) / declared
        if gap >= 0.05:
            p.append((22, f"declared vs bank-statement income gap {gap*100:.0f}%"))
    if "income_thin_file" in flags:
        p.append((15, "requested SI exceeds income multiple"))
    if "thin_file" in flags:
        p.append((10, "income evidence is AA-fallback only"))
    cb = sig.credit_bureau
    if cb.available and cb.total_outstanding and imputed and cb.total_outstanding > imputed:
        ratio = cb.total_outstanding / imputed
        p.append((min(round(10 + (ratio - 1) * 8, 1), 22), f"total outstanding {ratio:.1f}× annual income"))
    # The anomaly score (isolation-forest stand-in) is an authenticity signal here.
    an = risk_scores(inp, bre).anomaly_score
    if an and an >= C.ML_SCORE_HIGH_MIN:
        p.append((10, f"anomaly score {an} in high band"))
    return _deduct(p) if p else (100.0, ["income corroborated, debt in range"])


_LIFESTYLE_SEVERITY_PTS = {"high": 12, "moderate": 7, "low": 3}  # per indicator  # TODO(underwriting-manual)


def _s_lifestyle(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
    p = []
    # facial/CV smoking estimate is a FACT; declared tobacco is a declared FACT.
    fb = sig.facial_bmi_smoking
    declared_tobacco = bool(inp.application.health_declaration.tobacco)
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

    return _deduct(p) if p else (100.0, ["no adverse lifestyle indicators in facts"])


def _s_medical(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
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
    return _deduct(p) if p else (100.0, ["labs in range, no undisclosed conditions"])


def _s_velocity(inp, bre, flags) -> tuple[float, list[str]]:
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
    return _deduct(p) if p else (100.0, ["no cover-stacking pattern"])


def _s_geography(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
    g = sig.geography
    p = []
    if g.available and g.fraud_hotspot_flag is True:
        p.append((15, "pincode flagged fraud-hotspot (feeds score only, not a gate)"))
    if g.available and isinstance(g.morbidity_index, (int, float)) and g.morbidity_index > 0.5:
        p.append((10, f"elevated area morbidity index {g.morbidity_index}"))
    return _deduct(p) if p else (100.0, ["pincode not a hotspot, morbidity in range"])


def _s_litigation(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
    lit = (sig.model_extra or {}).get("litigation_fir")
    p = []
    if isinstance(lit, dict):
        crim = sum(1 for c in lit.get("cases", []) if c.get("civil_criminal") == "criminal")
        firs = lit.get("firs_registered", 0) or 0
        if crim:
            p.append((min(15 * crim, 30), f"{crim} criminal case(s)"))
        if firs:
            p.append((min(20 * firs, 40), f"{firs} FIR(s) registered"))
    return _deduct(p) if p else (100.0, ["no adverse litigation on record"])


def _s_fraud_check(inp, bre, flags) -> tuple[float, list[str]]:
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
    return _deduct(p) if p else (100.0, ["no tampering, low fraud score"])


def _s_insurance_portfolio(inp, bre, flags) -> tuple[float, list[str]]:
    sig = inp.signals
    iib = sig.iib
    p = []
    if iib.available and (iib.num_policies or 0) >= 2:
        p.append((10, f"{iib.num_policies} existing policies (portfolio concentration)"))
    if iib.available and iib.claim_match is True:
        p.append((10, "IIB prior-claim match"))
    return _deduct(p) if p else (100.0, ["no adverse portfolio signal"])


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
    "litigation_fir": _s_litigation,
    "fraud_check": _s_fraud_check,
    "insurance_portfolio": _s_insurance_portfolio,
}


def safety_score(inp: ProposalInput, bre: BreResult) -> tuple[SafetyScore, list[ScoringBreakdownRow], dict]:
    """Weighted composite 0-100 (higher = safer) + per-source breakdown (§5.2)."""
    flags = set(_flags_by_type(bre).keys())
    rows: list[ScoringBreakdownRow] = []
    total = 0.0
    sum_w = 0.0

    # ponytail: risk_scores() is recomputed inside a few sub-scorers (financial/
    # velocity/fraud_check) instead of computed once here and passed down. Deterministic
    # so it's safe; hoist to a single call + inject if scoring ever shows up hot.
    for group, weight in C.SAFETY_SCORE_WEIGHTS.items():
        scorer = _SOURCE_SCORERS[group]
        sub, whys = scorer(inp, bre, flags)
        contribution = round(weight * sub, 2)
        total += contribution
        sum_w += weight
        rows.append(ScoringBreakdownRow(
            source_group=group, weight=weight, risk_sub_score=sub,
            contribution=contribution, why="; ".join(whys),
        ))

    value = round(total, 1)
    ss = SafetyScore(value=value, band=C.safety_band(value))
    scoring_total = {"sum_of_weights": round(sum_w, 4), "computed_safety_score": value}
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
    assert abs(tot["sum_of_weights"] - 1.0) < 1e-6, tot
    assert ss.band == "High Risk", (ss.value, ss.band)
    assert 60 <= ss.value <= 70, f"Rohit safety score {ss.value} not ~65"
    # every contribution reconstructs the total (auditability)
    assert abs(sum(r.contribution for r in rows) - ss.value) < 0.5
    print(f"Rohit safety_score={ss.value} band={ss.band} fraud={rs.fraud_score} OK")


if __name__ == "__main__":
    _demo()
