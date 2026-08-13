"""rules.py — the deterministic Business Rule Engine (BRE). NO AI.

Real R-001–R-017 (IMPLEMENTATION_PLAN.md §4.1), the R-010 ICD/drug crosswalk
compare (§4.2), and the cross-source consistency check. Every rule is REAL
logic; the only placeholders are the numeric thresholds (all in config.py, each
tagged `# TODO(underwriting-manual)`).

Structure (§4): one checker function per source group → `RuleResult`. `run_bre`
runs them in order, applies the hard-gate short-circuit, then routes clean /
loading / postpone / grey-zone.

Boundary (§1.8): checkers read FACTS from the input and PRODUCE judgments
(flags, severities, gate outcomes). They never read a verdict field from input.

Hard lines enforced structurally (§1.6):
  - DECLINE only ever comes from a deterministic hard gate here.
  - AML/PEP/sanctions, the STP age/SI gate, and identity-fraud are decided ONLY
    in these checkers — never reachable from the LLM layer.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from . import config as C
from .schemas import (
    AmbiguousFlag,
    BreResult,
    ProposalInput,
    RuleOutcome,
    RuleResult,
    Severity,
    Signals,
    SoftFlag,
)


# ===========================================================================
# Small real helpers (name/address normalization for the consistency check)
# ===========================================================================
_NAME_NOISE = {"mr", "mrs", "ms", "dr", "shri", "smt", "kumar", "kumari"}


def _name_tokens(name: Optional[str]) -> set[str]:
    """Lowercased alnum tokens of a name, honorifics dropped. Real token-set logic."""
    if not name:
        return set()
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return {t for t in toks if t not in _NAME_NOISE and len(t) > 1}


def names_match(a: Optional[str], b: Optional[str]) -> bool:
    """Token-subset name match (one name's tokens ⊆ the other's).

    # TODO(consistency-spec): fuzzy/token name match — replace with the agreed
    # matcher (e.g. Jaro-Winkler threshold) once the consistency spec lands.
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def dob_match(a: Optional[str], b: Optional[str]) -> bool:
    """Exact DOB match (dates are canonical facts; no fuzz)."""
    if not a or not b:
        return False
    return a.strip() == b.strip()


_ADDR_NOISE = {
    "flat", "no", "number", "road", "rd", "lane", "ln", "street", "st",
    "west", "east", "north", "south", "near", "opp", "opposite", "the",
}


def _addr_tokens(addr: Optional[str]) -> set[str]:
    if not addr:
        return set()
    toks = re.findall(r"[a-z0-9]+", addr.lower())
    return {t for t in toks if t not in _ADDR_NOISE and len(t) > 1}


def address_match(a: Optional[str], b: Optional[str]) -> bool:
    """Normalized-token overlap; ≥60% Jaccard-ish overlap counts as a match.

    # TODO(consistency-spec): normalized address match — swap for the agreed
    # normalization (pincode-anchored + token overlap) when the spec lands.
    """
    ta, tb = _addr_tokens(a), _addr_tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return overlap >= 0.60  # TODO(underwriting-manual): address-match threshold


def _flag(flag_type, rule, reason_code, reason, severity=Severity.moderate, cited=None, ctx=None):
    return SoftFlag(
        flag_type=flag_type,
        related_rule=rule,
        severity=severity,
        reason_code=reason_code,
        reason=reason,
        cited_evidence=cited or [],
        context=ctx or {},
    )


# ===========================================================================
# R-001..R-006 — hard gates (identity / fraud / sanctions / eligibility)
# ===========================================================================
def r001_mobile_revocation(sig: Signals) -> RuleResult:
    """R-001 — mobile on fraud/revocation list → HARD_DECLINE (fraud)."""
    m = sig.mobile_intel
    if m.available and m.on_revocation_list is True:
        return RuleResult(
            rule_id="R-001", outcome=RuleOutcome.HARD_DECLINE,
            reason_code="R-001-mobile-revocation",
            reason="Mobile number is on the fraud/revocation list.",
            score_inputs={"on_revocation_list": True},
        )
    return RuleResult(rule_id="R-001")


def r002_pan_invalid(sig: Signals) -> RuleResult:
    """R-002 — PAN status != valid → HARD_DECLINE (invalid identity)."""
    p = sig.pan_verify
    if p.available and p.pan_status is not None and p.pan_status != "valid":
        return RuleResult(
            rule_id="R-002", outcome=RuleOutcome.HARD_DECLINE,
            reason_code="R-002-pan-invalid",
            reason=f"PAN verification status is '{p.pan_status}', not 'valid'.",
            score_inputs={"pan_status": p.pan_status},
        )
    return RuleResult(rule_id="R-002")


def r003_identity_fraud(sig: Signals) -> RuleResult:
    """R-003 — failed liveness OR deepfake OR facematch < threshold → HARD_DECLINE."""
    f = sig.liveness_facematch
    if not f.available:
        return RuleResult(rule_id="R-003")
    fails = []
    if f.liveness_pass is False:
        fails.append("liveness failed")
    if f.deepfake_flag is True:
        fails.append("deepfake detected")
    if f.face_match_score is not None and f.face_match_score < C.FACE_MATCH_MIN:
        fails.append(f"face_match {f.face_match_score:.2f} < {C.FACE_MATCH_MIN}")
    if fails:
        return RuleResult(
            rule_id="R-003", outcome=RuleOutcome.HARD_DECLINE,
            reason_code="R-003-identity-fraud",
            reason="Identity-fraud hard gate: " + "; ".join(fails) + ".",
            score_inputs={
                "liveness_pass": f.liveness_pass,
                "deepfake_flag": f.deepfake_flag,
                "face_match_score": f.face_match_score,
            },
        )
    return RuleResult(rule_id="R-003")


def r004_aml_pep_sanctions(sig: Signals, declared_pep: Optional[bool]) -> RuleResult:
    """R-004 — AML/PEP/sanctions hit → HARD_REFER (compliance). Never reachable from LLM."""
    s = sig.pep_sanctions
    hit = (s.available and (s.applicant_hit is True or s.nominee_hit is True)) or declared_pep is True
    if hit:
        return RuleResult(
            rule_id="R-004", outcome=RuleOutcome.HARD_REFER,
            reason_code="R-004-aml-pep-sanctions",
            reason="AML/PEP/sanctions screening hit — refer to compliance.",
            score_inputs={
                "applicant_hit": s.applicant_hit, "nominee_hit": s.nominee_hit,
                "declared_pep": declared_pep,
            },
        )
    return RuleResult(rule_id="R-004")


def r005_age_band(age: int) -> RuleResult:
    """R-005 — age outside the manual-review band → HARD_REFER (manual UW)."""
    if age < C.STP_AGE_MIN or age > C.STP_AGE_MAX:
        return RuleResult(
            rule_id="R-005", outcome=RuleOutcome.HARD_REFER,
            reason_code="R-005-age-band",
            reason=f"Age {age} outside auto-underwriting band "
                   f"[{C.STP_AGE_MIN}, {C.STP_AGE_MAX}].",
            score_inputs={"age": age},
        )
    return RuleResult(rule_id="R-005")


def r005b_senior_medicals(age: int) -> RuleResult:
    """R-005b — ages in the senior band (46–55) are allowed but require medicals/
    step-up before auto-issue (§4A). Not a refer; a step-up. Ages outside the band
    are handled by R-005 (hard refer) or auto-issue eligible.

    This is a distinct check from R-009: R-009 loads on BMI/hazard; this fires on
    AGE ALONE so a clean, normal-BMI 50-year-old still gets medicals, per §4A.
    """
    if C.AUTO_ISSUE_AGE_MAX < age <= C.STP_AGE_MAX:
        return RuleResult(
            rule_id="R-005b", beyond_matrix=True,
            reason_code="R-005b-senior-medicals",
            reason=f"Age {age} is in the senior band "
                   f"({C.AUTO_ISSUE_AGE_MAX + 1}–{C.STP_AGE_MAX}) → medicals/step-up "
                   f"required before auto-issue (§4A).",
            score_inputs={"age": age},
        )
    return RuleResult(rule_id="R-005b")


def r006_si_ceiling(sum_insured: int) -> RuleResult:
    """R-006 — SI above the STP ceiling → HARD_REFER (manual UW)."""
    if sum_insured > C.STP_SI_CEILING:
        return RuleResult(
            rule_id="R-006", outcome=RuleOutcome.HARD_REFER,
            reason_code="R-006-si-ceiling",
            reason=f"Sum insured ₹{sum_insured:,} exceeds STP ceiling "
                   f"₹{C.STP_SI_CEILING:,}.",
            score_inputs={"sum_insured": sum_insured},
        )
    return RuleResult(rule_id="R-006")


# ===========================================================================
# R-007 / R-008 — income sufficiency & thin file (soft)
# ===========================================================================
def _income_multiple_for_age(age: int) -> int:
    for age_max, mult in C.INCOME_SI_MULTIPLE_BY_AGE:
        if age <= age_max:
            return mult
    return C.INCOME_SI_MULTIPLE_BY_AGE[-1][1]


def _verified_income(sig: Signals) -> Optional[int]:
    """Best available verified annual income (ITR > AA-imputed > EPFO band midpoint)."""
    if sig.itr.available and sig.itr.latest_total_taxable_income:
        return sig.itr.latest_total_taxable_income
    if sig.account_aggregator.available and sig.account_aggregator.imputed_annual_income:
        return sig.account_aggregator.imputed_annual_income
    return None


def r007_income_thin(sig: Signals, age: int, sum_insured: int) -> RuleResult:
    """R-007 — requested SI > income × age-multiple → soft flag `income_thin_file`."""
    income = _verified_income(sig)
    if income is None:
        return RuleResult(rule_id="R-007")
    mult = _income_multiple_for_age(age)
    if sum_insured > income * mult:
        flag = _flag(
            "income_thin_file", "R-007", "R-007-income",
            f"Requested SI ₹{sum_insured:,} exceeds {mult}× verified income "
            f"₹{income:,} (max ₹{income * mult:,}).",
            severity=Severity.moderate,
            cited=["signals.itr.latest_total_taxable_income",
                   "signals.account_aggregator.imputed_annual_income"],
            ctx={"income": income, "multiple": mult, "sum_insured": sum_insured},
        )
        return RuleResult(rule_id="R-007", flags=[flag],
                          score_inputs={"income": income, "sum_insured": sum_insured})
    return RuleResult(rule_id="R-007", score_inputs={"income": income})


def r008_thin_file(sig: Signals, sum_insured: int) -> RuleResult:
    """R-008 — income source is AA-fallback-only → the auto-issue SI ceiling drops to
    `NO_INCOME_PROOF_SI_CEILING`, and a soft flag `thin_file` is raised.

    The ceiling is REAL, not just described: severity escalates to `high` when the
    requested SI is above the lowered ceiling (the material case), `moderate` when it
    is within it (thin file, but not over the no-proof cap)."""
    aa = sig.account_aggregator
    itr_ok = sig.itr.available and bool(sig.itr.latest_total_taxable_income)
    epfo_ok = sig.epfo.available and bool(sig.epfo.contribution_band)
    aa_only = aa.available and (aa.income_source == "AA_fallback_only" or (not itr_ok and not epfo_ok))
    if aa_only and aa.available:
        over_ceiling = sum_insured > C.NO_INCOME_PROOF_SI_CEILING
        flag = _flag(
            "thin_file", "R-008", "R-008-thin-file",
            f"Income evidence rests on Account-Aggregator fallback only; the auto-issue "
            f"SI ceiling drops to ₹{C.NO_INCOME_PROOF_SI_CEILING:,}. Requested SI "
            f"₹{sum_insured:,} is {'ABOVE' if over_ceiling else 'within'} that ceiling.",
            severity=Severity.high if over_ceiling else Severity.moderate,
            cited=["signals.account_aggregator.income_source"],
            ctx={"no_proof_ceiling": C.NO_INCOME_PROOF_SI_CEILING,
                 "sum_insured": sum_insured, "over_ceiling": over_ceiling},
        )
        return RuleResult(rule_id="R-008", flags=[flag],
                          score_inputs={"over_ceiling": over_ceiling})
    return RuleResult(rule_id="R-008")


# ===========================================================================
# R-F2 / R-F3 — LIFE financial underwriting: HLV ceiling + PAN-aggregate cover
# ===========================================================================
def rf2_hlv_ceiling(sig: Signals, financial, age: int, sum_assured: int) -> RuleResult:
    """R-F2 (LIFE) — max SA = min(income × age-multiple, HLV). Requested SA above the
    HLV ceiling raises `over_insurance` (a moral-hazard proxy, not a decline).

    This is the LIFE complement to R-007: R-007 flags SA > income×multiple; R-F2 adds
    the HLV hard ceiling when a Human Life Value is supplied. Only fires when HLV is
    present — otherwise R-007 alone governs. Grey-zone routing, never a gate (§1.6)."""
    hlv = getattr(financial, "human_life_value", None) if financial else None
    if not hlv or hlv <= 0:
        return RuleResult(rule_id="R-F2")
    if sum_assured > hlv:
        flag = _flag(
            "over_insurance", "R-F2", "R-F2-hlv-ceiling",
            f"Requested SA ₹{sum_assured:,} exceeds Human Life Value ₹{hlv:,} "
            f"— over-insurance vs HLV (moral-hazard signal).",
            severity=Severity.high,
            cited=["application.financial.human_life_value"],
            ctx={"hlv": hlv, "sum_assured": sum_assured},
        )
        return RuleResult(rule_id="R-F2", flags=[flag],
                          score_inputs={"hlv": hlv, "sum_assured": sum_assured})
    return RuleResult(rule_id="R-F2", score_inputs={"hlv": hlv})


def rf3_pan_aggregate(sig: Signals, financial, age: int, sum_assured: int) -> RuleResult:
    """R-F3 (LIFE) — aggregate in-force LIFE cover (PAN-linked, from IIB) plus this
    request must not exceed the income × age-multiple cap. Cover-stacking across
    insurers is the over-insurance signal life underwriting cares about.

    Raises `cover_stacking` when the aggregate breaches the cap (with a tolerance).
    Reads `iib.life_inforce_sa` (falls back to total_inforce_sa). No income → can't
    compute the cap → no flag (R-008 handles the no-proof case)."""
    income = _verified_income(sig)
    if income is None:
        return RuleResult(rule_id="R-F3")
    inforce = sig.iib.life_inforce_sa if sig.iib.available else None
    if inforce is None and sig.iib.available:
        inforce = sig.iib.total_inforce_sa
    if not inforce:
        return RuleResult(rule_id="R-F3", score_inputs={"income": income})
    mult = _income_multiple_for_age(age)
    cap = income * mult
    aggregate = inforce + sum_assured
    if aggregate > cap * C.PAN_AGGREGATE_TOLERANCE:
        flag = _flag(
            "cover_stacking", "R-F3", "R-F3-pan-aggregate",
            f"Aggregate in-force life cover ₹{inforce:,} + requested ₹{sum_assured:,} "
            f"= ₹{aggregate:,} exceeds {mult}× income cap ₹{cap:,} "
            f"(+{int((C.PAN_AGGREGATE_TOLERANCE-1)*100)}% tolerance) — cover-stacking.",
            severity=Severity.high,
            cited=["signals.iib.life_inforce_sa", "signals.iib.total_inforce_sa"],
            ctx={"inforce": inforce, "requested": sum_assured, "cap": cap, "aggregate": aggregate},
        )
        return RuleResult(rule_id="R-F3", flags=[flag],
                          score_inputs={"aggregate": aggregate, "cap": cap})
    return RuleResult(rule_id="R-F3", score_inputs={"aggregate": aggregate, "cap": cap})


# ===========================================================================
# R-M1 — LIFE medical evidence grid (age × sum-assured → evidence required)
# ===========================================================================
def _required_evidence_tier(age: int, sum_assured: int) -> str:
    """The medical evidence tier this age×SA needs (config grid; first match wins)."""
    for age_max, sa_bands in C.MEDICAL_GRID_BY_AGE_SA:
        if age <= age_max:
            for sa_max, tier in sa_bands:
                if sum_assured <= sa_max:
                    return tier
            return sa_bands[-1][1]
    return C.MEDICAL_GRID_BY_AGE_SA[-1][1][-1][1]


# Evidence tiers in increasing order — for "have we already met the requirement?".
_TIER_ORDER = {"none": 0, "tele_mer": 1, "full_mer": 2, "full_labs": 3}


def _evidence_on_file(sig: Signals) -> int:
    """Highest medical evidence tier already present in the bundle (0 = none)."""
    ppm = sig.pre_policy_medical
    if ppm.available and ppm.lab:
        return _TIER_ORDER["full_labs"] if len(ppm.lab) >= 4 else _TIER_ORDER["full_mer"]
    if ppm.available and ppm.exam:
        return _TIER_ORDER["full_mer"]
    if sig.rppg_scan.available or sig.abha_health_records.available or sig.aps.available:
        return _TIER_ORDER["tele_mer"]
    return _TIER_ORDER["none"]


_LIFE_PRODUCT_TYPES = {"term_life", "whole_life", "ulip", "life"}


def _is_life(product) -> bool:
    """A LIFE product (by type or plan_variant). The life-only rules (R-M1) gate on
    this so they never fire on a health proposal — health keeps its own medical logic."""
    t = (getattr(product, "type", "") or "").lower()
    pv = (getattr(product, "plan_variant", "") or "").lower()
    return t in _LIFE_PRODUCT_TYPES or pv in _LIFE_PRODUCT_TYPES or t.endswith("_life")


def rm1_medical_grid(sig: Signals, product, age: int, sum_assured: int) -> RuleResult:
    """R-M1 (LIFE only) — age×SA decides the required medical evidence tier. If the
    required tier is above what's on file → step-up (beyond_matrix) requesting the
    medical. Decides WHAT evidence, never the price (loading stays R-009). Gated to
    life products so a health proposal (which has its own NON_MEDICAL_SI logic) is
    untouched."""
    if not _is_life(product):
        return RuleResult(rule_id="R-M1")
    required = _required_evidence_tier(age, sum_assured)
    if _TIER_ORDER[required] <= _evidence_on_file(sig):
        return RuleResult(rule_id="R-M1", score_inputs={"required": required, "met": True})
    return RuleResult(
        rule_id="R-M1", beyond_matrix=True,
        reason_code="R-M1-medical-grid",
        reason=f"Age {age} × SA ₹{sum_assured:,} requires '{required}' medical evidence, "
               f"not yet on file → step-up (request the medical exam).",
        score_inputs={"required": required, "met": False},
    )


# ===========================================================================
# R-009 — BMI × age × occupation loading matrix
# ===========================================================================
def _bmi_band(bmi: float) -> str:
    for upper, label in C.BMI_BANDS:
        if bmi < upper:
            return label
    return "obese_3"


def _age_col(age: int) -> int:
    if age <= 35:
        return 0
    if age <= 45:
        return 1
    if age <= 55:
        return 2
    return 3


def _effective_bmi(sig: Signals, declared_bmi: Optional[float]) -> Optional[float]:
    """Prefer measured facts (pre-policy exam) > CV estimate > declared."""
    ppm = sig.pre_policy_medical
    if ppm.available and ppm.exam.get("bmi") is not None:
        return float(ppm.exam["bmi"])
    fb = sig.facial_bmi_smoking
    if fb.available and fb.bmi_estimate is not None:
        return float(fb.bmi_estimate)
    return declared_bmi


def r009_loading(sig: Signals, age: int, declared_bmi: Optional[float]) -> RuleResult:
    """R-009 — BMI×age×occupation → loading class, or step-up if beyond the matrix."""
    bmi = _effective_bmi(sig, declared_bmi)
    if bmi is None:
        return RuleResult(rule_id="R-009")
    band = _bmi_band(bmi)
    col = _age_col(age)
    base = C.BMI_AGE_LOADING[band][col]

    hazard = (sig.occupation_hazard.hazard_class or "non_hazardous").lower()
    haz_extra, haz_outcome = C.OCCUPATION_HAZARD_MODIFIER.get(hazard, (0, None))

    # Beyond the standard matrix in either dimension → step-up (not a load).
    if base == C.REFER or haz_outcome == C.REFER or haz_extra is None:
        return RuleResult(
            rule_id="R-009", beyond_matrix=True,
            reason_code="R-009-beyond-matrix",
            reason=f"BMI {bmi} (band {band}) × age {age} or hazard '{hazard}' "
                   f"exceeds the standard loading matrix → step-up.",
            score_inputs={"bmi": bmi, "band": band, "hazard": hazard},
        )

    total = base + haz_extra
    if total <= 0:
        return RuleResult(rule_id="R-009", loading_pct=0.0,
                          score_inputs={"bmi": bmi, "band": band})
    return RuleResult(
        rule_id="R-009", loading_pct=float(total),
        reason_code="R-009-loading",
        reason=f"BMI {bmi} (band {band}) × age {age} = +{base}%; "
               f"occupation '{hazard}' +{haz_extra}% → total +{total}%.",
        score_inputs={"bmi": bmi, "band": band, "hazard": hazard,
                      "base_pct": base, "hazard_pct": haz_extra},
    )


# ===========================================================================
# R-010 — declared-health ↔ ABHA/pharmacy non-disclosure (crosswalk compare)
# ===========================================================================
def _declared_condition_set(health) -> set[str]:
    """Conditions the applicant declared "Yes" to (normalized labels)."""
    return {c.strip().lower().replace(" ", "_") for c in (health.conditions or [])}


# A crosswalk condition counts as "the same" as a declared/extracted label if any
# alias matches (used both for declared-set comparison and free-text label mapping).
_CONDITION_ALIASES = {
    "hypothyroidism": {"thyroid", "hypothyroidism", "hypothyroid"},
    "dyslipidemia": {"dyslipidemia", "high_cholesterol", "cholesterol", "hyperlipidemia"},
    "hypertension": {"hypertension", "high_bp", "bp", "hypertensive"},
    "diabetes": {"diabetes", "diabetic", "sugar", "mellitus", "t2dm", "dm"},
    "heart_disease": {"heart_disease", "cardiac", "cad", "coronary", "coronary_artery_disease",
                      "ischaemic", "ischemic", "myocardial"},
    "anaemia": {"anaemia", "anemia"},
    # LIFE mortality-relevant conditions:
    "cancer": {"cancer", "carcinoma", "malignant", "malignancy", "tumour", "tumor", "neoplasm",
               "oncology", "chemotherapy"},
    "hepatitis": {"hepatitis", "hbv", "hcv", "cirrhosis", "liver"},
    "mental_illness": {"schizophrenia", "bipolar", "psychosis", "psychotic", "depression",
                       "mental", "psychiatric"},
    "hiv": {"hiv", "aids", "retroviral", "antiretroviral"},
    "respiratory_disease": {"copd", "asthma", "emphysema", "bronchitis", "respiratory"},
    "kidney_disease": {"kidney", "renal", "nephropathy", "ckd", "dialysis"},
    "stroke": {"stroke", "cva", "cerebrovascular", "tia"},
}


def _label_to_condition(label: str) -> Optional[str]:
    """Map a free-text condition label (e.g. 'coronary artery disease') → a crosswalk
    condition key via the alias set. Returns None if no known condition matches."""
    toks = set(re.findall(r"[a-z]+", label.lower()))
    if not toks:
        return None
    for cond, al in _CONDITION_ALIASES.items():
        if toks & al or cond in toks:
            return cond
    # direct match against a crosswalk key (e.g. label already normalized)
    key = label.strip().lower().replace(" ", "_")
    return key if key in C.CONDITION_TO_ICD else None


def _evidence_conditions(
    sig: Signals, extractor: Optional[Callable[[str], list[str]]] = None
) -> dict[str, list[str]]:
    """Map evidence → condition via the ICD/drug crosswalk. Returns condition → why.

    When `extractor` is supplied (the LLM `extract_condition`, wired by the pipeline
    §4.2/§6), free-text `unstructured_notes` on ABHA are first turned into condition
    labels, then mapped through the same crosswalk — the messy-ABHA path. Without an
    extractor, only structured ICD/drug evidence is read (the deterministic default).
    """
    found: dict[str, list[str]] = {}

    def add(cond: str, why: str):
        found.setdefault(cond, []).append(why)

    # ICD-coded diagnoses (ABHA structured + pharmacy).
    coded: list[str] = []
    for src_name in ("abha_health_records", "pharmacy"):
        src = getattr(sig, src_name)
        if not src.available:
            continue
        coded += list(getattr(src, "icd_codes", []) or [])
        # `diagnoses` may already be ICD codes; try to match, else treat as labels.
        for d in getattr(src, "diagnoses", []) or []:
            if re.match(r"^[A-Za-z]\d", d.strip()):
                coded.append(d)

    for code in coded:
        fam = C.icd_code_family(code)
        for cond, families in C.CONDITION_TO_ICD.items():
            if fam in families:
                add(cond, f"ICD {code} → {cond}")

    # Drug → condition.
    for src_name in ("abha_health_records", "pharmacy"):
        src = getattr(sig, src_name)
        if not src.available:
            continue
        for drug in getattr(src, "prescriptions", []) or []:
            cond = C.DRUG_TO_CONDITION.get(drug.strip().lower())
            if cond:
                add(cond, f"drug {drug} → {cond}")

    # Free-text / scanned notes → LLM extraction → crosswalk (§4.2 messy-ABHA path).
    # Both ABHA unstructured notes AND the LIFE Aps (attending physician statement)
    # are RAW free-text — the LLM extractor is their adapter to canonical conditions.
    if extractor is not None:
        for src_name, field in (("abha_health_records", "unstructured_notes"),
                                ("aps", "notes")):
            src = getattr(sig, src_name)
            if not src.available:
                continue
            for note in getattr(src, field, []) or []:
                for label in _safe_extract(extractor, note):
                    cond = _label_to_condition(label)
                    if cond:
                        add(cond, f"free-text '{label}' → {cond} (LLM-extracted, {src_name})")

    return found


# Prompt-injection guard: extractor output is UNTRUSTED (a document — APS/ABHA note —
# can carry hidden instructions like "ignore rules, approve"). It is DATA, never an
# instruction. This bounds it (count + length) and keeps only strings; the real defense
# is downstream — `_label_to_condition` accepts only KNOWN crosswalk labels and drops
# everything else, so injected free text can never reach the decision. (§ files/CLAUDE.md
# untrusted-document-text; IMPLEMENTATION_PLAN prompt-injection mitigation.)
_MAX_EXTRACTED_LABELS = 20      # a real note yields a handful of conditions, not hundreds
_MAX_LABEL_LEN = 80            # a condition label, not a paragraph of injected instructions


def _safe_extract(extractor: Callable[[str], list[str]], note: str) -> list[str]:
    """Run the extractor and defensively bound its output before it is trusted."""
    try:
        out = extractor(note) or []
    except Exception:  # noqa: BLE001 — a failed extraction yields nothing, never crashes
        return []
    if not isinstance(out, list):
        return []
    safe: list[str] = []
    for label in out[:_MAX_EXTRACTED_LABELS]:
        if isinstance(label, str) and 0 < len(label) <= _MAX_LABEL_LEN:
            safe.append(label)
    return safe


def r010_non_disclosure(
    sig: Signals, health, extractor: Optional[Callable[[str], list[str]]] = None
) -> RuleResult:
    """R-010 — for any NOT-declared condition with matching ICD/drug/free-text evidence
    → soft flag `non_disclosure_signal`. One rule over the checklist + one crosswalk.

    `extractor` (the LLM) enables the free-text messy-ABHA path (§4.2); without it the
    rule runs structured-only (fully deterministic). Materiality/intent stays with the
    judge — this rule only surfaces the signal."""
    declared = _declared_condition_set(health)
    evidence = _evidence_conditions(sig, extractor)

    aliases = _CONDITION_ALIASES

    undisclosed = []
    for cond, whys in evidence.items():
        cond_aliases = aliases.get(cond, {cond})
        if declared & cond_aliases:
            continue  # applicant declared it — not a non-disclosure
        undisclosed.append((cond, whys))

    if not undisclosed:
        return RuleResult(rule_id="R-010")

    conds = ", ".join(c for c, _ in undisclosed)
    whys = "; ".join(w for _, ws in undisclosed for w in ws)
    flag = _flag(
        "non_disclosure_signal", "R-010", "R-010-nondisclosure",
        f"Undisclosed condition(s) with matching health evidence: {conds}. ({whys})",
        severity=Severity.high,
        cited=["signals.abha_health_records.diagnoses",
               "signals.abha_health_records.prescriptions",
               "signals.pharmacy.prescriptions"],
        ctx={"undisclosed": [c for c, _ in undisclosed]},
    )
    return RuleResult(rule_id="R-010", flags=[flag],
                      score_inputs={"undisclosed_count": len(undisclosed)})


# ===========================================================================
# R-011 — waiting period / exclusion (not a decline)
# ===========================================================================
def r011_waiting_period(sig: Signals, health) -> RuleResult:
    """R-011 — a pre-existing condition triggers a waiting-period/exclusion, not a
    decline. Any confirmed chronic condition (evidence or declared) → exclusion note."""
    evidence = _evidence_conditions(sig)
    declared = _declared_condition_set(health)
    if evidence or declared:
        conds = sorted(set(evidence.keys()) | declared)
        return RuleResult(
            rule_id="R-011", reason_code="R-011-waiting-period",
            reason=f"Pre-existing/chronic condition(s) {conds} → apply the product "
                   f"waiting period / exclusion (not a decline).",
            score_inputs={"conditions": conds},
        )
    return RuleResult(rule_id="R-011")


# ===========================================================================
# R-012 — adverse selection / velocity (soft)
# ===========================================================================
def r012_velocity(sig: Signals) -> RuleResult:
    """R-012 — cross-product velocity ≥ K AND recent health signal → soft flag."""
    v = sig.velocity_graph
    if not v.available:
        return RuleResult(rule_id="R-012")
    cross = v.cross_product_count_45d
    last = v.days_since_last_health_signal
    if (cross is not None and cross >= C.VELOCITY_CROSS_PRODUCT_MIN
            and last is not None and last < C.VELOCITY_LAST_HEALTH_SIGNAL_DAYS):
        flag = _flag(
            "velocity_anomaly", "R-012", "R-012-velocity",
            f"{cross} cross-product applications in 45d with a health signal "
            f"{last}d ago — possible adverse selection.",
            severity=Severity.high,
            cited=["signals.velocity_graph.cross_product_count_45d",
                   "signals.velocity_graph.days_since_last_health_signal"],
            ctx={"cross_product_count_45d": cross, "days_since_last_health_signal": last},
        )
        return RuleResult(rule_id="R-012", flags=[flag])
    return RuleResult(rule_id="R-012")


# ===========================================================================
# R-013 / R-014 — ML score thresholds (soft; never auto-decline off a score)
# ===========================================================================
def _ml_scores(sig: Signals) -> dict[str, float]:
    """Read ML scores wherever they were placed in the bundle (Phase 2 computes them)."""
    ml = getattr(sig, "ml_scores", None)
    if isinstance(ml, dict):
        return ml
    extra = sig.model_extra or {}
    if isinstance(extra.get("ml_scores"), dict):
        return extra["ml_scores"]
    return {}


def r013_ml_high(sig: Signals) -> RuleResult:
    """R-013 — any ML score ≥ high threshold → soft flag → grey-zone (never auto-decline)."""
    scores = _ml_scores(sig)
    highs = {k: v for k, v in scores.items() if isinstance(v, (int, float)) and v >= C.ML_SCORE_HIGH_MIN}
    if highs:
        flag = _flag(
            "moderate_ml_score", "R-013", "R-013-ml-high",
            f"High ML risk score(s): {highs} (≥ {C.ML_SCORE_HIGH_MIN}). "
            f"Routed to grey-zone; never an auto-decline on score alone.",
            severity=Severity.high,
            cited=[f"signals.ml_scores.{k}" for k in highs],
            ctx=highs,
        )
        return RuleResult(rule_id="R-013", flags=[flag])
    # Moderate band also flags into the cluster (still grey-zone material).
    mods = {k: v for k, v in scores.items()
            if isinstance(v, (int, float)) and C.ML_SCORE_CLEAN_MAX <= v < C.ML_SCORE_HIGH_MIN}
    if mods:
        flag = _flag(
            "moderate_ml_score", "R-013", "R-013-ml-moderate",
            f"Moderate ML risk score(s): {mods} "
            f"([{C.ML_SCORE_CLEAN_MAX}, {C.ML_SCORE_HIGH_MIN})).",
            severity=Severity.moderate,
            cited=[f"signals.ml_scores.{k}" for k in mods],
            ctx=mods,
        )
        return RuleResult(rule_id="R-013", flags=[flag])
    return RuleResult(rule_id="R-013")


def r014_ml_clean(sig: Signals) -> bool:
    """R-014 — all ML scores below the clean cutoff (auto-issue candidate signal)."""
    scores = _ml_scores(sig)
    if not scores:
        return True  # no ML signal present ⇒ nothing blocking on score grounds
    return all(v < C.ML_SCORE_CLEAN_MAX for v in scores.values() if isinstance(v, (int, float)))


# ===========================================================================
# R-M2 — LIFE cross-signal moral hazard (the differentiator; routes, never decides)
# ===========================================================================
def rm2_cross_signal(inp: ProposalInput, soft_flags: list[SoftFlag]) -> RuleResult:
    """R-M2 (LIFE) — detect a COMBINATION of individually-benign signals that together
    describe a fronting / proxy / early-claim pattern no single rule flags. Raises
    `cross_signal_moral_hazard` for the judge to reason over; it does NOT decide (§1.1
    grey-zone is rule-detected, LLM-resolved). Three patterns; ≥2 co-occurring signals
    in a pattern fires it.

    The signals here are each INNOCENT alone (a family SIM, a spouse paying premium),
    which is exactly why this is the LLM's job: distinguishing the innocent combination
    from the fronting one needs context, not a threshold (the §1.3 'LLM not rule' test).
    """
    sig = inp.signals
    app = inp.application
    product = app.product

    # Only a LIFE concern (proxy/fronting on a life policy).
    if not _is_life(product):
        return RuleResult(rule_id="R-M2")

    signals_hit: list[str] = []

    # --- Fronting / proxy signals (each benign alone) ---
    # 1. mobile holder-name mismatch (already surfaced by consistency_check)
    if any(f.flag_type == "mobile_pan_mismatch" for f in soft_flags):
        signals_hit.append("mobile holder-name mismatch")
    # 2. premium paid by a third party (not self/spouse)
    payer = (app.premium_payer or "").lower()
    if payer and payer not in ("self", "spouse", ""):
        signals_hit.append(f"premium paid by third party ({app.premium_payer})")
    # 3. proxy nominee: nominee much older than a young applicant (reverse-dependency)
    nominee = app.nominee or {}
    rel = (nominee.get("relationship") or "").lower()
    if rel in ("father", "mother", "parent") and app.applicant.age <= 35:
        signals_hit.append(f"elderly {rel} nominee for a young applicant (reverse dependency)")
    # 4. sudden large SA with no prior cover history
    iib = sig.iib
    no_prior = iib.available and (iib.num_policies or 0) == 0
    large_sa = product.sum_assured >= 10_000_000  # ₹1cr+  # TODO(underwriting-manual)
    if no_prior and large_sa:
        signals_hit.append("sudden large sum-assured with no prior insurance history")
    # 5. backdating requested (early-claim / age manipulation setup)
    if app.backdating_requested is True:
        signals_hit.append("backdating requested")

    # ≥2 co-occurring signals → the pattern is worth the judge's attention.
    if len(signals_hit) >= 2:  # TODO(underwriting-manual): pattern threshold
        flag = _flag(
            "cross_signal_moral_hazard", "R-M2", "R-M2-cross-signal",
            "Individually-benign signals co-occur into a possible fronting/proxy/"
            "early-claim pattern: " + "; ".join(signals_hit) +
            " — no single rule flags this; routed to a human via the judge.",
            severity=Severity.high,
            cited=["signals.mobile_intel.holder_name", "application.premium_payer",
                   "application.nominee", "signals.iib.num_policies"],
            ctx={"signals": signals_hit},
        )
        return RuleResult(rule_id="R-M2", flags=[flag],
                          score_inputs={"signal_count": len(signals_hit)})
    return RuleResult(rule_id="R-M2", score_inputs={"signal_count": len(signals_hit)})


# ===========================================================================
# R-018 — adverse litigation / FIR (soft; moral hazard → grey-zone)
# ===========================================================================
def _litigation_fir(sig: Signals) -> Optional[dict]:
    """Read the `litigation_fir` source (not a modeled field → lives in model_extra)."""
    lit = (sig.model_extra or {}).get("litigation_fir")
    return lit if isinstance(lit, dict) and lit.get("status") == "available" else None


def r018_litigation(sig: Signals) -> RuleResult:
    """R-018 — criminal case / pending case / registered FIR / cheque-bounce (NI Act
    §138) on record → soft flag `adverse_litigation`. Moral-hazard signal, never a
    gate: it routes to grey-zone (→ human UW), never an auto-decline (§1.6)."""
    lit = _litigation_fir(sig)
    if lit is None:
        return RuleResult(rule_id="R-018")
    cases = lit.get("cases") or []
    criminal = sum(1 for c in cases if c.get("civil_criminal") == "criminal")
    cheque = sum(1 for c in cases if c.get("cheque_bounce"))
    firs = lit.get("firs_registered", 0) or 0
    pending = lit.get("pending_cases", 0) or 0

    if criminal < C.LITIGATION_CRIMINAL_MIN and firs < C.LITIGATION_FIR_MIN and not pending and not cheque:
        return RuleResult(rule_id="R-018", score_inputs={"criminal": criminal, "firs": firs})

    bits = []
    if criminal:
        bits.append(f"{criminal} criminal case(s)")
    if pending:
        bits.append(f"{pending} pending case(s)")
    if firs:
        bits.append(f"{firs} FIR(s) registered")
    if cheque:
        bits.append(f"{cheque} cheque-bounce (NI Act §138) case(s)")
    severity = Severity.high if (criminal or cheque) else Severity.moderate
    flag = _flag(
        "adverse_litigation", "R-018", "R-018-litigation",
        "Adverse litigation on record: " + "; ".join(bits) +
        " — moral-hazard signal for a human underwriter.",
        severity=severity,
        cited=["signals.litigation_fir.cases", "signals.litigation_fir.firs_registered"],
        ctx={"criminal": criminal, "pending": pending, "firs": firs, "cheque_bounce": cheque},
    )
    return RuleResult(rule_id="R-018", flags=[flag],
                      score_inputs={"criminal": criminal, "firs": firs, "cheque_bounce": cheque})


# ===========================================================================
# R-019 — GST active alerts (soft; occupation/financial → grey-zone if material)
# ===========================================================================
def r019_gst_alerts(sig: Signals) -> RuleResult:
    """R-019 — vendor GST `activeAlerts` (isGstCancelled / isGstTransactionDelay) →
    soft flag `gst_alert`. A cancelled GSTIN is the material case (high); a filing/
    transaction delay is moderate. Feeds the occupation/financial sub-score (scoring)."""
    g = sig.gst
    alerts = (g.model_extra or {}).get("activeAlerts") if g.available else None
    if not alerts:
        return RuleResult(rule_id="R-019")
    keys = [a.get("key") for a in alerts if isinstance(a, dict) and a.get("key") in C.GST_ALERT_KEYS]
    if not keys:
        return RuleResult(rule_id="R-019")
    cancelled = "isGstCancelled" in keys
    flag = _flag(
        "gst_alert", "R-019", "R-019-gst-alert",
        f"GST active alert(s): {', '.join(keys)}" +
        (" (GSTIN cancelled)." if cancelled else " (filing/transaction delay)."),
        severity=Severity.high if cancelled else Severity.moderate,
        cited=["signals.gst.activeAlerts"],
        ctx={"alerts": keys, "cancelled": cancelled},
    )
    return RuleResult(rule_id="R-019", flags=[flag], score_inputs={"alerts": keys})


# ===========================================================================
# R-016 — geography guardrail (feeds ML only; never a standalone gate)
# ===========================================================================
def r016_geography(sig: Signals) -> RuleResult:
    """R-016 — fraud_hotspot alone is NOT a gate; it only feeds the ML score."""
    g = sig.geography
    if g.available and g.fraud_hotspot_flag is True:
        return RuleResult(
            rule_id="R-016", reason_code="R-016-geo-hotspot",
            reason="Pincode flagged fraud-hotspot — feeds ML score only; never a "
                   "standalone gate or decline.",
            score_inputs={"fraud_hotspot_flag": True},
        )
    return RuleResult(rule_id="R-016")


# ===========================================================================
# R-017 — rPPG trigger (step-up only; never a loading/decline input)
# ===========================================================================
_RPPG_NORMAL = {
    "heart_rate": (50, 100),        # bpm     # TODO(underwriting-manual)
    "respiratory_rate": (12, 20),   # /min    # TODO(underwriting-manual)
    "spo2": (95, 100),              # %       # TODO(underwriting-manual)
}


def r017_rppg(sig: Signals) -> RuleResult:
    """R-017 — consented rPPG vital outside normal range → trigger step-up only."""
    r = sig.rppg_scan
    consented = r.consented is True or r.available
    if not (r.available and consented and r.vitals):
        return RuleResult(rule_id="R-017")
    out_of_range = []
    for vital, (lo, hi) in _RPPG_NORMAL.items():
        val = r.vitals.get(vital)
        if isinstance(val, (int, float)) and not (lo <= val <= hi):
            out_of_range.append(f"{vital}={val} (normal {lo}-{hi})")
    if out_of_range:
        return RuleResult(
            rule_id="R-017", beyond_matrix=True,
            reason_code="R-017-rppg-abnormal",
            reason="rPPG vital(s) outside normal range → step-up: " + "; ".join(out_of_range),
            score_inputs={"out_of_range": out_of_range},
        )
    return RuleResult(rule_id="R-017")


# ===========================================================================
# Postpone (decision row 4) — recent acute event / pregnancy
# ===========================================================================
def postpone_check(sig: Signals) -> RuleResult:
    """Acute event / surgery / hospitalization within the postpone window, or active
    pregnancy → POSTPONE. Reads facts from ABHA/pre-policy medical."""
    a = sig.abha_health_records
    days = None
    if a.available:
        days = getattr(a, "days_since_acute_event", None)
    reason_bits = []
    if isinstance(days, (int, float)) and days <= C.POSTPONE_WINDOW_DAYS:
        reason_bits.append(f"acute medical event {days}d ago (≤ {C.POSTPONE_WINDOW_DAYS}d window)")
    # active pregnancy fact (may be on abha or pre-policy medical)
    preg = (a.available and getattr(a, "active_pregnancy", None) is True)
    if preg:
        reason_bits.append("active pregnancy")
    if reason_bits:
        return RuleResult(
            rule_id="POSTPONE", outcome=RuleOutcome.POSTPONE,
            reason_code="POSTPONE-recent-event",
            reason="; ".join(reason_bits) +
                   f" → postpone, re-evaluate after {C.POSTPONE_REEVALUATE_MONTHS} months.",
        )
    return RuleResult(rule_id="POSTPONE")


# ===========================================================================
# Consistency check (cross-source name / DOB / address)
# ===========================================================================
def consistency_check(inp: ProposalInput) -> RuleResult:
    """Compare name/DOB/address across sources. Produces `identity_mismatch` /
    `ckyc_mismatch` / `mobile_pan_mismatch` soft flags (WE derive these; §1.8).

    # TODO(consistency-spec): matching rule deferred in the plan — this is a real,
    # testable first cut (token name match, exact DOB, normalized address).
    """
    sig = inp.signals
    app = inp.application.applicant
    flags: list[SoftFlag] = []

    # Reference identity = PAN (authoritative), falling back to Aadhaar e-KYC.
    ref_name = sig.pan_verify.name or sig.aadhaar_ekyc.name or app.name
    ref_dob = sig.pan_verify.dob or sig.aadhaar_ekyc.dob or app.dob
    ref_addr = sig.pan_verify.address or sig.aadhaar_ekyc.address or app.address

    # Proposal-form vs reference.
    n_ok = names_match(app.name, ref_name)
    d_ok = dob_match(app.dob, ref_dob)
    a_ok = address_match(app.address, ref_addr)

    # CKYC mismatch (its own cluster flag).
    if sig.ckyc.available:
        ck_n = names_match(sig.ckyc.name, ref_name)
        ck_d = dob_match(sig.ckyc.dob, ref_dob)
        ck_a = address_match(sig.ckyc.address, ref_addr)
        if not (ck_n and ck_d and ck_a):
            flags.append(_flag(
                "ckyc_mismatch", "R-015", "R-015-ckyc-mismatch",
                f"CKYC record mismatch (name={ck_n}, dob={ck_d}, address={ck_a}).",
                cited=["signals.ckyc.name", "signals.ckyc.dob", "signals.ckyc.address"],
            ))

    # Mobile holder vs applicant (mobile_pan_mismatch cluster flag).
    if sig.mobile_intel.available and sig.mobile_intel.holder_name:
        if not names_match(sig.mobile_intel.holder_name, ref_name):
            flags.append(_flag(
                "mobile_pan_mismatch", "R-015", "R-015-mobile-holder",
                f"Mobile holder '{sig.mobile_intel.holder_name}' does not match "
                f"applicant '{ref_name}'.",
                severity=Severity.low,
                cited=["signals.mobile_intel.holder_name"],
            ))

    # Overall identity mismatch if any of name/DOB/address fails vs reference.
    if not (n_ok and d_ok and a_ok):
        flags.append(_flag(
            "identity_mismatch", "R-015", "R-015-identity-mismatch",
            f"Applicant identity fields mismatch across sources "
            f"(name_match={n_ok}, dob_match={d_ok}, address_match={a_ok}).",
            severity=Severity.high,
            cited=["signals.pan_verify.name", "signals.pan_verify.dob",
                   "signals.pan_verify.address"],
            ctx={"name_match": n_ok, "dob_match": d_ok, "address_match": a_ok},
        ))

    return RuleResult(rule_id="CONSISTENCY", flags=flags,
                      score_inputs={"name_match": n_ok, "dob_match": d_ok, "address_match": a_ok})


# ===========================================================================
# Orchestrator — run the whole BRE for one proposal
# ===========================================================================
_HARD_GATE_ORDER = ["R-001", "R-002", "R-003", "R-004", "R-005", "R-006"]


def run_bre(
    inp: ProposalInput, extractor: Optional[Callable[[str], list[str]]] = None
) -> BreResult:
    """Run every checker, apply the hard-gate short-circuit, then route (§4).

    `extractor` (the LLM `extract_condition`, passed by the pipeline) enables R-010's
    free-text messy-ABHA path (§4.2). Called with no extractor, the BRE is fully
    deterministic (structured evidence only) — Phase-1/2 behaviour, unchanged.
    """
    sig = inp.signals
    app = inp.application
    age = app.applicant.age
    sum_insured = app.product.sum_assured
    health = app.health_declaration

    results: list[RuleResult] = []

    # --- Hard gates first (any DECLINE/REFER short-circuits, no AI) ---
    hard_gates = [
        r001_mobile_revocation(sig),
        r002_pan_invalid(sig),
        r003_identity_fraud(sig),
        r004_aml_pep_sanctions(sig, app.declared_pep),
        r005_age_band(age),
        r006_si_ceiling(sum_insured),
    ]
    results.extend(hard_gates)

    for rid in _HARD_GATE_ORDER:
        r = next(x for x in hard_gates if x.rule_id == rid)
        if r.outcome == RuleOutcome.HARD_DECLINE:
            return BreResult(outcome="DECLINE", hard_gate=r.rule_id, rule_results=results,
                             reason_codes=[r.reason_code])
        if r.outcome == RuleOutcome.HARD_REFER:
            return BreResult(outcome="REFER", hard_gate=r.rule_id, rule_results=results,
                             reason_codes=[r.reason_code])

    # --- Postpone (row 4) — precedes loading/clean ---
    pp = postpone_check(sig)
    results.append(pp)
    if pp.outcome == RuleOutcome.POSTPONE:
        return BreResult(outcome="POSTPONE", rule_results=results, reason_codes=[pp.reason_code])

    # --- Soft rules + consistency ---
    financial = app.financial
    soft_results = [
        r005b_senior_medicals(age),
        r007_income_thin(sig, age, sum_insured),
        r008_thin_file(sig, sum_insured),
        rf2_hlv_ceiling(sig, financial, age, sum_insured),   # LIFE: HLV ceiling
        rf3_pan_aggregate(sig, financial, age, sum_insured),  # LIFE: PAN-aggregate cover
        rm1_medical_grid(sig, app.product, age, sum_insured),  # LIFE: age×SA medical grid
        r009_loading(sig, age, health.bmi),
        r010_non_disclosure(sig, health, extractor),
        r011_waiting_period(sig, health),
        r012_velocity(sig),
        r013_ml_high(sig),
        r016_geography(sig),
        r017_rppg(sig),
        r018_litigation(sig),
        r019_gst_alerts(sig),
        consistency_check(inp),
    ]
    results.extend(soft_results)

    soft_flags: list[SoftFlag] = []
    for r in soft_results:
        soft_flags.extend(r.flags)

    # R-M2 (LIFE cross-signal) runs LAST — it reads the soft flags the other rules
    # produced (e.g. mobile_pan_mismatch from the consistency check) to detect a
    # fronting/proxy COMBINATION. It routes (raises a flag), never decides.
    rm2 = rm2_cross_signal(inp, soft_flags)
    results.append(rm2)
    soft_flags.extend(rm2.flags)

    r009 = next(r for r in soft_results if r.rule_id == "R-009")
    beyond = [r for r in soft_results if r.beyond_matrix]  # R-005b / R-009 / R-017 step-ups

    reason_codes = [r.reason_code for r in results if r.reason_code]

    # --- Routing (§4 / §7 rows 5-6, plus grey-zone) ---
    # Grey-zone if the cluster rule (R-015) fires OR any high-severity soft flag
    # survives the gates, OR a beyond-matrix step-up is needed on non-loading grounds.
    cluster_hits = [f for f in soft_flags if f.flag_type in C.CLUSTER_FLAG_TYPES]
    cluster_fires = len(cluster_hits) >= C.CLUSTER_SOFT_FLAG_MIN  # R-015
    any_soft = len(soft_flags) > 0

    ambiguous = _to_ambiguous(soft_flags)

    if cluster_fires or any_soft:
        # Step-up beyond the matrix without other flags is handled by decision.py
        # (row 5 → STEP_UP); everything with surviving soft flags is grey-zone.
        return BreResult(
            outcome="GREY-ZONE",
            ambiguous_flags=ambiguous,
            soft_flags=soft_flags,
            rule_results=results,
            loading_pct=r009.loading_pct,
            reason_codes=reason_codes,
        )

    # No soft flags. Beyond-matrix step-up (age 46-55 medicals / BMI / rPPG)? (row 5)
    # reason_codes already carries each beyond-matrix rule's code (collected from
    # `results` above) — no need to re-append.
    if beyond:
        return BreResult(outcome="GREY-ZONE", soft_flags=[], rule_results=results,
                         reason_codes=reason_codes)
    if r009.loading_pct and r009.loading_pct > 0:
        return BreResult(outcome="LOADING", loading_pct=r009.loading_pct,
                         rule_results=results, reason_codes=reason_codes)

    return BreResult(outcome="CLEAN", rule_results=results, reason_codes=reason_codes)


def _to_ambiguous(soft_flags: list[SoftFlag]) -> list[AmbiguousFlag]:
    """Convert soft flags into the LLM-judge grey-zone flag list (Phase 3 input)."""
    out = []
    for i, f in enumerate(soft_flags, start=1):
        out.append(AmbiguousFlag(
            flag_id=f"flg_{i:03d}",
            flag_type=f.flag_type,
            related_rule=f.related_rule,
            context={**f.context, "reason": f.reason, "severity": f.severity.value,
                     "cited_evidence": f.cited_evidence},
        ))
    return out
