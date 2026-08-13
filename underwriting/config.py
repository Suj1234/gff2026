"""config.py — thresholds, weights, crosswalks, matrices, band cutoffs.

Every underwriting NUMBER lives here as a named constant so it is (a) swappable
against the real underwriting manual and (b) never buried inline in rule logic.

Values are the *industry-standard starting defaults* from IMPLEMENTATION_PLAN.md
§4A / §5 — real, usable, but pending sign-off. Each placeholder is tagged
`# TODO(underwriting-manual)`. The ICD/drug crosswalk (§4.2, §4A) uses the
public WHO ICD-10 code families.

Rule LOGIC (rules.py) is real and never a stub — only these thresholds are
placeholders, and only the vendor raw payload (behind an adapter) is mocked.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# STP eligibility & age / sum-assured ceilings  (R-005, R-006)  — LIFE values
# ---------------------------------------------------------------------------
# Auto-issue age band; ages above it (up to STP_AGE_MAX) allow issue but require
# medicals/step-up; outside the manual-review band → REFER to human UW.
# LIFE re-parameterization: term life is written to ~65-70; STP_AGE_MAX raised
# from the health value (55) to 65 so a 60-year-old term applicant reaches the
# reasoning path instead of an instant R-005 hard-refer. AUTO_ISSUE_AGE_MAX stays
# 45 — older clean lives are still allowed but require medicals (R-005b / grid).
AUTO_ISSUE_AGE_MIN = 18   # TODO(underwriting-manual)
AUTO_ISSUE_AGE_MAX = 45   # TODO(underwriting-manual)
STP_AGE_MIN = 18          # TODO(underwriting-manual): below → manual UW (minor)
STP_AGE_MAX = 65          # TODO(underwriting-manual): LIFE — above → manual UW (senior)

# Non-medical (no full medicals) sum-assured limits, by age. (R-M1's age×SA grid
# supersedes this binary in Phase 2; kept for the current medical-trigger logic.)
NON_MEDICAL_SI_LIMIT_YOUNG = 5_000_000   # age <= 45   # TODO(underwriting-manual)
NON_MEDICAL_SI_LIMIT_SENIOR = 2_500_000  # age 46-55   # TODO(underwriting-manual)

# Max sum-assured that can be auto-issued at all; above → REFER (manual UW).
# LIFE: raised from the health ₹1cr to ₹2.5cr — life SA runs far higher, and a
# large-SA case should reach financial/moral-hazard reasoning, not instant-refer.
STP_SI_CEILING = 25_000_000  # ₹2.5 crore  # TODO(underwriting-manual): LIFE

# ---------------------------------------------------------------------------
# Income → sum-assured multiple  (R-007)  — LIFE taper
# ---------------------------------------------------------------------------
# Max SA as a multiple of verified annual income, by age band. Requested SA above
# the band's multiple raises `income_thin_file` (soft flag → step-up).
# LIFE re-parameterization (convergent Indian practice: young lives 25-35×,
# tapering hard at older ages; the health floor of 15× at 56+ was too generous —
# life over-insurance vs HLV is a moral-hazard signal, so the tail drops to 5×).
INCOME_SI_MULTIPLE_BY_AGE = [
    # (age_max_inclusive, multiple)
    (35, 30),   # TODO(underwriting-manual)  ≤35 → 25-35× (use 30×)
    (45, 25),   # TODO(underwriting-manual)  36-45 → 20-25×
    (50, 15),   # TODO(underwriting-manual)  46-50 → 15×
    (60, 10),   # TODO(underwriting-manual)  51-60 → 10×
    (200, 5),   # TODO(underwriting-manual)  60+  → 5×  (life HLV floor)
]
# When income cannot be proven (AA fallback / no proof), SA ceiling below which
# no income evidence is demanded.
NO_INCOME_PROOF_SI_CEILING = 1_000_000  # TODO(underwriting-manual): LIFE ₹10L

# ---------------------------------------------------------------------------
# BMI × age premium-loading matrix  (R-009)  — % loading
# ---------------------------------------------------------------------------
# Keyed by BMI band; value is a 4-tuple of loading % for age bands
# (<=35, 36-45, 46-55, 56+). "REFER" means beyond the standard matrix → step-up.
REFER = "REFER"
BMI_AGE_LOADING = {
    # band label:            <=35   36-45  46-55  56+
    "underweight":          (5,     5,     10,    15),   # BMI < 18.5
    "normal":               (0,     0,     0,     5),    # 18.5 - 24.9
    "overweight":           (5,     10,    15,    25),   # 25 - 29.9
    "obese_1":              (15,    25,    35,    50),   # 30 - 34.9
    "obese_2":              (30,    40,    50,    REFER),# 35 - 39.9
    "obese_3":              (50,    REFER, REFER, REFER),# >= 40
}  # TODO(underwriting-manual): all loading percentages

# BMI band boundaries (WHO standard bands).
BMI_BANDS = [
    # (upper_exclusive, label)
    (18.5, "underweight"),
    (25.0, "normal"),
    (30.0, "overweight"),
    (35.0, "obese_1"),
    (40.0, "obese_2"),
    (float("inf"), "obese_3"),
]

# Occupation hazard modifier applied on top of the BMI×age loading.
# value: (extra_loading_pct_or_None, outcome_hint)  outcome_hint ∈ {None, "exclusion", REFER}
OCCUPATION_HAZARD_MODIFIER = {
    "non_hazardous":  (0, None),         # Class I     # TODO(underwriting-manual)
    "class_1":        (0, None),
    "moderate":       (10, "exclusion"), # Class II    # TODO(underwriting-manual)
    "class_2":        (10, "exclusion"),
    "hazardous":      (25, REFER),       # Class III   # TODO(underwriting-manual)
    "class_3":        (25, REFER),
    "extreme":        (None, REFER),     # Class IV
    "class_4":        (None, REFER),
}

# ---------------------------------------------------------------------------
# ML score cutoffs  (R-013, R-014)  — scores in [0, 1], higher = riskier
# ---------------------------------------------------------------------------
ML_SCORE_CLEAN_MAX = 0.30   # < this → clean (auto-issue candidate)  # TODO(underwriting-manual)
ML_SCORE_HIGH_MIN = 0.70    # >= this → high (grey-zone, never auto-decline) # TODO(underwriting-manual)
# 0.30 <= score < 0.70 → moderate (grey-zone). Applies to fraud/anomaly/graph.

# ---------------------------------------------------------------------------
# Identity gate  (R-003)
# ---------------------------------------------------------------------------
FACE_MATCH_MIN = 0.85  # below → identity-fraud hard decline  # TODO(underwriting-manual)

# ---------------------------------------------------------------------------
# Postpone window  (decision row 4)
# ---------------------------------------------------------------------------
POSTPONE_WINDOW_DAYS = 90  # acute event / surgery / hospitalization within → POSTPONE  # TODO(underwriting-manual)
POSTPONE_REEVALUATE_MONTHS = 3  # TODO(underwriting-manual)

# ---------------------------------------------------------------------------
# Adverse selection / velocity  (R-012)
# ---------------------------------------------------------------------------
VELOCITY_CROSS_PRODUCT_MIN = 3          # K: cross-product apps in 45d  # TODO(underwriting-manual)
VELOCITY_LAST_HEALTH_SIGNAL_DAYS = 30   # recent health signal window   # TODO(underwriting-manual)

# ---------------------------------------------------------------------------
# LIFE financial underwriting  (R-F2 HLV ceiling, R-F3 PAN-aggregate)
# ---------------------------------------------------------------------------
# R-F2: max SA = min(income × age-multiple, HLV). The income multiple reuses
# INCOME_SI_MULTIPLE_BY_AGE (above); HLV, when provided, is a hard second ceiling.
# Requested SA above EITHER ceiling raises `over_insurance` (moral-hazard proxy).
#
# R-F3: aggregate in-force life cover (PAN-linked) must not exceed the same
# income×age-multiple cap — cover-stacking across insurers is the over-insurance
# signal life cares about. A tolerance avoids flagging trivial rounding overages.
PAN_AGGREGATE_TOLERANCE = 1.10  # allow aggregate up to 110% of the cap  # TODO(underwriting-manual)

# ---------------------------------------------------------------------------
# LIFE medical evidence grid  (R-M1)  — age × sum-assured → evidence required
# ---------------------------------------------------------------------------
# Decides WHAT medical evidence a case needs (never the price). Replaces the
# binary NON_MEDICAL_SI_LIMIT_* with a 2-D grid. Evidence tiers, increasing:
#   "none"      DGH only
#   "tele_mer"  tele/video medical interview
#   "full_mer"  physical exam + standard labs
#   "full_labs" full exam + ECG/TMT + extended labs
# A case needing tele_mer+ beyond what's on file → step-up (R-M1 beyond_matrix).
# Grid keyed by (age_max_inclusive, [(sa_max_inclusive, tier), ...]); first match wins.
MEDICAL_GRID_BY_AGE_SA = [
    # age ≤ 35
    (35, [(5_000_000, "none"), (10_000_000, "tele_mer"), (float("inf"), "full_mer")]),
    # age 36-45
    (45, [(5_000_000, "tele_mer"), (10_000_000, "full_mer"), (float("inf"), "full_labs")]),
    # age 46-55
    (55, [(2_500_000, "tele_mer"), (float("inf"), "full_labs")]),
    # age 56+
    (200, [(float("inf"), "full_labs")]),
]  # TODO(underwriting-manual): tiers + cutoffs per the life underwriting manual

# ---------------------------------------------------------------------------
# Litigation / FIR  (R-018)  — moral-hazard soft flag → grey-zone
# ---------------------------------------------------------------------------
# Any of these on record raises `adverse_litigation` (grey-zone → human UW): a
# criminal case, a pending case, a registered FIR, or an NI-Act-138 cheque bounce.
# High-severity when a criminal case or cheque-bounce is present; else moderate.
LITIGATION_CRIMINAL_MIN = 1   # >= this many criminal cases → flag  # TODO(underwriting-manual)
LITIGATION_FIR_MIN = 1        # >= this many FIRs registered → flag  # TODO(underwriting-manual)

# ---------------------------------------------------------------------------
# GST active alerts  (A2)  — occupation/financial penalty + soft flag
# ---------------------------------------------------------------------------
# Vendor `soleProprietor.activeAlerts` keys we act on (docs §1). Cancelled GSTIN
# is the more material signal (business wound down / struck off) → high severity.
GST_ALERT_KEYS = ("isGstCancelled", "isGstTransactionDelay")  # TODO(underwriting-manual)

# ---------------------------------------------------------------------------
# Cluster rule  (R-015)
# ---------------------------------------------------------------------------
CLUSTER_SOFT_FLAG_MIN = 2  # >= this many soft flags from the cluster set → GREY-ZONE  # TODO(underwriting-manual)
CLUSTER_FLAG_TYPES = frozenset({
    "ckyc_mismatch",
    "mobile_pan_mismatch",
    "identity_mismatch",
    "thin_file",
    "income_thin_file",
    "moderate_ml_score",
    "velocity_anomaly",
    "non_disclosure_signal",
    "adverse_litigation",
    # LIFE moral-hazard flags:
    "over_insurance",             # R-F2/R-F3: SA above HLV/income-multiple or PAN-aggregate
    "cover_stacking",             # R-F3: aggregate in-force cover breaches the cap
    "cross_signal_moral_hazard",  # R-M2 (Phase 3): the combination signal
})

# ---------------------------------------------------------------------------
# Safety Score  (§5) — composite 0-100, HIGHER = SAFER
# ---------------------------------------------------------------------------
SAFETY_SCORE_WEIGHTS = {
    "medical": 0.22,
    "financial": 0.16,
    "identity_kyc": 0.12,
    "occupation_employer": 0.10,
    "fraud_check": 0.08,
    "lifestyle": 0.08,
    "velocity_graph": 0.06,
    "insurance_portfolio": 0.06,
    "litigation_fir": 0.05,
    "contactability": 0.04,
    "geography": 0.03,
}  # sum = 1.00  # TODO(underwriting-manual): calibrate against labeled outcomes

# Bands (higher = safer).  (low, high) inclusive ranges.
SAFETY_BANDS = [
    (80, 100, "Low Risk"),      # auto-issue          # TODO(underwriting-manual)
    (66, 79, "Moderate Risk"),  # loading / step-up   # TODO(underwriting-manual)
    (0, 65, "High Risk"),       # refer / decline     # TODO(underwriting-manual)
]


def safety_band(value: float) -> str:
    """Map a 0-100 safety score to its band label (higher = safer)."""
    for low, high, label in SAFETY_BANDS:
        if low <= value <= high:
            return label
    return "High Risk"


# ---------------------------------------------------------------------------
# ICD / drug crosswalk  (R-010) — public WHO ICD-10 families
# ---------------------------------------------------------------------------
# Declared health-checklist condition  →  set of ICD-10 code families that
# constitute evidence of that condition. One row per declared condition;
# adding a 31st question = one new row, NOT a new rule (§4.2).
CONDITION_TO_ICD = {
    "heart_disease": ["I20", "I21", "I22", "I23", "I24", "I25"],  # ischaemic heart disease
    "diabetes": ["E10", "E11", "E12", "E13", "E14"],
    "hypertension": ["I10", "I11", "I12", "I13", "I14", "I15"],
    "thyroid": ["E00", "E01", "E02", "E03", "E04", "E05", "E06", "E07"],
    "hypothyroidism": ["E03", "E00", "E01", "E02"],
    "dyslipidemia": ["E78"],
    "anaemia": ["D50", "D51", "D52", "D53", "D64"],
    # LIFE mortality-relevant conditions (the §45 non-disclosure drivers):
    "cancer": ["C" + f"{n:02d}" for n in range(0, 97)],  # C00–C96 malignant neoplasms
    "hepatitis": ["B15", "B16", "B17", "B18", "B19", "K70", "K71", "K72", "K73", "K74"],
    "mental_illness": ["F20", "F21", "F22", "F23", "F25", "F30", "F31", "F32", "F33"],
    "hiv": ["B20", "B21", "B22", "B23", "B24", "Z21"],
    "respiratory_disease": ["J40", "J41", "J42", "J43", "J44", "J45", "J46", "J47"],  # COPD/asthma
    "kidney_disease": ["N17", "N18", "N19"],  # acute + chronic kidney disease
    "stroke": ["I60", "I61", "I62", "I63", "I64", "G45"],
}  # TODO(underwriting-manual): confirm full condition crosswalk

# Drug (generic) → the condition it implies. Prescription evidence of the drug
# is treated as evidence of the mapped condition (§4.2).
DRUG_TO_CONDITION = {
    "statin": "dyslipidemia",
    "atorvastatin": "dyslipidemia",
    "rosuvastatin": "dyslipidemia",
    "telmisartan": "hypertension",
    "amlodipine": "hypertension",
    "antihypertensive": "hypertension",
    "levothyroxine": "hypothyroidism",
    "thyroid_med": "hypothyroidism",
    "metformin": "diabetes",
    "insulin": "diabetes",
    # LIFE mortality-relevant drug markers:
    "tenofovir": "hiv",
    "efavirenz": "hiv",
    "antiretroviral": "hiv",
    "tamoxifen": "cancer",
    "chemotherapy": "cancer",
    "entecavir": "hepatitis",
    "tenofovir_hbv": "hepatitis",
    "salbutamol": "respiratory_disease",
    "budesonide": "respiratory_disease",
    "olanzapine": "mental_illness",
    "clozapine": "mental_illness",
    "lithium": "mental_illness",
}  # TODO(underwriting-manual): confirm full drug list


def icd_code_family(code: str) -> str:
    """Normalize an ICD-10 code to its 3-char family prefix (e.g. 'I25.10' -> 'I25')."""
    return code.strip().upper().split(".")[0][:3]


# ---------------------------------------------------------------------------
# Rule versioning — stamped onto every output for auditability (§11)
# ---------------------------------------------------------------------------
RULES_VERSION = "v1"
