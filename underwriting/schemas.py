"""schemas.py — Pydantic models for the input contract and the output report.

INPUT is **facts-only** (IMPLEMENTATION_PLAN.md §1.8 + Phase 0): upstream vendors
/ analyzers deliver structured *facts* (BMI number, lab values + ref ranges,
categorized transactions, imputed income, per-source name/DOB/address, vitals) —
with **no** good/bad/risky labels. The verdict fields Appendix B shows inline for
illustration (`risk_triggers`, `severity`, `holder_mismatch`, `field_match`,
`gst_transaction_delay`, other `*_flag`s) are **our outputs**, so they are NOT in
the input schema — WE produce them (rules.py / scoring.py / decision.py).

OUTPUT is the combined report of Appendix A: I-Adore-style sections + Safety Score
+ the six technical blocks + audit log.

Design notes:
- Per-source models set `extra="allow"` so the full richness of a vendor's facts
  flows through untouched (we do not build the analyzers). We model *by name* only
  the facts the Phase-1 rules actually read, so a typo in a consumed field fails
  loudly while un-consumed facts pass through.
- `SourceStatus` on every enrichment source makes "no two applicants send the same
  data" a first-class citizen (partial data is the normal case, §11).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Shared
# ===========================================================================
class SourceStatus(str, Enum):
    """Per-source presence — no two applicants send the same data (Appendix B)."""

    available = "available"
    unavailable = "unavailable"
    not_applicable = "not_applicable"
    consent_declined = "consent_declined"
    not_requested = "not_requested"


class _Src(BaseModel):
    """Base for every enrichment source: carries a status; lets unmodeled facts pass."""

    model_config = ConfigDict(extra="allow")
    status: SourceStatus = SourceStatus.unavailable

    @property
    def available(self) -> bool:
        return self.status == SourceStatus.available


# ===========================================================================
# INPUT — declared proposal form  (facts the applicant stated)
# ===========================================================================
class Applicant(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    dob: Optional[str] = None
    gender: Optional[str] = None
    age: int
    marital_status: Optional[str] = None
    address: Optional[str] = None
    pincode: Optional[str] = None


class Occupation(BaseModel):
    model_config = ConfigDict(extra="allow")
    declared_type: Optional[str] = None
    declared_occupation: Optional[str] = None
    industry: Optional[str] = None
    employer_declared: Optional[str] = None


class Product(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "individual_health"  # LIFE demo uses "term_life"; inert unless a rule reads it
    sum_assured: int
    premium: Optional[int] = None
    tenure_years: Optional[int] = 1
    payment_mode: Optional[str] = None
    # LIFE fields (optional; ride on the model, named so rules can read them):
    plan_variant: Optional[str] = None          # term | whole_life | ulip
    premium_payment_term: Optional[int] = None  # PPT can differ from tenure in life


class FinancialDeclared(BaseModel):
    model_config = ConfigDict(extra="allow")
    declared_annual_income: Optional[int] = None
    purpose_of_cover: Optional[str] = None
    source_of_funds: Optional[str] = None
    # LIFE financial-underwriting inputs (R-F2 HLV ceiling):
    human_life_value: Optional[int] = None   # HLV if computed upstream
    net_worth: Optional[int] = None
    liabilities: Optional[int] = None
    dependents_count: Optional[int] = None


class HealthDeclaration(BaseModel):
    """The 30-item structured checklist the applicant filled (declared facts)."""

    model_config = ConfigDict(extra="allow")
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    bmi: Optional[float] = None
    conditions: list[str] = Field(default_factory=list)  # declared "Yes" conditions
    tobacco: Optional[bool] = None
    alcohol: Optional[bool] = None
    drugs: Optional[bool] = None
    past_medical_history: Optional[str] = None
    ongoing_medication: Optional[str] = None
    family_history: list[str] = Field(default_factory=list)


class Consent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    framework: Optional[str] = None
    granted: bool
    timestamp: Optional[str] = None
    version: Optional[str] = None


class Application(BaseModel):
    model_config = ConfigDict(extra="allow")
    applicant: Applicant
    occupation: Optional[Occupation] = None
    product: Product
    financial: Optional[FinancialDeclared] = None
    nominee: Optional[dict] = None  # relationship read from here (insurable interest, R-M2)
    existing_cover_declared: list[dict] = Field(default_factory=list)
    declared_pep: Optional[bool] = None
    health_declaration: HealthDeclaration = Field(default_factory=HealthDeclaration)
    # LIFE moral-hazard inputs (named so R-M2 can read them; optional):
    premium_payer: Optional[str] = None       # "self" | "spouse" | "third_party" | name
    backdating_requested: Optional[bool] = None


# ===========================================================================
# INPUT — enrichment sources  (facts only; verdicts stripped per §1.8)
# ===========================================================================
class MobileIntel(_Src):
    # FACTS only: no `holder_mismatch` — WE derive that in the consistency check.
    number: Optional[str] = None
    provider: Optional[str] = None
    connection_type: Optional[str] = None
    sim_activation: Optional[str] = None
    vintage_months: Optional[int] = None
    ported_recently: Optional[bool] = None
    on_revocation_list: Optional[bool] = None  # FACT from the fraud/revocation source (R-001)
    holder_name: Optional[str] = None
    holder_dob: Optional[str] = None


class PanVerify(_Src):
    pan: Optional[str] = None
    pan_status: Optional[str] = None  # "valid"/"invalid" — vendor status fact (R-002)
    name: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    masked_aadhaar: Optional[str] = None
    aadhaar_seeded: Optional[bool] = None
    address: Optional[str] = None
    mobile_on_record: Optional[str] = None
    email_on_record: Optional[str] = None


class AadhaarEkyc(_Src):
    name: Optional[str] = None
    dob: Optional[str] = None
    address: Optional[str] = None
    photo: Optional[bool] = None


class Ckyc(_Src):
    # FACTS only: no `field_match` verdict — WE compare the raw name/dob/address.
    existing_record: Optional[bool] = None
    name: Optional[str] = None
    dob: Optional[str] = None
    address: Optional[str] = None


class LivenessFacematch(_Src):
    liveness_pass: Optional[bool] = None
    liveness_score: Optional[float] = None
    face_match_score: Optional[float] = None
    deepfake_flag: Optional[bool] = None  # FACT from the deepfake detector (R-003)


class Epfo(_Src):
    name: Optional[str] = None
    employer: Optional[str] = None
    date_of_joining: Optional[str] = None
    tenure_years: Optional[float] = None
    contribution_band: Optional[str] = None
    epf_deducted_last_45d: Optional[bool] = None


class Gst(_Src):
    # FACT: raw filing dates/turnover. `gst_transaction_delay` is OUR verdict → not here.
    gstin: Optional[str] = None
    entity: Optional[str] = None
    registration_date: Optional[str] = None
    turnover_slab: Optional[str] = None
    firm_type: Optional[str] = None
    business_type: Optional[str] = None


class Itr(_Src):
    name: Optional[str] = None
    latest_total_taxable_income: Optional[int] = None
    taxable_income_by_year: list[dict] = Field(default_factory=list)


class AccountAggregator(_Src):
    # FACTS: imputed income, categorized credits/debits. `risk_triggers` &
    # `lifestyle_spends[*].severity` are OUR verdicts → not modeled as input.
    name: Optional[str] = None
    address: Optional[str] = None
    period: Optional[str] = None
    imputed_annual_income: Optional[int] = None
    avg_monthly_balance: Optional[float] = None
    expense_to_income: Optional[float] = None
    income_source: Optional[str] = None  # e.g. "gst_itr" | "AA_fallback_only" (R-008)
    credits: list[dict] = Field(default_factory=list)
    debits: list[dict] = Field(default_factory=list)


class CreditBureau(_Src):
    name: Optional[str] = None
    score: Optional[int] = None
    estimated_income: Optional[int] = None
    total_outstanding: Optional[int] = None


class McaDirector(_Src):
    name: Optional[str] = None
    din: Optional[str] = None
    din_status: Optional[str] = None
    director_default: Optional[bool] = None  # FACT from MCA (R-012 moral-hazard signal)
    entity: Optional[str] = None


class OccupationHazard(_Src):
    hazard_class: Optional[str] = None  # FACT: mapped hazard class (R-009 modifier)


class Geography(_Src):
    pincode: Optional[str] = None
    morbidity_index: Optional[float] = None
    fraud_hotspot_flag: Optional[bool] = None  # FACT from the geo source (R-016)
    hospital_density: Optional[str] = None


class VelocityGraph(_Src):
    velocity_score: Optional[float] = None
    shared_device_count: Optional[int] = None
    shared_bank_count: Optional[int] = None
    shared_nominee_count: Optional[int] = None
    cross_product_count_45d: Optional[int] = None  # FACT (R-012)
    days_since_last_health_signal: Optional[int] = None  # FACT (R-012)
    related_proposals: list[dict] = Field(default_factory=list)


class PepSanctions(_Src):
    applicant_hit: Optional[bool] = None  # FACT from screening (R-004)
    nominee_hit: Optional[bool] = None
    lists_checked: list[str] = Field(default_factory=list)


class LabResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    test: str
    unit: Optional[str] = None
    result: Optional[float] = None
    ref: Optional[str] = None  # reference range as a FACT; WE judge severity


class AbhaHealthRecords(_Src):
    # FACT: diagnoses (ICD codes) + prescriptions. WE run the R-010 crosswalk.
    diagnoses: list[str] = Field(default_factory=list)        # may be ICD codes or labels
    icd_codes: list[str] = Field(default_factory=list)        # explicit coded diagnoses
    prescriptions: list[str] = Field(default_factory=list)    # generic drug names
    unstructured_notes: list[str] = Field(default_factory=list)  # messy/free-text → LLM (Phase 3)


class Pharmacy(_Src):
    prescriptions: list[str] = Field(default_factory=list)
    icd_codes: list[str] = Field(default_factory=list)


class RppgScan(_Src):
    consented: Optional[bool] = None
    vitals: dict[str, Any] = Field(default_factory=dict)  # heart_rate, bp, ... (FACTS)
    quality_flag: Optional[str] = None


class PrePolicyMedical(_Src):
    name: Optional[str] = None
    dob: Optional[str] = None
    exam: dict[str, Any] = Field(default_factory=dict)  # height/weight/bmi/bp/pulse (FACTS)
    lab: list[LabResult] = Field(default_factory=list)
    radiology: list[dict] = Field(default_factory=list)
    medical_questionnaire: dict[str, Any] = Field(default_factory=dict)


class FacialBmiSmoking(_Src):
    bmi_estimate: Optional[float] = None       # FACT from the CV model (upstream)
    smoking_estimate: Optional[str] = None
    gender_estimate: Optional[str] = None


class Iib(_Src):
    claim_match: Optional[bool] = None
    num_policies: Optional[int] = None
    num_insurers: Optional[int] = None
    policies: list[dict] = Field(default_factory=list)
    # LIFE cover-stacking (R-F3): aggregate in-force sum-assured across ALL policies
    # (PAN-linked). The over-insurance / fronting signal that health didn't need.
    # From the IIB fraud-framework feed (April-2026) or summed from `policies`.
    total_inforce_sa: Optional[int] = None   # total in-force SA across all lines
    life_inforce_sa: Optional[int] = None    # life-only in-force SA (the relevant one)


class Aps(_Src):
    """Attending Physician Statement — a treating doctor's free-text medical record,
    requested with the applicant's signed release (LIFE staple; no standardized India
    API — always RAW free-text). Mirrors AbhaHealthRecords.unstructured_notes: the LLM
    extractor turns `notes` into conditions the R-010 crosswalk compares. `diagnoses`/
    `icd_codes` carry any already-coded facts. Facts only — no verdict (§1.8)."""

    notes: list[str] = Field(default_factory=list)        # free-text physician narrative (RAW)
    diagnoses: list[str] = Field(default_factory=list)     # coded diagnoses if present
    icd_codes: list[str] = Field(default_factory=list)
    prescriptions: list[str] = Field(default_factory=list)


class EmailIntel(_Src):
    # FACTS from the email-intelligence vendor. The vendor's own 1-100 fraud score
    # (higher = SAFER) is INVERTED by the adapter to `fraud_risk_score` in [0,1]
    # (higher = riskier) so it matches the engine's ml_scores polarity — the derived
    # number is a fact we read; the fraud judgment is ours (scoring.py fraud sub-score).
    email: Optional[str] = None
    is_disposable: Optional[bool] = None
    is_spam: Optional[bool] = None
    name_match: Optional[bool] = None
    fraud_risk_score: Optional[float] = None  # inverted 0-1, higher = riskier


class Signals(BaseModel):
    """All enrichment sources. Every field optional — partial data is the norm.

    `extra="allow"` keeps sources present in the bundle but not yet named here
    (e.g. `digital_footprint`, `defaulter_list`, `email_intel`, `salary_slip`,
    `vehicle`, `litigation_fir`, `device_fingerprint`) — they flow through and are
    still available to scoring/report assembly without failing validation.
    """

    model_config = ConfigDict(extra="allow")

    mobile_intel: MobileIntel = Field(default_factory=MobileIntel)
    pan_verify: PanVerify = Field(default_factory=PanVerify)
    aadhaar_ekyc: AadhaarEkyc = Field(default_factory=AadhaarEkyc)
    ckyc: Ckyc = Field(default_factory=Ckyc)
    liveness_facematch: LivenessFacematch = Field(default_factory=LivenessFacematch)
    epfo: Epfo = Field(default_factory=Epfo)
    gst: Gst = Field(default_factory=Gst)
    itr: Itr = Field(default_factory=Itr)
    account_aggregator: AccountAggregator = Field(default_factory=AccountAggregator)
    credit_bureau: CreditBureau = Field(default_factory=CreditBureau)
    mca_director: McaDirector = Field(default_factory=McaDirector)
    occupation_hazard: OccupationHazard = Field(default_factory=OccupationHazard)
    geography: Geography = Field(default_factory=Geography)
    velocity_graph: VelocityGraph = Field(default_factory=VelocityGraph)
    pep_sanctions: PepSanctions = Field(default_factory=PepSanctions)
    abha_health_records: AbhaHealthRecords = Field(default_factory=AbhaHealthRecords)
    pharmacy: Pharmacy = Field(default_factory=Pharmacy)
    rppg_scan: RppgScan = Field(default_factory=RppgScan)
    pre_policy_medical: PrePolicyMedical = Field(default_factory=PrePolicyMedical)
    facial_bmi_smoking: FacialBmiSmoking = Field(default_factory=FacialBmiSmoking)
    iib: Iib = Field(default_factory=Iib)
    email_intel: EmailIntel = Field(default_factory=EmailIntel)
    aps: Aps = Field(default_factory=Aps)  # LIFE: attending physician statement (R-010 free-text)


class ProposalInput(BaseModel):
    """The full facts-only bundle `POST /underwrite` accepts (Appendix B, verdicts stripped)."""

    model_config = ConfigDict(extra="allow")

    proposal_id: str
    meta: dict[str, Any] = Field(default_factory=dict)
    application: Application
    consents: list[Consent] = Field(default_factory=list)
    signals: Signals = Field(default_factory=Signals)
    documents: list[dict] = Field(default_factory=list)
    follow_up_observations: dict[str, Any] = Field(default_factory=dict)


# ===========================================================================
# INTERNAL — rule results (what checkers emit; consumed by decision.py)
# ===========================================================================
class Outcome(str, Enum):
    """The Core 6 final outcomes (§7) + the internal routing states."""

    ISSUE = "ISSUE"
    ISSUE_WITH_LOADING = "ISSUE_WITH_LOADING"
    STEP_UP = "STEP_UP"
    POSTPONE = "POSTPONE"
    REFER = "REFER"
    DECLINE = "DECLINE"


class RuleOutcome(str, Enum):
    """Per-checker gate result (rules.py). None-equivalent handled as CLEAN/soft."""

    HARD_DECLINE = "HARD_DECLINE"
    HARD_REFER = "HARD_REFER"
    POSTPONE = "POSTPONE"
    CLEAN = "CLEAN"


class Severity(str, Enum):
    low = "low"
    moderate = "moderate"
    high = "high"


class SoftFlag(BaseModel):
    """A judgment WE produce (not an input fact): a soft risk signal from a rule."""

    flag_type: str
    related_rule: str
    severity: Severity = Severity.moderate
    reason_code: str
    reason: str  # human-readable — required on every flag (§11)
    cited_evidence: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class RuleResult(BaseModel):
    """One checker's output (IMPLEMENTATION_PLAN.md §4)."""

    rule_id: str
    outcome: Optional[RuleOutcome] = None  # None → no gate fired (soft flags only)
    flags: list[SoftFlag] = Field(default_factory=list)
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    loading_pct: Optional[float] = None       # R-009 actuarial table output
    beyond_matrix: bool = False               # R-009 → step-up
    score_inputs: dict[str, Any] = Field(default_factory=dict)


class AmbiguousFlag(BaseModel):
    """A grey-zone flag routed to the LLM judge (Phase 3). Shape matches agent.py."""

    flag_id: str
    flag_type: str
    related_rule: str
    context: dict[str, Any] = Field(default_factory=dict)


class BreResult(BaseModel):
    """Aggregate output of the whole rule engine for one proposal."""

    outcome: str  # "DECLINE" | "REFER" | "POSTPONE" | "CLEAN" | "GREY-ZONE" | "LOADING"
    hard_gate: Optional[str] = None           # which hard gate fired, if any
    ambiguous_flags: list[AmbiguousFlag] = Field(default_factory=list)
    soft_flags: list[SoftFlag] = Field(default_factory=list)
    rule_results: list[RuleResult] = Field(default_factory=list)
    loading_pct: Optional[float] = None
    reason_codes: list[str] = Field(default_factory=list)
    gathered: bool = False  # set True by the pipeline after the one gather cycle (§7.1)


# ===========================================================================
# INTERNAL — LLM judge contract (Phase 3; modeled now so decision.py is stable)
# ===========================================================================
class FlagRuling(BaseModel):
    flag_id: str
    ruling: Literal[
        "benign_explained",
        "needs_income_corroboration",
        "needs_medical_check",
        "needs_identity_reverification",
        "unresolvable_escalate",
    ]
    cited_evidence: list[str] = Field(default_factory=list)
    reasoning: str = ""


# ===========================================================================
# OUTPUT — the report (Appendix A)
# ===========================================================================
class ScoringBreakdownRow(BaseModel):
    source_group: str
    weight: float
    risk_sub_score: float  # 0-100, 100 = clean/safe (only meaningful when assessed)
    contribution: float
    why: str
    # `assessed` distinguishes "source present and clean" (True, sub_score ~100)
    # from "source absent — not assessed" (False). An unassessed group is EXCLUDED
    # from the composite (safety_score renormalizes over assessed groups) so an
    # absent source no longer scores as clean. Defaults True for backward-compat.
    assessed: bool = True


class SafetyScore(BaseModel):
    value: float
    band: str
    scale: str = "0-100, higher = safer"
    method: str = "weighted_sum_of_per_source_sub_scores"
    bands: dict[str, str] = Field(
        default_factory=lambda: {"low_risk": "80-100", "moderate_risk": "66-79", "high_risk": "0-65"}
    )


class SectionEvaluation(BaseModel):
    model_config = ConfigDict(extra="allow")
    risk_level: str  # "Low" | "Moderate" | "High"


class RiskScores(BaseModel):
    model_config = ConfigDict(extra="allow")
    fraud_score: Optional[float] = None
    anomaly_score: Optional[float] = None
    graph_score: Optional[float] = None
    composite_band: Optional[str] = None
    shap: dict[str, float] = Field(default_factory=dict)


class Decision(BaseModel):
    verdict: str  # one of the Core 6 (Outcome)
    escalation_reason: Optional[str] = None
    next_step: Optional[str] = None            # STEP_UP action, if any
    indicative_loading_if_cleared: Optional[str] = None
    loading_pct: Optional[float] = None
    reason_summary: str = ""
    reason_codes: list[str] = Field(default_factory=list)


class CitedEvidence(BaseModel):
    claim: str
    cited_source: str
    ruling: Optional[str] = None
    cycle: Optional[int] = None


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    rules_version: str
    prompt_version: Optional[str] = None
    model: Optional[str] = None
    total_cost_usd: float = 0.0
    latency_seconds: Optional[float] = None
    tags: list[str] = Field(default_factory=list)


class AuditEntry(BaseModel):
    step: str
    actor: str  # "system" | "agent"
    timestamp: Optional[str] = None
    detail: str


class ReportOutput(BaseModel):
    """The full combined report the system returns (Appendix A / §8)."""

    model_config = ConfigDict(extra="allow")

    report_meta: dict[str, Any] = Field(default_factory=dict)
    safety_score: Optional[SafetyScore] = None
    scoring_breakdown: list[ScoringBreakdownRow] = Field(default_factory=list)
    scoring_total: dict[str, Any] = Field(default_factory=dict)
    signals: dict[str, Any] = Field(default_factory=dict)
    sections: dict[str, SectionEvaluation] = Field(default_factory=dict)
    risk_scores: Optional[RiskScores] = None
    bre_result: Optional[BreResult] = None
    risk_and_fraud_verdict: dict[str, Any] = Field(default_factory=dict)
    decision: Optional[Decision] = None
    cited_evidence_chain: list[CitedEvidence] = Field(default_factory=list)
    run_metadata: Optional[RunMetadata] = None
    audit_log: list[AuditEntry] = Field(default_factory=list)
