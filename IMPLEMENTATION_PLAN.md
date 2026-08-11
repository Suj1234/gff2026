# Implementation Plan — Onboarding Risk Assessment System

> Single-system underwriting risk engine for individual retail health insurance (India).
> One API in → one detailed report out. Deterministic rules do the bulk; a narrow LLM
> resolves only the grey-zone residue. This document is the build contract — everything
> we aligned on, in codeable detail, phase by phase, with **real rules, no mocked logic**.

Companion specs (already in repo): `files/PRD_Onboarding_Risk_Assessment_Agent.md`,
`files/Technical_Implementation_Plan.md`, `files/Agent_Build_Specification.md`,
`files/India_Health_Insurance_Data_Sources.md`, `docs/IAdore Sample Report.pdf` (the output-format reference).

---

## 0. What we are building (one paragraph)

A customer's data is collected through the onboarding journey (all API calls happen there).
That full data bundle is sent to **one endpoint**. Inside, a layered pipeline runs: deterministic
rules score and sort every case; clean cases and hard-gate cases are decided without any AI; only
**ambiguous ("grey-zone") cases** reach a single narrow LLM call that resolves the specific
ambiguity; a deterministic layer then maps everything to **one final decision** and assembles a
**detailed report** (the I-Adore-style sectioned report + a Safety Score + the technical blocks).

---

## 1. Core concepts we aligned on (the mental model — read first)

1. **Triage vs resolution are two different jobs.**
   - *Deciding a case needs judgment* (triage / routing into buckets) = **rules**.
   - *Making that judgment* (resolving the ambiguity) = **LLM**, then human if unresolved.
   - Grey-zone is **rule-detected** (a conflict or gap the rules cannot resolve), never AI-detected.

2. **One pipeline, two kinds of steps — not two systems.** Deterministic code and LLM calls are
   interleaved in a single flow. The deterministic code is the orchestrator; the LLM is a narrow
   subroutine it calls only for the residue.

3. **What goes where — the one test:** *Can you write down the correct answer for every possible
   input in advance?*
   - **Yes → deterministic** (PAN valid? income ≥ cover×N? is ICD code in declared set? AML hit?).
   - **No, needs weighing context/nuance → LLM** (is a mismatch innocent or concealment? does a
     messy note indicate a material condition? is this comparison-shopping or cover-stacking?).

4. **Most cases never reach the LLM.** Rules clear the clean majority (auto-issue) and the hard
   rejects (fraud/sanctions). The LLM runs on a small % (grey-zone). Human runs on fewer still.

5. **Structured comparison is rules; messy interpretation is LLM.** Declared-health vs ABHA when
   ABHA is coded (ICD) = a rule (set comparison via a crosswalk). When ABHA is a scanned/free-text
   note with no code = LLM extracts the fact first, *then* the rule compares. Materiality and
   intent of a mismatch = LLM.

6. **Pricing/scoring lines that never bend:**
   - The LLM **never sets a premium/loading number** — it may surface a *risk factor*; the
     deterministic **actuarial loading table** sets the number.
   - **DECLINE only ever comes from a deterministic hard gate** (fraud, sanctions, failed liveness).
     The LLM's most severe possible output is REFER.
   - The LLM never touches AML/PEP/sanctions, the STP age/SI hard gate, identity-fraud decisions,
     or sum-insured/pricing. No code path reaches them from the LLM layer — enforced structurally.

7. **Every case gets a reason report + audit trail** so "on what basis?" is always answerable —
   for approvals too, not just rejections.

8. **Facts in, judgments out — the input/agent boundary.** Upstream (vendors / analyzers) turns raw
   files into **structured facts** (BMI number, lab values + ref ranges, categorized bank
   transactions, imputed income, extracted document fields, vitals) — **with NO good/bad/risky
   labels.** Extraction / OCR / CV / statement-structuring is **not our scope.** Our system produces
   **every judgment**: risk flags & severities (rules for clear cases, LLM for ambiguous), per-section
   risk levels, the Safety Score (good/bad/average), and the decision. So there is **no analysis layer
   to build** — only the judgment layer, which is entirely ours.

   | Comes IN as a FACT (input) | We PRODUCE as a JUDGMENT (output) |
   |---|---|
   | BMI = 30 | "obese → loading" |
   | cholesterol 228, ref `<200` | severity "high" |
   | bank txn tagged "PAN-shop ₹6,400" | "smoking risk", `risk_trigger: high` |
   | imputed income 18.48L vs declared 20L | `income_mismatch` flag |
   | MCA `director_default: true` | moral-hazard flag |
   | name/DOB/address per source | `holder_mismatch` / consistency verdict |
   | lab values, vitals, credit score | section risk levels + Safety Score + decision |

---

## 2. Architecture — one system, layered, one API

```
INPUT: full collected data for one proposal (all journey API responses + flags)
                     │  POST /underwrite   (ONE endpoint)
 ┌───────────────────▼────────────────────────────────────────────────┐
 │ Layer 0  INTAKE & VALIDATE      shape + validate the bundle          │
 │ Layer 1  DETERMINISTIC RULES    per-source checkers + R-001..R-017    │  ← rules live here
 │            → hard gate hit?  → DECLINE / REFER (stop, no AI)          │
 │            → fully clean?    → ISSUE / ISSUE_WITH_LOADING (stop)      │
 │            → else: emit the ambiguous-flag list (grey-zone)          │
 │ Layer 2  SCORING                ML risk scores (fraud/anomaly/graph)  │
 │            + SHAP + per-source weighted SAFETY SCORE                  │
 │ Layer 3  LLM JUDGE              ONLY on the grey-zone flags           │  ← the only AI
 │            (one call → decision table → gather once → re-judge once)  │
 │ Layer 4  DECISION MAPPER        rules + rulings → ONE final decision  │  ← deterministic
 │            + grounding gate (every cited fact must resolve)           │
 │ Layer 5  REPORT ASSEMBLY        sections + scores + reasons + audit   │
 └───────────────────┬────────────────────────────────────────────────┘
                     │
OUTPUT: one detailed report object (JSON) — see §8
```

**One API, definitively.** Deterministic and LLM are steps *inside* one function behind one
endpoint — not separate services. External vendor APIs (PAN, ABHA, EPFO…) are **not** part of this
endpoint; they run earlier in the journey. This system receives already-collected data.

**Async note:** STEP_UP (gather evidence) can pause for hours waiting on a customer upload. v1
builds the synchronous core and returns `pending` with what it's waiting on; the durable
pause/resume (Temporal) is a later phase.

---

## 3. Data sources and what each drives (the signal surface)

From `Technical_Implementation_Plan.md §2` (API inventory). Each source has **key output variables**,
a **consent** basis, and **what consumes it**. These are the inputs the rules and scoring read.

| # | Source | Key output variables | Consent | Consumed by |
|---|---|---|---|---|
| 1 | Mobile vintage & porting | vintage_days, ported_recently | none | BRE, ML |
| 2 | Mobile fraud/revocation | on_revocation_list | none | BRE (hard gate) |
| 3 | Device fingerprint | device_id, emulator_flag, device_reuse_count | none | ML |
| 4 | Mobile→PAN reverse lookup | pan_candidates, match_confidence | none | prefill only |
| 5 | PAN verify (advanced) | name, dob, gender, masked_aadhaar, aadhaar_seeded, mobile_on_record, email, address | none | BRE (hard gate) |
| 6 | Aadhaar/DigiLocker e-KYC | full dob, address, photo | Aadhaar Act | BRE, face-match ref |
| 7 | CKYC lookup | existing_record, field-level match/mismatch | none | BRE (soft flag) |
| 8 | Liveness + face-match + deepfake | liveness_pass, face_match_score, deepfake_flag | biometric | BRE (hard gate) |
| 9 | EPFO | employer_name, tenure_months, contribution_band | none | BRE, ML |
| 10 | GST / ITR | declared_turnover/income, filing_consistency | none | BRE, ML |
| 11 | Account Aggregator | inflow_pattern, estimated_monthly_income, account_vintage | AA artifact | BRE, ML |
| 12 | MCA / director / legal | director_defaults, litigation_flags, FIR_flags | none | BRE, ML |
| 13 | Geography / pincode risk | morbidity_index, fraud_hotspot_flag, hospital_density | none | ML only (fairness-tested) |
| 14 | Velocity / cover-stacking graph | shared_device/bank/nominee_count, velocity_score | none | ML |
| 15 | Occupation hazard class | hazard_class | none | BRE |
| 16 | ABHA / ABDM health records | diagnoses, discharge_summaries, prescriptions | HIE-CM (revocable) | **agent tool** |
| 17 | rPPG facial scan | heart_rate, breathing_rate, bp_estimate, stress_indicator | explicit | **agent tool** (step-up only) |

Extended sources present in the I-Adore report (pre-policy medical exam, lab, radiology, credit
bureau, IIB portfolio, litigation/FIR) are handled the same way — one checker + one section each.

**Adapter rule:** business logic only ever sees the *internal contract shape*. A real vendor's raw
response is mapped to that shape in an adapter. Mock the vendor **response** only; never skip the step.

---

## 4. Deterministic Rule Engine (BRE) — real rules, `rules.py`

**Principle — no mocked rules:** every rule below is implemented as **real logic**. The only
placeholders allowed are (a) numeric thresholds, each tagged `# TODO(underwriting-manual)`, and
(b) a vendor's raw response payload behind an adapter. Rule *logic* is never a stub.

**Structure:** one checker function per source group, each returning a `RuleResult`:
`{ outcome: HARD_DECLINE | HARD_REFER | CLEAN | None, flags: [SoftFlag], score_inputs: {...} }`.

### 4.1 The rule table (R-001 – R-017) — implement each as a testable function

| Rule | Type | Condition (real logic; thresholds are placeholders) | Result |
|---|---|---|---|
| R-001 | Hard gate | `mobile.on_revocation_list == true` | HARD_DECLINE (fraud) |
| R-002 | Hard gate | `pan.status != "valid"` | HARD_DECLINE (invalid identity) |
| R-003 | Hard gate | `liveness.pass == false OR deepfake.flag == true OR facematch.score < 0.90*` | HARD_DECLINE (identity fraud) |
| R-004 | Hard gate | `aml_pep_sanctions.hit == true` | HARD_REFER (compliance) |
| R-005 | Hard gate | `age NOT IN product.eligible_age_band*` | HARD_REFER (manual UW) |
| R-006 | Hard gate | `sum_insured > product.stp_ceiling*` | HARD_REFER (manual UW) |
| R-007 | Soft/income | `requested_SI > income.verified_annual × N*` | soft flag `income_thin_file` |
| R-008 | Soft/thin-file | `income_source == "AA_fallback_only"` | soft flag `thin_file`; lowers auto-issue SI ceiling |
| R-009 | Loading matrix | `BMI × age_band × occupation_hazard → loading_class` (real lookup table) | assign loading class OR step-up if beyond standard matrix |
| R-010 | Non-disclosure | declared condition set vs enriched evidence (ABHA/pharmacy) — **crosswalk compare**, see §4.2 | soft flag `non_disclosure_signal` |
| R-011 | Waiting period | `product.waiting_period_trigger met` | apply exclusion/waiting-period (not a decline) |
| R-012 | Adverse selection | `velocity.cross_product_count_45d ≥ K* AND time_since_last_health_signal < 30d*` | soft flag `velocity_anomaly` |
| R-013 | ML threshold | `ml.fraud_score ≥ high_threshold*` | soft flag → grey-zone (never auto-decline off score) |
| R-014 | ML threshold | `ml.fraud_score < low_threshold* AND all hard gates pass AND zero soft flags` | AUTO-ISSUE candidate |
| R-015 | Cluster rule | `≥ 2 soft flags from {ckyc_mismatch, mobile_pan_mismatch, thin_file, moderate_ml_score, velocity, non_disclosure}` | GREY-ZONE |
| R-016 | Geography guardrail | `geography.fraud_hotspot_flag == true` **alone** | feeds ML score only; never a standalone gate/decline |
| R-017 | rPPG trigger | `rppg.consented AND rppg.vital outside normal range*` | trigger step-up (never a loading/decline input) |

`*` = threshold is a placeholder pending the underwriting manual; the comparison logic is real.

**Grey-zone (the honest definition):** a case is grey-zone when the rules detect a **conflict or
gap they cannot resolve** — i.e. R-013 (moderate/high score), R-015 (≥2 soft flags), R-007/R-010/R-012
flags that survive the hard gates but don't cleanly clear. It is a *routing* decision, not a verdict.

### 4.2 Declared-health ↔ ABHA compare (R-010) — one rule + one crosswalk, NOT 30 rules

- Health declaration is a **structured checklist** (30 yes/no conditions).
- Build **one crosswalk table** (config): declared condition → set of ICD-10 code families
  (e.g. `heart_disease → {I20–I25}`, `diabetes → {E10–E14}`, `hypertension → {I10–I15}`).
- **One rule** runs over the list: for any condition marked "No", if ABHA/pharmacy evidence contains
  a matching code (or drug→condition mapping, e.g. `Telmisartan → hypertension`), raise
  `non_disclosure_signal`. Adding a 31st question = one crosswalk row, not a new rule.
- **When ABHA is unstructured** (scanned note, free text, no code): the compare cannot run on rules
  alone → the LLM extracts the condition first (§6), then this same rule compares. Materiality/intent
  of the mismatch = LLM.

---

## 4A. Config values — industry-standard starting defaults

> Filled in per your instruction with industry-standard values for Indian retail health
> underwriting. These live as `config.py` constants and are **real, usable defaults, not blanks** —
> but still require sign-off / calibration against your actual underwriting manual before go-live.
> The ICD/drug crosswalk is taken from the **public WHO ICD-10** list.

**STP eligibility & age/SI ceilings (R-005, R-006):**
- Auto-issue age band **18–45**; ages **46–55** allowed but medicals/step-up required; **<18 or >55** → manual UW.
- Non-medical SI limit: **₹50,00,000** (age ≤45), **₹25,00,000** (46–55). STP SI ceiling (max auto-issue): **₹1,00,00,000**; above → refer.

**Income → sum-insured multiple (R-007):**

| Age | Max SI as multiple of verified annual income |
|---|---|
| ≤35 | 30× |
| 36–45 | 25× |
| 46–55 | 20× |
| 56+ | 15× |

No-income-proof SI ceiling **₹7,50,000**; requested SI above the age multiple → `income_thin_file` / step-up.

**BMI × age premium-loading matrix (R-009) — % loading:**

| BMI band | ≤35 | 36–45 | 46–55 | 56+ |
|---|---|---|---|---|
| <18.5 underweight | +5% | +5% | +10% | +15% |
| 18.5–24.9 normal | 0 | 0 | 0 | +5% |
| 25–29.9 overweight | +5% | +10% | +15% | +25% |
| 30–34.9 obese I | +15% | +25% | +35% | +50% |
| 35–39.9 obese II | +30% | +40% | +50% | REFER |
| ≥40 obese III | +50% | REFER | REFER | REFER |

Occupation hazard modifier on top: Class I non-hazardous **+0%** · Class II moderate **+10% / exclusion** · Class III hazardous **+25% / refer** · Class IV extreme **REFER**.

**ML score cutoffs (R-013, R-014), scores 0–1:** `fraud_score < 0.30` clean (auto-issue) · `0.30–0.69` grey-zone · `≥0.70` high (grey-zone, never auto-decline on score alone). `anomaly_score`/`graph_score` use the same 0.30/0.70 bands.

**Identity gate (R-003):** `face_match_score < 0.85` OR `liveness.pass == false` OR `deepfake.flag == true` → hard decline.

**Postpone window (decision row 4):** acute event / surgery / hospitalization within **90 days**, or active pregnancy → POSTPONE, re-evaluate after 3 months.

**Safety-Score bands (higher = safer):** **80–100 Low Risk** (auto-issue) · **66–79 Moderate Risk** (loading/step-up) · **0–65 High Risk** (refer/decline).

**ICD / drug crosswalk (R-010) — public WHO ICD-10:** declared condition → ICD family
(heart_disease → I20–I25, diabetes → E10–E14, hypertension → I10–I15, thyroid → E00–E07,
dyslipidemia → E78); drug→condition from a public generic list (statin → dyslipidemia,
telmisartan → hypertension, levothyroxine → hypothyroidism, metformin → diabetes).

**LLM (from `.env`, already configured):** `LLM_MODEL=openai/gpt-4o` via company gateway
`LLM_BASE_URL=https://prism-api.hinagro.com/gateway` (OpenAI-compatible). Fallbacks in `.env`:
`gemini/gemini-2.0-flash-lite`, `anthropic/claude-3-5-haiku-latest`.

---

## 5. Risk scoring & Safety Score — `scoring.py` (real, not random)

### 5.1 ML risk scores (fraud / anomaly / graph)
- Target: XGBoost (fraud/morbidity) + isolation forest (anomaly) + graph model (cover-stacking),
  each with **SHAP** feature attribution, per `Technical_Implementation_Plan.md §5`.
- **No fake scores.** Until a model is trained on labeled data, use a **documented deterministic
  heuristic scorer** (real, explainable feature-weighted function) as the interim — clearly marked,
  running in shadow. Swap to the trained model when the labeled set exists. Never a random number.

### 5.2 Safety Score (composite 0–100, higher = safer)
- `safety_score = Σ (weight_i × risk_sub_score_i)` over source groups.
- Each source group produces a `risk_sub_score` 0–100 (100 = clean/safe) from its rule results.
- **Starting weights (industry-informed — calibrate against real labeled outcomes):**

| Source group | Weight | Source group | Weight |
|---|---|---|---|
| medical | 0.22 | fraud_check | 0.08 |
| financial | 0.16 | lifestyle | 0.08 |
| identity_kyc | 0.12 | velocity_graph | 0.06 |
| occupation_employer | 0.10 | insurance_portfolio | 0.06 |
| litigation_fir | 0.05 | contactability | 0.04 |
| geography | 0.03 | **(sum)** | **1.00** |

- **Bands (industry-standard default, see §4A):** 80–100 Low Risk · 66–79 Moderate Risk · 0–65 High Risk.
- Weights, sub-scores, and bands are industry-informed starting values — calibrate against real outcomes.

---

## 6. LLM Judge — grey-zone resolution only — `judge.py`

- One narrow DSPy signature `GreyZoneJudge` (`dspy.ChainOfThought`, **not** `dspy.ReAct`).
- Inputs: `evidence_bundle`, `ambiguous_flags` (the specific flags only), `follow_up_observations`.
- Output: one `FlagRuling` per flag. Ruling ∈ `{ benign_explained, needs_income_corroboration,
  needs_medical_check, needs_identity_reverification, unresolvable_escalate }`.
- The prompt forces: rule on every flag; use only given evidence; `benign_explained` only with a
  cited specific fact; if nothing resolves it → `unresolvable_escalate`, never guess.
- **Also used for unstructured extraction** (R-010 messy-ABHA case): read a scanned/free-text
  record → emit a structured fact the rules can then compare.
- **Production discipline:** disable DSPy call-history retention (OOM risk); LLM caching OFF in prod,
  ON in eval; stamp model + prompt version on every output.

---

## 7. Decision logic — how each of the 6 outcomes is reached — `decision.py`

Outcome set (**Core 6**): `ISSUE · ISSUE_WITH_LOADING · STEP_UP · POSTPONE · REFER · DECLINE`.
First matching row wins:

| # | Condition | Layer | Outcome |
|---|---|---|---|
| 1 | Fraud / failed liveness / invalid identity | Rules (hard gate) | **DECLINE** |
| 2 | AML / PEP / sanctions hit | Rules (hard gate) | **REFER** |
| 3 | Age / SI outside band | Rules (hard gate) | **REFER** |
| 4 | Recent medical event inside postpone window* | Rules | **POSTPONE** |
| 5 | BMI×age×occupation (or a confirmed condition) exceeds standard matrix, otherwise acceptable | Rules (+LLM fact) | **ISSUE_WITH_LOADING** (actuarial table sets the %) |
| 6 | All checks clean, low score, zero flags | Rules | **ISSUE** |
| 7 | Grey-zone → LLM rules all flags benign | LLM→Rules | **ISSUE** |
| 8 | LLM says a flag needs a document / medical record | LLM→Rules | **STEP_UP** (gather once, re-judge) |
| 9 | After the one STEP_UP cycle, still unresolved | LLM→Rules | **REFER** |
| 10 | LLM rules a flag `unresolvable_escalate` | LLM→Rules | **REFER** |

**Hard lines:** rows 1–6 never call the LLM; **DECLINE only from row 1**; the LLM never sets a
loading number (row 5's % comes from the table).

### 7.1 Decision-table + gather + grounding (the pipeline internals)
- `decide_next_step(rulings, cycle)` → `FINALIZE | GATHER_EVIDENCE | ESCALATE` (cycle capped at 2).
- `GATHER_EVIDENCE` maps flag type → real action: income → `request_additional_document(bank_statement)`;
  medical → `request_abha_consent()`; identity → `request_identity_reverification()`. The **action is
  real code** calling the gateway; only the vendor response is mocked in dev.
- **Grounding gate (with the fix):** every `cited_evidence` path must resolve against the real
  bundle/observations — **including on the escalate path** (today's code skips this; the plan fixes
  it so a hallucinated escalation reason can't cite fake evidence).

---

## 8. The output — one detailed report (JSON)

The output combines the **I-Adore report** (header, Safety Score, per-section evaluations with risk
levels and per-source findings, underwriting decision) with the **six technical blocks** (`signals`,
`risk_scores`, `bre_result`, `risk_and_fraud_verdict`, `decision`, `run_metadata` + `audit_log`).

The full worked **output** example (Rohit Kishan Sharma, Safety Score 65, decision **REFER** —
confirmed non-disclosure) is in **Appendix A**; the full worked **input** example is in **Appendix B**.
Both are inline in this file — no separate JSON files.

Top-level keys: `report_meta`, `safety_score`, `scoring_breakdown`, `scoring_total`, `signals`,
`sections`, `risk_scores`, `bre_result`, `risk_and_fraud_verdict`, `decision`, `cited_evidence_chain`,
`run_metadata`, `audit_log`.

---

## 9. Code structure

```
underwriting/
  __init__.py
  config.py        # thresholds (# TODO underwriting-manual), safety-score weights, ICD crosswalk, loading matrix, band cutoffs
  schemas.py       # Pydantic: ProposalInput/EvidenceBundle, per-source models, AmbiguousFlag, FlagRuling,
                   #           SafetyScore, SectionEvaluation, Decision, ReportOutput, RunMetadata, AuditEntry
  sources/         # one adapter per data source → maps raw vendor response → internal contract shape
    identity.py  income.py  health.py  enrichment.py  ...
  rules.py         # REAL per-source checkers + R-001..R-017 + the R-010 crosswalk compare
  scoring.py       # ml risk scores (real heuristic → trained model) + SHAP + safety-score weighting
  judge.py         # GreyZoneJudge DSPy signature + run_judge + unstructured-record extraction
  decision.py      # decide_next_step + final decision mapper (Core 6) + grounding gate (fixed)
  report.py        # assemble the full combined output JSON (§8)
  pipeline.py      # orchestrator: intake → rules → scoring → (grey-zone? judge→gather→judge) → decision → report
  api.py           # single endpoint: POST /underwrite
  tests/
    fixtures/      # real labeled cases: rohit, vikram, suresh, priya, anjali (+ grow)
    test_rules.py  test_scoring.py  test_decision.py  test_pipeline.py  test_grounding.py
```

Maps to the current `agent.py` "7 parts": schemas→`schemas.py`, GreyZoneJudge→`judge.py`,
decision table + grounding gate→`decision.py`, pipeline→`pipeline.py`. The missing "Part 0" (the
per-source rules) becomes `rules.py` + `sources/`.

---

## 10. Phase-wise build plan (real rules & data — no mocked logic)

Each phase ships something runnable and testable. Thresholds are placeholder-marked; **logic is real**.

### Phase 0 — Scaffolding, schemas & the eval seed
- Package layout (§9), `config.py` skeleton, `schemas.py` — Pydantic models for **both** the input
  contract (Appendix B) and the output report (Appendix A), field-for-field.
- **5 fixture pairs** under `tests/fixtures/`, each `{ input, expected }` (the eval seed / "claim master"):

  | Fixture | Case | Expected decision |
  |---|---|---|
  | `suresh_salaried_clean` | salaried, all clean | ISSUE |
  | `rohit_self_employed` | non-disclosure (Appendix B) | REFER |
  | `anjali_thin_file` | income needs a document | STEP_UP → ISSUE |
  | `vikram_velocity` | velocity + undisclosed cardiac | REFER |
  | `fraud_deepfake` | failed liveness / deepfake | DECLINE |

- **Expected-label shape** (from Office-Hours D1): `expected: { decision, expected_rulings: [{flag_id, ruling, must_cite?}], expected_outcome }`.
- **Scope boundary — facts in, judgments out (see §1.8).** Upstream delivers **analyzed facts**
  (categorized bank transactions, imputed income, BMI number, lab values + ref ranges, extracted
  document fields, vitals) with **no verdicts**. We do **not** build the analyzers (BSA engine,
  CV/BMI model, OCR). We **do** build the entire judgment layer: every `risk_trigger`, `severity`,
  `*_flag`, `holder_mismatch`/consistency verdict, per-section risk level, the Safety Score, and the
  decision. → **Phase 0 finalizes a facts-only input schema** (strips the verdict fields that
  Appendix B currently shows inline for illustration).
- **Deferred — `# TODO(consistency-spec)`:** the consistency-check matching rule (how to compare
  name/DOB/address across sources — proposed: fuzzy/token name match, exact DOB, normalized
  address) is to be detailed before Phase 1's consistency rule is coded. Noted here per decision to defer.
- **Done when:** all 5 fixtures parse against the schemas; `pytest` runs.

### Phase 1 — Deterministic rule engine (NO AI)
- Implement `sources/` adapters (internal contract shape) and `rules.py`: every checker + R-001..R-017
  + the R-010 ICD/drug crosswalk. Real logic; thresholds tagged.
- `decision.py`: rows 1–6 + 9–10 of §7 (the non-LLM outcomes) and `decide_next_step`.
- **Done when:** each rule has a "fires / doesn't fire" test; Rohit → flags + GREY-ZONE, Suresh → ISSUE,
  a fraud fixture → DECLINE, an AML fixture → REFER — all with **no AI**.

### Phase 2 — Scoring & Safety Score
- `scoring.py`: real heuristic risk scorer (fraud/anomaly/graph) with attribution + the weighted
  Safety Score (§5). Bands + `scoring_breakdown`.
- **Done when:** Rohit's Safety Score computes to ~65/High from the weight table; every score carries attribution.

### Phase 3 — LLM judge + grey-zone pipeline
- `judge.py` (real DSPy signature + prompt), unstructured-record extraction for messy ABHA.
- `pipeline.py`: BRE → judge → decision table → **one** gather cycle → re-judge → grounding gate.
- Fix the grounding hole (check citations before trusting escalate).
- **Done when:** Vikram runs end-to-end (grey-zone → judge → REFER with grounded citations);
  a fabricated-citation test escalates on `grounding_check_failed`.

### Phase 4 — Report assembly & API
- `report.py`: assemble the full §8 JSON (sections + technical blocks + reason report + audit log).
- `api.py`: `POST /underwrite` → the report object; `pending` state for STEP_UP.
- **Done when:** one call on each fixture returns the complete report JSON validating against the schema.

### Robustness — folded into Phases 3 & 4 (not a standalone phase)
The former standalone "Robustness" step is done where it belongs: grounding + confidence gates in
**Phase 3**; version stamps, append-only audit log, partial-data handling, idempotency in **Phase 4**.

### Phase 5 — Eval harness & real data
- Growing labeled case set replayed on every change (caching on). Add every mishandled prod case.
- Track the accuracy triad (false-benign, over-escalation, grounding-hallucination — Office-Hours D1).
- Real vendor adapters swapped in behind the internal contract; keep mocks passing.
- **Done when:** the suite fails a bad rule/prompt change before prod; real adapters pass the same fixtures.

### Phase 6 — Durable async orchestration + real ML models · post-v1
- Wrap the pipeline in a **Temporal** workflow so STEP_UP can pause for hours (consent / document-upload
  waits) and resume with the real observation — replacing the synchronous `pending`.
- Train the real **ML models** (XGBoost / isolation-forest / graph) on the Phase-5 labeled set, run in
  shadow, and swap in for the interim heuristic scorer once they beat it.
- **Gated on:** a deployment context with real long-waits + enough labeled data to train.

### Phase 7 — Channel + human-review dashboard · post-v1
- **WhatsApp Cloud API + Flows** for the customer journey (capture, media/selfie upload, templates).
- The **underwriter queue / dashboard** where REFER cases land with the full ruling trace + evidence.
- **Gated on:** WABA/BSP access; a UI decision.
- **Done when:** a case flows end-to-end WhatsApp → decision → (if REFER) the dashboard.

---

## 11. Robustness requirements (bake in from Phase 1, not after)

- Reason code + human-readable reason on **every** rule and decision.
- Append-only audit log: every rule fired, LLM in/out, every citation, versions, cost, timestamps.
- Grounding check on citations, **including the escalate path** (the fix).
- Reproducibility: LLM caching on in eval; version stamps; disable DSPy call-history in prod (OOM).
- Confidence gate: calibrated (vs eval set, not model self-report) → low routes to REFER.
- Partial data is the normal case, not an exception — BRE and judge reason over "unavailable" fields.
- Consent gating: ABHA/rPPG/AA go through the real consent request/response flow even against mocked
  vendors (mock the response, never the step).
- Idempotency: `proposal_id` + same data → same decision on retry.

---

## 12. Decisions — resolved & remaining

**Resolved (folded into §4A / §5):**
1. ✅ **Underwriting numbers** — industry-standard defaults added (§4A); calibrate vs the real manual later.
2. ✅ **Safety-Score weights & bands** — industry-informed defaults (§5 + §4A).
3. ✅ **ICD/drug crosswalk** — public WHO ICD-10 code families (§4A).
4. ✅ **LLM** — `openai/gpt-4o` via company gateway, from `.env`.
5. ✅ **I-Adore alignment** — Option B: core sections always present; optional sections
   (radiology, vehicle, rPPG, lab) rendered only when that data exists.

**Still open (do not block coding — sensible defaults chosen):**
6. **Vendor selection per source** — building against the internal contract with adapters; real vendors swap in later (Phase 6).
7. **DSPy version** — default to latest stable unless you want `3.3.0b1` pinned for reference parity.

---

## Appendix A — Full example output JSON (Rohit Kishan Sharma)

> Output — the detailed report the system returns. The matching input is in Appendix B.

```json
{
  "report_meta": {
    "applicant_name": "Rohit Kishan Sharma",
    "application_no": "110L156V03",
    "product_name": "Secure Insure",
    "sum_assured": 10000000,
    "premium": 31000,
    "report_date": "2026-06-03",
    "profile": { "gender": "male", "age": 32, "marital_status": "married",
                 "education": "under_graduate", "location": "Mumbai-400032",
                 "occupation_type": "salaried_and_self_employed" }
  },

  "safety_score": {
    "value": 65, "band": "High Risk", "scale": "0-100, higher = safer",
    "method": "weighted_sum_of_per_source_sub_scores",
    "bands": { "low_risk": "80-100", "moderate_risk": "66-79", "high_risk": "0-65" },
    "_note": "weights and sub_scores are illustrative placeholders — calibrate against the underwriting manual + labeled outcomes"
  },

  "scoring_breakdown": [
    { "source_group": "identity_kyc",        "weight": 0.12, "risk_sub_score": 70, "contribution": 8.40, "why": "facematch/liveness ok, but name+DOB+address mismatched" },
    { "source_group": "contactability",      "weight": 0.04, "risk_sub_score": 90, "contribution": 3.60, "why": "email 7.3y old, low spam; mobile holder-name mismatch" },
    { "source_group": "occupation_employer", "weight": 0.10, "risk_sub_score": 60, "contribution": 6.00, "why": "EPF ok, but MCA director = defaulter, GST delay" },
    { "source_group": "financial",           "weight": 0.16, "risk_sub_score": 55, "contribution": 8.80, "why": "declared 20L vs imputed 18.5L, cash-deposit>salary, luxury-vehicle triggers" },
    { "source_group": "lifestyle",           "weight": 0.08, "risk_sub_score": 45, "contribution": 3.60, "why": "smoking, alcohol, gambling, adventurous activity spends" },
    { "source_group": "medical",             "weight": 0.22, "risk_sub_score": 60, "contribution": 13.20, "why": "dyslipidemia, hypothyroid, anaemia, past polytrauma, misrepresentation" },
    { "source_group": "velocity_graph",      "weight": 0.06, "risk_sub_score": 80, "contribution": 4.80, "why": "no significant cover-stacking pattern" },
    { "source_group": "geography",           "weight": 0.03, "risk_sub_score": 85, "contribution": 2.55, "why": "pincode not a fraud hotspot" },
    { "source_group": "litigation_fir",      "weight": 0.05, "risk_sub_score": 70, "contribution": 3.50, "why": "1 criminal + 1 civil, probable-match medium confidence" },
    { "source_group": "fraud_check",         "weight": 0.08, "risk_sub_score": 60, "contribution": 4.80, "why": "no tampering, but income-source + personal-detail inconsistency" },
    { "source_group": "insurance_portfolio", "weight": 0.06, "risk_sub_score": 80, "contribution": 4.80, "why": "2 existing health policies, IIB claim match" }
  ],
  "scoring_total": { "sum_of_weights": 1.00, "computed_safety_score": 65.0 },

  "signals": {
    "mobile_vintage":     { "vintage_months": 19, "ported_recently": false, "consent": "none", "consumed_by": ["BRE", "ML"], "result": "clean" },
    "mobile_fraud":       { "on_revocation_list": false, "consent": "none", "consumed_by": ["BRE"], "result": "R-001 pass" },
    "device_fingerprint": { "emulator_flag": false, "device_reuse_count": 0, "consent": "none", "consumed_by": ["ML"], "result": "clean" },
    "mobile_to_pan":      { "match_confidence": 0.61, "consent": "none", "consumed_by": ["prefill"], "result": "weak_link" },
    "pan_verify":         { "pan": "FWIPS4634L", "name_match": false, "dob_match": false, "address_match": false, "aadhaar_seeded": true, "consent": "none", "consumed_by": ["BRE"], "result": "R-002 pass, 3 soft mismatches" },
    "aadhaar_ekyc":       { "dob_verified": true, "address_verified": true, "photo": true, "consent": "aadhaar_act", "consumed_by": ["BRE"], "result": "verified" },
    "ckyc":               { "existing_record": true, "address_match": false, "consent": "none", "consumed_by": ["BRE"], "result": "ckyc_mismatch flag" },
    "liveness_facematch": { "liveness_pass": true, "face_match_score": 0.9597, "liveness_score": 0.8088, "deepfake_flag": false, "consent": "biometric", "consumed_by": ["BRE"], "result": "R-003 pass" },
    "epfo":               { "employer": "Perfios Software Solutions Private Limited", "date_of_joining": "2023-05", "tenure_years": 2, "epf_deducted_45d": true, "consent": "none", "consumed_by": ["BRE", "ML"], "result": "verified" },
    "gst_itr":            { "gstin": "27***********A", "entity": "Roko Brokers", "turnover_slab": "40L-1.5Cr", "gst_transaction_delay": true, "itr_taxable_income": 1995000, "filing_consistency": "delayed", "consent": "none", "consumed_by": ["BRE", "ML"], "result": "gst_delay flag" },
    "account_aggregator": { "imputed_annual_income": 1848000, "avg_monthly_balance": 80000, "expense_to_income": 0.455, "salary_credit_monthly": 142927, "consent": "aa_artifact", "consumed_by": ["BRE", "ML"], "result": "income_mismatch flag" },
    "mca_director_legal": { "din": "10312312", "din_status": "approved", "director_default": true, "entity": "Smart Workers Private Limited", "consent": "none", "consumed_by": ["BRE", "ML"], "result": "mca_defaulter flag" },
    "geography":          { "morbidity_index": 0.38, "fraud_hotspot_flag": false, "hospital_density": "high", "consent": "none", "consumed_by": ["ML"], "result": "clean" },
    "velocity_graph":     { "velocity_score": 0.31, "shared_device_count": 0, "related_proposals": [], "consent": "none", "consumed_by": ["ML"], "result": "clean" },
    "occupation_hazard":  { "hazard_class": "non_hazardous", "consent": "none", "consumed_by": ["BRE"], "result": "clean" },
    "abha_health_records":{ "diagnoses": ["dyslipidemia", "hypothyroidism", "iron_deficiency_anaemia"], "prescriptions": ["thyroid_med", "statin", "antihypertensive"], "consent": "hie_cm", "consumed_by": ["agent"], "result": "non_disclosure flag" },
    "rppg_scan":          { "heart_rate": 72, "respiratory_rate": 16, "hrv_ms": 42, "stress_index": 0.38, "bp": "118/76", "hemoglobin": 14.1, "spo2": 98, "consent": "explicit", "consumed_by": ["agent"], "result": "step_up_only" }
  },

  "sections": {
    "identity_checks":    { "risk_level": "Moderate", "facematch_pct": 95.97, "liveness_pct": 80.88, "kyc_verified": true, "pan_aadhaar_linked": true, "name_match": false, "dob_match": false, "address_match": false, "pep": false, "compliance_206ab": true },
    "contactability":     { "risk_level": "Low", "mobile": { "number": "+91-8710830213", "status": "active", "provider": "Jio", "connection": "prepaid", "sim_activation": "2012-02", "holder_name": "Kishan V Sharma", "holder_mismatch": true }, "email": { "id": "rohit.s@gmail.com", "created": "2008-04-22", "spam": false, "fraud_risk": "very_low" } },
    "occupation_self_employed": { "risk_level": "Moderate", "entity": "Roko Brokers", "type": "proprietorship", "business_type": "agency_services", "registered": "2018-10-01", "turnover_slab": "40L-1.5Cr", "gst_transaction_delay": true, "mca_director_default": true },
    "financial_evaluation": { "risk_level": "High", "declared_income": 2000000, "imputed_income_bsa": 1848000, "salary_slip_income": 1664000, "itr_income": 1995000, "credit_score": 722, "total_outstanding": 3600000, "vehicle_idv": 1596000, "triggers": [ { "finding": "irregular_salary_credits", "category": "behavioural", "risk": "high", "source": "bank_statement" }, { "finding": "cash_deposit_gt_max_salary", "category": "transactional", "risk": "high", "source": "bank_statement" }, { "finding": "luxury_vehicle_vs_income", "category": "authenticity", "risk": "medium", "source": "bank_statement" }, { "finding": "income_declared_vs_bsa_mismatch", "category": "authenticity", "risk": "high", "source": "bank_statement" } ] },
    "lifestyle_analysis": { "risk_level": "High", "risk_spends_pct": 5.5, "wellness_spends_pct": 0.0, "indicators": [ { "indicator": "smoking_pan_shop", "frequency": "weekly", "severity": "high" }, { "indicator": "gambling", "frequency": "occasional", "severity": "high" }, { "indicator": "alcohol", "frequency": "occasional", "severity": "low" }, { "indicator": "adventurous_activity", "frequency": "occasional", "severity": "moderate" } ] },
    "medical_evaluation": { "risk_level": "High", "bmi": 30, "bp": "118/76", "conditions": [ { "condition": "dyslipidemia", "severity": "moderate" }, { "condition": "hypothyroidism", "severity": "moderate" }, { "condition": "iron_deficiency_anaemia", "severity": "moderate" }, { "condition": "chronic_systemic_inflammation", "severity": "moderate" }, { "condition": "historical_polytrauma", "severity": "moderate" } ], "lab": [ { "test": "hemoglobin", "result": 11.8, "ref": "13.5-17.5", "severity": "low" }, { "test": "TSH", "result": 5.1, "ref": "0.4-4.5", "severity": "high" }, { "test": "total_cholesterol", "result": 228, "ref": "<200", "severity": "high" }, { "test": "fasting_glucose", "result": 102, "ref": "70-99", "severity": "high" } ], "questionnaire_declared_conditions": "none", "misrepresentation_flag": true },
    "consistency_check":  { "risk_level": "High", "name_match": false, "dob_match": false, "address_match": false, "sources_checked": ["pan", "proposal_form", "mobile", "bank_statement", "salary_slip", "itr", "bureau", "medical_exam", "vehicle"] },
    "litigation_fir":     { "risk_level": "Moderate", "total_cases": 2, "criminal_cases": 1, "firs": 0, "confidence": "medium_probable_match", "cases": [ { "type": "succession", "civil_criminal": "civil", "severity": "medium", "status": "disposed" }, { "type": "warrant_summons", "civil_criminal": "criminal", "severity": "high", "status": "disposed" } ] },
    "fraud_check":        { "risk_level": "Moderate", "red_flags": 0, "yellow_flags": 2, "green_flags": 1, "document_tampering": false, "income_source_mismatch": true, "personal_detail_inconsistency": true },
    "insurance_portfolio_iib": { "existing_policies": [ { "type": "health", "insurer": "HDFC ERGO", "premium": 3000, "frequency": "monthly", "source": "bank_statement" }, { "type": "health", "insurer": "ICICI Lombard", "premium": 1200, "frequency": "yearly", "source": "bank_statement" } ], "iib": { "claim_match": true, "num_policies": 2, "num_insurers": 2 } }
  },

  "risk_scores": {
    "fraud_score": 0.71, "anomaly_score": 0.66, "graph_score": 0.30, "composite_band": "high",
    "shap": { "income_declared_vs_bsa_mismatch": 0.22, "medical_misrepresentation": 0.19, "identity_field_mismatch": 0.14, "mca_director_default": 0.11 }
  },

  "bre_result": {
    "outcome": "GREY-ZONE",
    "ambiguous_flags": [
      { "flag_id": "flg_001", "flag_type": "income_thin_file", "related_rule": "R-007" },
      { "flag_id": "flg_002", "flag_type": "non_disclosure_signal", "related_rule": "R-010" },
      { "flag_id": "flg_003", "flag_type": "identity_mismatch", "related_rule": "R-015" }
    ]
  },

  "risk_and_fraud_verdict": {
    "risk_summary": "Elevated long-term morbidity (dyslipidemia + hypothyroid + past polytrauma) plus high-risk lifestyle.",
    "fraud_summary": "No synthetic-identity fraud; income authenticity and identity-field mismatches present.",
    "non_disclosure": "Declared 'no medical history' but ABHA/lab evidence shows multiple active conditions.",
    "confidence_band": "high"
  },

  "decision": {
    "verdict": "REFER", "escalation_reason": "non_disclosure_confirmed",
    "indicative_loading_if_cleared": "40-60%",
    "reason_summary": "Confirmed medical non-disclosure (declared 'no history' vs lab/exam evidence) routes to a human underwriter per policy; income-authenticity + lifestyle add risk. (I-Adore displayed a load; our rule refers confirmed non-disclosure — decision #2.)",
    "reason_codes": ["R-010-nondisclosure", "R-007-income", "R-009-loading", "medical_misrepresentation"]
  },

  "cited_evidence_chain": [
    { "claim": "medical misrepresentation", "cited_source": "sections.medical_evaluation.misrepresentation_flag", "ruling": "needs_medical_check", "cycle": 1 },
    { "claim": "income authenticity", "cited_source": "signals.account_aggregator.imputed_annual_income", "ruling": "needs_income_corroboration", "cycle": 1 }
  ],

  "run_metadata": {
    "rules_version": "v1", "prompt_version": "v1", "model": "openai/gpt-4o",
    "per_stage": { "judge_cycle_1": { "input_tokens": 2400, "output_tokens": 380, "total_cost_usd": 0.00048 } },
    "total_cost_usd": 0.00048, "latency_seconds": 2.7,
    "tags": ["NON_DISCLOSURE", "INCOME_AUTHENTICITY", "REFERRED"]
  },

  "audit_log": [
    { "step": "bre",           "actor": "system", "timestamp": "2026-06-03T10:00:01Z", "detail": "3 ambiguous flags -> GREY-ZONE" },
    { "step": "judge_cycle_1", "actor": "agent",  "timestamp": "2026-06-03T10:00:04Z", "detail": "needs_medical_check + needs_income_corroboration" },
    { "step": "decision",      "actor": "system", "timestamp": "2026-06-03T10:00:05Z", "detail": "REFER — confirmed non-disclosure to underwriter" }
  ]
}
```

---

## Appendix B — Full example input JSON (Rohit Kishan Sharma)

> Input — the raw collected data the system receives: the full **declared proposal form** + every
> enrichment source that fired (**30 sources**, each with a `status`) + **consents** + document refs.
> This is exactly what `POST /underwrite` accepts. `status ∈ available | unavailable | not_applicable
> | consent_declined | not_requested` handles the fact that no two applicants send the same data.
>
> **Boundary note (§1.8):** fields shown here that are *verdicts* — `risk_triggers`, `severity`,
> `holder_mismatch`, `field_match`, `gst_transaction_delay`, other `*_flag`s — are **system outputs,
> shown inline only to illustrate the full picture.** The true input carries the underlying **facts
> only** (raw transactions, values, per-source name/DOB/address); the facts-only input schema is
> finalized in Phase 0.

```json
{
  "proposal_id": "110L156V03",
  "meta": { "channel": "whatsapp", "received_at": "2026-06-03T09:00:00Z", "insurer": "Secure Insure", "product_name": "Secure Insure Health", "persona": "self_employed" },

  "application": {
    "applicant": { "name": "Rohit Kishan Sharma", "dob": "1990-05-21", "gender": "male", "age": 32, "marital_status": "married", "spouse_name": "Ridhima Sharma", "education": "post_graduate", "address": "C-705, One Kalpataru Towers, Rokadia Lane, MG Road, Bandra, 400084", "pincode": "400084" },
    "occupation": { "declared_type": "self_employed", "declared_occupation": "business_owner", "industry": "others", "employer_declared": "Perfios Software Solutions Private Limited" },
    "product": { "type": "individual_health", "sum_assured": 10000000, "premium": 31000, "tenure_years": 1, "payment_mode": "annual", "proposer_type": "individual", "proposal_type": "employer_employee", "relationship_with_assured": "self" },
    "financial": { "declared_annual_income": 2000000, "purpose_of_cover": "family_protection", "source_of_funds": "business_income" },
    "nominee": { "name": "Ridhima Sharma", "relationship": "spouse" },
    "existing_cover_declared": [],
    "declared_pep": false,
    "health_declaration": { "height_cm": 180, "weight_kg": 80, "bmi": 25.2, "conditions": [], "tobacco": false, "alcohol": false, "drugs": false, "past_medical_history": "none", "ongoing_medication": "none", "family_history": [], "substance_details": "none" }
  },

  "consents": [
    { "type": "general_application", "framework": "DPDP_2023", "granted": true,  "timestamp": "2026-06-03T09:00:00Z", "version": "v3" },
    { "type": "aadhaar_ekyc",        "framework": "Aadhaar_Act", "granted": true, "timestamp": "2026-06-03T09:02:00Z", "version": "v1" },
    { "type": "ckyc",                "framework": "CKYC_OTP",    "granted": true, "timestamp": "2026-06-03T09:03:00Z", "version": "v1" },
    { "type": "account_aggregator",  "framework": "RBI_AA",      "granted": true, "timestamp": "2026-06-03T09:06:00Z", "version": "v2" },
    { "type": "credit_bureau",       "framework": "CICRA_2005",  "granted": true, "timestamp": "2026-06-03T09:06:30Z", "version": "v1" },
    { "type": "abha_hie_cm",         "framework": "ABDM_HIE_CM", "granted": false, "timestamp": "2026-06-03T09:07:00Z", "version": "v1" }
  ],

  "signals": {
    "pan_verify":         { "status": "available", "pan": "FWIPS4634L", "name": "Rohit Kishan Sharma", "dob": "1990-05-21", "gender": "male", "masked_aadhaar": "XXXX1234", "aadhaar_seeded": true, "address": "705-C, Kalpataru Towers, Rokadia Lane, MG Road, Bandra West, 400084", "mobile_on_record": "+91-87108xxxxx", "email_on_record": "rohit.s@gmail.com" },
    "aadhaar_ekyc":       { "status": "available", "name": "Rohit Kishan Sharma", "dob": "1990-05-21", "address": "C-705, One Kalpataru Towers, Bandra, 400084", "photo": true },
    "ckyc":               { "status": "available", "existing_record": true, "name": "Rohit Kishan Sharma", "dob": "1990-05-21", "address": "C-705, One Kalpataru Towers, Bandra West, 400084", "field_match": { "name": true, "dob": true, "address": false } },
    "liveness_facematch": { "status": "available", "liveness_pass": true, "liveness_score": 0.8088, "face_match_score": 0.9597, "deepfake_flag": false },
    "video_kyc":          { "status": "not_requested" },
    "mobile_intel":       { "status": "available", "number": "+91-8710830213", "line_status": "active", "provider": "Jio", "connection_type": "prepaid", "network_location": "Bengaluru", "sim_activation": "2012-02", "vintage_months": 19, "ported_recently": false, "on_revocation_list": false, "holder_name": "Kishan V Sharma", "holder_dob": "1962-10-03", "holder_mismatch": true },
    "email_intel":        { "status": "available", "email": "rohit.s@gmail.com", "line_status": "active", "created": "2008-04-22", "spam": false, "fraud_risk": "very_low", "domain_disposable": false },
    "device_fingerprint": { "status": "available", "device_id": "d8f2...", "emulator_flag": false, "device_reuse_count": 0 },
    "epfo":               { "status": "available", "name": "Rohit Sharma", "employer": "Perfios Software Solutions Private Limited", "date_of_joining": "2023-05", "tenure_years": 2, "contribution_band": "18-20L", "epf_deducted_last_45d": true },
    "gst":                { "status": "available", "gstin": "27***********A", "entity": "Roko Brokers", "sole_proprietor": "Rohit Sharma", "registration_date": "2018-10-01", "turnover_slab": "40L-1.5Cr", "firm_type": "proprietorship", "business_type": "agency_services", "gst_transaction_delay": true },
    "itr":                { "status": "available", "name": "Rohit Kishan Sharma", "taxable_income_by_year": [ { "fy": "2022-23", "total": 1720000 }, { "fy": "2023-24", "total": 1900000 }, { "fy": "2024-25", "total": 1990000 } ], "latest_total_taxable_income": 1995000 },
    "salary_slip":        { "status": "available", "name": "Rohit K Sharma", "months": [ { "m": "2025-01", "net": 142927 }, { "m": "2025-02", "net": 142727 }, { "m": "2025-03", "net": 130500 } ], "estimated_annual": 1664616, "employer": "Perfios Software Solutions Private Limited" },
    "account_aggregator": { "status": "available", "name": "Rohit Kishan Sharma", "address": "C-705, Kalpataru Towers, Bandra West, Mumbai", "period": "2025-01/2025-06", "imputed_annual_income": 1848000, "avg_monthly_balance": 80000, "expense_to_income": 0.455, "total_surplus": 154927,
                            "credits": [ { "type": "salary", "amount": 142927, "freq": "monthly", "regular": true }, { "type": "investment_income", "amount": 2000, "freq": "monthly", "regular": true }, { "type": "rental_income", "amount": 10000, "freq": "monthly", "regular": true } ],
                            "debits":  [ { "type": "emi", "amount": 33000, "freq": "monthly" }, { "type": "credit_card", "amount": 33000, "freq": "monthly" }, { "type": "investment", "amount": 10000, "freq": "monthly" }, { "type": "rent", "amount": 10000, "freq": "monthly" } ],
                            "risk_triggers": [ { "finding": "irregular_salary_credits", "category": "behavioural", "risk": "high" }, { "finding": "cash_deposit_gt_max_salary", "category": "transactional", "risk": "high" }, { "finding": "immediate_cash_withdrawal_post_deposit", "category": "behavioural", "risk": "high" }, { "finding": "luxury_vehicle_vs_income", "category": "authenticity", "risk": "medium" }, { "finding": "cash_withdrawal_on_holidays", "category": "authenticity", "risk": "medium" } ],
                            "lifestyle_spends": { "risk_spends": { "amount": 7400, "pct_income": 5.5, "indicators": [ { "indicator": "smoking_pan_shop", "frequency": "weekly", "severity": "high" }, { "indicator": "gambling", "frequency": "occasional", "severity": "high" }, { "indicator": "alcohol", "frequency": "occasional", "severity": "low" }, { "indicator": "adventurous_activity", "frequency": "occasional", "severity": "moderate" } ] }, "medical_spends": { "amount": 1200, "pct_income": 1.2 }, "wellness_spends": { "amount": 0, "pct_income": 0.0 } } },
    "credit_bureau":      { "status": "available", "name": "Rohit Kishan Sharma", "score": 722, "estimated_income": 2373432, "total_outstanding": 3600000, "total_monthly_obligation": 98893, "active_accounts": 7, "tradelines_considered": 2, "overdue_accounts": 0, "recent_inquiries": 1 },
    "vehicle":            { "status": "available", "name": "Rohit Kishan Sharma", "make_model": "Maruti Suzuki Grand Vitara Smart Hybrid Zeta", "manufactured_year": 2023, "registration_no": "MH26FA2442", "idv": 1596876, "idv_based_income": 4790628, "rc_status": "active", "vehicle_type": "motor_car_lmv" },
    "occupation_hazard":  { "status": "available", "hazard_class": "non_hazardous" },
    "mca_director":       { "status": "available", "name": "Rohit Sharma", "din": "10312312", "din_status": "approved", "director_default": true, "entity": "Smart Workers Private Limited", "entity_address": "Y-E902, Bandra Kurla Complex, 400054" },
    "litigation_fir":     { "status": "available", "total_cases": 2, "firs_registered": 0, "confidence": "medium_probable_match", "cases": [ { "type": "succession", "court": "district", "civil_criminal": "civil", "severity": "medium", "status": "disposed" }, { "type": "warrant_summons_criminal", "court": "district", "civil_criminal": "criminal", "severity": "high", "status": "disposed" } ] },
    "background_verification": { "status": "not_requested" },
    "iib":                { "status": "available", "note": "representation only — real-time IIB health granularity unconfirmed", "claim_match": true, "num_policies": 2, "num_insurers": 2, "policies": [ { "product": "motor", "status": "active", "term_years": 3, "vintage_years": 6, "channel": "digital" }, { "product": "health", "status": "active", "term_years": 2, "vintage_years": 0, "channel": "broker" } ], "existing_health_from_bank_statement": [ { "insurer": "HDFC ERGO", "premium": 3000, "frequency": "monthly" }, { "insurer": "ICICI Lombard", "premium": 1200, "frequency": "yearly" } ] },
    "geography":          { "status": "available", "pincode": "400084", "morbidity_index": 0.38, "fraud_hotspot_flag": false, "hospital_density": "high", "aqi_band": "moderate", "disease_outbreak_flag": false },
    "velocity_graph":     { "status": "available", "velocity_score": 0.31, "shared_device_count": 0, "shared_bank_count": 0, "shared_nominee_count": 0, "related_proposals": [] },
    "pep_sanctions":      { "status": "available", "applicant_hit": false, "nominee_hit": false, "lists_checked": ["UN", "OFAC", "RBI_caution"] },
    "defaulter_list":     { "status": "available", "hit": false },
    "digital_footprint":  { "status": "available", "score": 0.62, "note": "corroborating context only, never standalone" },
    "abha_health_records":{ "status": "consent_declined" },
    "pharmacy":           { "status": "consent_declined" },
    "rppg_scan":          { "status": "available", "vitals": { "heart_rate": 72, "respiratory_rate": 16, "hrv_ms": 42, "stress_index": 0.38, "bp": "118/76", "hemoglobin": 14.1, "spo2": 98 }, "quality_flag": "acceptable" },
    "facial_bmi_smoking": { "status": "available", "bmi_estimate": 30, "smoking_estimate": "likely", "gender_estimate": "male" },
    "pre_policy_medical": { "status": "available", "name": "Rohit Sharma", "dob": "1998-05-21",
                            "exam": { "height_cm": 180, "weight_kg": 80, "bmi": 25.2, "pulse": 75, "bp": "120/80" },
                            "lab": [ { "test": "hemoglobin", "unit": "g/dL", "result": 11.8, "ref": "13.5-17.5", "severity": "low" }, { "test": "MCV", "unit": "fL", "result": 76, "ref": "80-96", "severity": "low" }, { "test": "ESR", "unit": "mm/hr", "result": 28, "ref": "<20", "severity": "high" }, { "test": "TSH", "unit": "uIU/mL", "result": 5.1, "ref": "0.4-4.5", "severity": "high" }, { "test": "total_cholesterol", "unit": "mg/dL", "result": 228, "ref": "<200", "severity": "high" }, { "test": "fasting_glucose", "unit": "mg/dL", "result": 102, "ref": "70-99", "severity": "high" } ],
                            "radiology": [ { "test": "pelvis_xray", "impression": "healed pelvic fractures, post polytrauma", "severity": "moderate" }, { "test": "chest_xray", "impression": "mild cardiomegaly, post-traumatic fibrosis", "severity": "mild" } ],
                            "medical_questionnaire": { "regular_consultation_or_hospitalization": "no", "substances": "yes_2_cigarettes_a_day", "major_disorders": "no" } }
  },

  "documents": [
    { "type": "bank_statement", "ref": "doc://bsa/110L156V03", "source": "account_aggregator", "format": "pdf" },
    { "type": "salary_slip",    "ref": "doc://sal/110L156V03", "source": "customer_upload", "format": "pdf" },
    { "type": "itr",            "ref": "doc://itr/110L156V03", "source": "itr_vendor", "format": "xml" },
    { "type": "medical_report", "ref": "doc://med/110L156V03", "source": "tpa_medical", "format": "pdf" }
  ],

  "follow_up_observations": {}
}
```

---

## 13. Deferred work — known gaps, phase-wise

> Living ledger of items we consciously deferred, with enough detail to pick each up cold.
> **This is the single source of truth for deferred work** (folded in here per decision — no
> separate document). Fixed-now items are **not** here (see git history / CLAUDE.md); this is only
> what we chose to leave for later, and why. Each entry: **what · why deferred · impact if left ·
> how to do it · trigger**. `D-n` ids are stable — reference them from code/PRs.

### 13.1 From Phase 1 (deterministic rule engine) — audit 2026-08-10

**D-1 — Name matcher is too crude (false mismatches on real Indian names).**
- *What.* `rules._name_tokens` / `names_match` decide the cross-source `identity_mismatch` flag
  (severity **high**). Defects: (1) `kumar`/`kumari` are in `_NAME_NOISE` and dropped as honorifics,
  but **Kumar is a ubiquitous surname** — `names_match("A. B. Kumar", "A B Kumar")` → False (tokens
  empty after dropping "kumar" + ≤1-char initials → forced mismatch); (2) empty-token-set → False
  (treated as *mismatch*, not *unknown*) invents a high-severity flag on absence of data; (3) subset
  logic collides the other way — bare `"Sharma"` matches any `"X Sharma"`.
- *Why deferred.* User decision (2026-08-10) "we will do name match later"; also formally deferred in
  Phase 0 as `# TODO(consistency-spec)` pending the agreed matcher (fuzzy name / exact DOB / normalized address).
- *Impact if left.* Spurious `identity_mismatch` → grey-zone → REFER for legitimate applicants
  (initials + noise-listed surname). Over-refers; never mis-issues/mis-declines (fail-safe direction).
- *How.* Drop `kumar`/`kumari` from `_NAME_NOISE`; replace subset match with a real similarity
  (`difflib.SequenceMatcher` ratio to avoid a dep, or `rapidfuzz`); empty-token-set → **abstain**
  (return match/"unknown", never a high-severity flag on missing data); keep threshold
  `# TODO(underwriting-manual)`-tagged; add fixtures (`"R Sharma"` vs `"Rohit Sharma"` match, `"A B Kumar"`
  vs `"A. B. Kumar"` match, `"Ram"` vs `"Sham"` no-match, mononym identical match).
- *Trigger.* **Before Phase 6 / any real applicant traffic** (production-correctness bug), or when the
  consistency-spec lands — whichever first.

**D-2 — R-015 cluster threshold (≥2 flags) is not what drives routing.**
- *What.* `config.CLUSTER_SOFT_FLAG_MIN = 2` + `CLUSTER_FLAG_TYPES` model R-015, but `run_bre` routes on
  `cluster_fires **or** any_soft`, so a **single** soft flag already routes to grey-zone; the cluster
  threshold gates nothing.
- *Why deferred / not a bug.* Intentional conservatism — R-014 auto-issues only with **zero** soft flags;
  requiring ≥2 would let a lone high-severity flag (e.g. one confirmed non-disclosure) slip to auto-ISSUE.
  Nothing is mis-issued/mis-declined.
- *Impact if left.* Config constant reads as dead/misleading (tuning it changes nothing). Clarity debt only.
- *How.* Either (a) comment the `run_bre` routing branch stating any soft flag → grey-zone by design and
  `CLUSTER_SOFT_FLAG_MIN` is reserved for future severity-weighted routing; or (b) make routing
  severity-aware — a lone **low**-severity flag could still auto-issue while ≥2 low / any high routes to
  grey-zone (needs a UW decision on which low-severity flags are auto-clearable).
- *Trigger.* Phase 6 tuning (raise auto-issue rate) with labeled outcomes + UW sign-off on (b). Not before.

**D-3 — Single moderate ML score → grey-zone.**
- *What.* A lone `fraud_score` in the moderate band (0.30–0.69) raises one `moderate_ml_score` flag →
  grey-zone → REFER (Phase 1) / judge (Phase 3).
- *Why deferred / not a bug.* §4A explicitly says the moderate band **is** grey-zone. Correct per spec.
- *Impact if left.* None functional; listed so it isn't re-raised as a surprise.
- *How.* Folds into D-2 option (b) if routing ever becomes score/severity-weighted.
- *Trigger.* Same as D-2.

**D-4 — Non-medical SI limits are config-only (no rule reads them).**
- *What.* `NON_MEDICAL_SI_LIMIT_YOUNG` (₹50L, ≤45) / `NON_MEDICAL_SI_LIMIT_SENIOR` (₹25L, 46–55) from §4A
  are declared but **no rule enforces them** — there is no R-0xx "requested SI above the non-medical limit
  → require full medicals / step-up." (Sibling `NO_INCOME_PROOF_SI_CEILING` **was** wired into R-008 in the
  audit — not dead; only these two remain unwired.)
- *Why deferred.* A genuinely *new* rule (missing R-row), not a bug in an existing one — needs a UW decision
  on the consequence (step-up vs cap SI vs refer). Out of Phase 1's "implement R-001–R-017" scope.
- *Impact if left.* A young applicant can request up to the STP ceiling (₹1cr, R-006) with no medicals on
  SI-size grounds. Age 46–55 is partly covered by R-005b (senior-medicals, added in the audit); the
  "SI > ₹50L for under-45 → medicals" case is not.
- *How.* Add `R-018 non_medical_si_gate`: pick the age-appropriate `NON_MEDICAL_SI_LIMIT_*`; if
  `requested_SI > limit` and no full pre-policy medical in the bundle → `beyond_matrix=True` (step-up),
  reusing R-005b's wiring. Add fires/doesn't-fire tests + a fixture (young, ₹80L SI, no medicals → STEP_UP).
- *Trigger.* Before go-live UW sign-off (a real UW control), or when the non-medical-vs-full-medical SI
  policy is finalized. Coordinate with the actuarial/UW manual.

### 13.2 From Phase 2 (scoring & Safety Score) — review 2026-08-10

> Phase 2's done-when is **met** (Rohit → 65.6 / High from the §4A weight table; every score attributed).
> None of the below blocks Phases 3–5. D-5/D-6 gate on the Phase-6 labeled eval set — **do NOT touch the
> penalty knobs before then**; D-7/D-8 are optional cleanups with no current output change.

**D-5 — The Rohit ~65 Safety Score is a calibration ANCHOR, not validated ground truth.**
- *What.* Every penalty magnitude in `scoring.py` (all `# TODO(underwriting-manual)`-tagged) was tuned so
  the canonical Rohit case reproduces Appendix A's illustrative Safety Score (**65.6 / High**);
  `test_rohit_safety_score_is_65_high` asserts `60 ≤ value ≤ 70` + band High. Two coupled facts: (1) Appendix
  A itself calls its sub-scores "illustrative placeholders — calibrate against the underwriting manual +
  labeled outcomes," so we tuned to reproduce an *illustration*; (2) to land ~65 from scored data (not a
  hardcode) the Rohit fixture was enriched with `litigation_fir` + `account_aggregator.lifestyle_spends`
  copied verbatim from Appendix B — real canonical facts, but the *choice of which to add* was target-driven.
- *Why deferred.* No labeled ground truth exists yet; re-fitting against real outcomes is exactly Phase 6.
  Doing it now would re-fit to another guess (YAGNI).
- *Impact if left.* The score *orders* cases correctly (clean > non-disclosure, enforced by
  `test_safer_case_scores_higher`) and every number is explainable — safe to **display** and **rank** on.
  **Not** yet safe to gate an automated action on an absolute band cutoff. Decision routing does not depend
  on the band today (hard gates + grey-zone edge drive decisions), so this is latent, not active, risk.
- *How.* Phase 6: (a) collect underwriter-labeled accept/refer/decline outcomes; (b) re-fit §4A weights +
  per-source penalties against those; (c) **replace** `test_rohit_safety_score_is_65_high` with a
  separation/ranking metric (AUC / rank-order over the cohorts), keeping Rohit as a regression snapshot only;
  (d) re-derive fixture facts from the label set rather than from Appendix B.
- *Trigger.* Phase 6, when the labeled eval set exists.

**D-6 — `risk_scores.shap` is a heuristic stand-in, not real SHAP.**
- *What.* `risk_scores` returns a `shap`-shaped attribution from documented feature contributions, honest
  about being a heuristic (`score_source` ∈ `heuristic`|`upstream_model`, `attribution_note`; when
  heuristic-driven the weights are rescaled to sum to the reported score). Real SHAP over a trained model is
  not built (no model yet).
- *Why deferred.* §5.1 says heuristic scorer now (shadow), swap to trained XGBoost/isolation-forest/graph +
  real SHAP when the labeled set exists. Same gate as D-5.
- *Impact if left.* None functional — attribution is real and reconciles. Listed so nobody mistakes the
  heuristic `shap` for model SHAP.
- *How.* When the shadow models train (Phase 6): compute real SHAP; keep the reconciliation contract
  (`shap` sums to the score when the model drives it) so reports stay auditable; keep the heuristic as the
  documented fallback when no model is available.
- *Trigger.* Phase 6, with D-5.

**D-7 — `safety_score` recomputes `risk_scores()` ~3× per call (perf/clarity, not correctness).**
- *What.* `_s_financial`, `_s_velocity`, `_s_fraud_check` each call `risk_scores(inp, bre)` fresh, so one
  `safety_score()` runs the risk-score pipeline ~4× total. Marked with a `ponytail:` comment in `scoring.py`.
- *Why deferred.* Deterministic → safe; numbers identical each call. Pure waste, no wrong output.
- *Impact if left.* Slightly slower scoring; the safety→risk dependency is implicit rather than injected.
- *How.* Compute `risk_scores` once at the top of `safety_score` and pass it into the sub-scorers (add a
  `risk` param). ~5-line change; the `ponytail:` comment marks the spot.
- *Trigger.* If scoring shows up hot in a profile, or during a Phase-6 refactor.

**D-8 — Sub-scorer penalty branches with no fixture exercising them.**
- *What.* `_s_geography` (fraud_hotspot / high morbidity), `_s_lifestyle` declared-tobacco branch,
  `_s_occupation` hazardous-class branch, `_s_velocity` velocity_anomaly-flag branch have real logic but no
  fixture drives them non-trivially (the current fixtures don't carry those adverse facts).
- *Why deferred.* Branches are simple and read facts the same way the exercised sub-scorers do — low
  regression risk. Fixture-coverage debt, not a code defect.
- *Impact if left.* A regression in an unexercised penalty (e.g. zeroing the hazardous-class deduction)
  wouldn't be caught by the current suite.
- *How.* Add one targeted unit case per branch (minimal `ProposalInput` with just the adverse fact, assert
  the sub-score drops), or a small fixture per branch.
- *Trigger.* When widening fixture coverage generally, or alongside D-5's fixture re-derivation.

### 13.3 From Phase 3 (LLM judge + grey-zone pipeline) — audit 2026-08-10

> Phase 3's done-when is **met** and the deep audit found + fixed one critical safety hole (see the
> "fixed now" note below). The items below were consciously left for later. **Fixed in the audit (NOT
> deferred — here only so they aren't re-raised):** (a) **coverage gate** `decision.coverage_ok` — the
> judge must return exactly one grounded ruling per raised flag, ids matching 1:1, else REFER
> `ruling_coverage_failed`; without it a malformed judge response (dropped/extra/duplicate/empty ruling)
> could silently auto-ISSUE an unaddressed grey-zone flag; (b) free-text extractor path now has real test
> coverage incl. consent-gating; (c) history retention disabled in prod via BOTH `disable_history=True`
> and `max_history_size=0`; (d) the extractor re-run only re-scores when the flag set actually changed
> (was the Phase-3 sibling of D-7); (e) **fail-safe** — an LM/gateway exception on a grey-zone case now
> yields a deterministic REFER `judge_unavailable` instead of a crash (D-11 decision layer). None of the
> below blocks Phase 4-5.

**D-9 — Per-call token counts in `run_metadata` read 0 (cost is reliable).**
- *What.* `judge.usage_since` rolls up `cost` + `input_tokens`/`output_tokens` from `dspy.settings.lm.history`
  for the audit stamp (§6/§11). **Cost is accurate** (verified live: Vikram ≈ $0.018, Anjali ≈ $0.006);
  **token counts read 0** even in eval mode, because some DSPy adapter history entries (notably the
  `ChainOfThought` calls) carry `cost` but omit a matching `usage` dict, so the `prompt_tokens`/
  `completion_tokens` lookup finds nothing. History is off in prod, so both are 0 there by design.
- *Why deferred.* §11 requires **cost** on every output — that works. Exact per-call tokens are a
  nice-to-have for the audit stamp, not a gate on any decision; chasing DSPy internals further now is
  low-value (YAGNI). Tagged `# ponytail:` in `judge.usage_since`.
- *Impact if left.* `run_metadata.input_tokens/output_tokens` show 0; cost + model + prompt_version are
  correct. Audit trail is complete on the field that matters (cost); token telemetry is missing only.
- *How.* Register a LiteLLM success callback (`litellm.success_callback`) that captures `response.usage`
  per call into a run-scoped accumulator, or read tokens from the raw `response` object DSPy stores rather
  than the adapter's `usage` field. Then assert non-zero tokens in the eval-mode live smoke test.
- *Trigger.* When per-case token/cost dashboards are built (cost-at-scale monitoring), or Phase 6 eval
  harness reporting. Not before.

**D-10 — `CONFIDENCE_MIN = 0.60` is an uncalibrated placeholder.**
- *What.* `decision.CONFIDENCE_MIN` gates the calibrated confidence check (low confidence → REFER
  `low_confidence`). The value 0.60 is a `# TODO(underwriting-manual)`-tagged guess; `decision.confidence`
  is a real deterministic function of ruling decisiveness + grounding coverage (NOT model self-report, per
  §11), but the **cutoff** has not been fitted to any labeled outcomes.
- *Why deferred.* Same gate as D-5/D-10: no labeled eval set exists yet; calibrating the cutoff now would
  fit to a guess. This is explicitly Phase 5's "confidence-gate calibration" item.
- *Impact if left.* The gate still functions (all-escalate / ungrounded → low → REFER, the fail-safe
  direction); only the exact threshold is unvalidated. Over-refers or under-refers at the margin; never
  mis-issues past an ungrounded ruling (grounding + coverage gates run first, independently).
- *How.* Phase 5: sweep `CONFIDENCE_MIN` against the labeled grey-zone set; pick the cutoff that best
  separates human-confirmed ISSUE from REFER; keep `confidence()` deterministic; add a regression test
  asserting the chosen cutoff's behaviour on a few labeled cases.
- *Trigger.* Phase 5 (confidence-gate calibration), when the labeled eval set exists. Same gate as D-5.

**D-11 — API status code + retry policy for an unreachable LLM (decision-layer fail-safe DONE).**
- *What.* **Fixed in the audit:** `pipeline.run` now wraps the whole Layer-3 block (extractor re-run + both
  `run_judge` calls) in a try/except; any LM/gateway exception on a grey-zone case → deterministic REFER
  `judge_unavailable` (fail-safe to a human), never an unhandled crash. Covered by
  `test_judge_unavailable_fails_safe_to_refer`. **Still deferred:** what the API (`api.run_and_report` /
  `POST /underwrite`) should *return* for this case — 200 with a REFER report, 503 retry-later, or enqueue
  for async re-try — and whether to auto-retry the gateway before giving up.
- *Why deferred.* The remaining piece is a product/ops decision on the HTTP contract + retry semantics; it
  touches `api.py` response shape (Phase 4) and the durable async/Temporal story (Later §). The *decision*
  is already safe (REFER); only the transport-level policy is open.
- *Impact if left.* A grey-zone case during a gateway outage returns a valid REFER report today. Whether the
  caller sees 200/503 and whether a transient blip is retried before referring is unspecified — an ops/UX
  nuance, not a correctness gap (clean / hard-gate / postpone cases never touch the LLM regardless).
  **Phase-4-confirmed:** `api._status` currently returns `"complete"` for a `judge_unavailable` REFER with
  no `degraded` marker (verified 2026-08-10) — i.e. the transport-level policy is still exactly as described.
- *How.* In `api.py`, map `escalation_reason == "judge_unavailable"` to the chosen status (recommend 200 +
  REFER so the journey completes with a human hand-off, with a `degraded: true` flag in `run_metadata`);
  optionally add a bounded gateway retry (e.g. 1 retry) in `judge.run_judge` before the pipeline's
  fail-safe trips. Decide alongside the async/Temporal design.
- *Trigger.* Phase 4 API-contract finalization / before go-live, or when the durable async layer lands.

### 13.4 From Phase 4 (report assembly & API) — audit 2026-08-10

> Phase 4's done-when is **met** (one call on **all 6** fixtures — list auto-discovered from `fixtures/`,
> not hardcoded — returns a full report validating against `ReportOutput`; `test_pipeline.py` = 17 tests
> incl. the real FastAPI route via TestClient). **Fixed in the review (NOT deferred — here so they aren't
> re-raised):** (a) false "`risk_level: Unavailable`" claim in `report.py`/CLAUDE.md corrected — `_level`
> only ever emits Low/Moderate/High; (b) idempotency test rewritten to assert **decision** stability
> (`test_idempotency_same_input_same_decision`) + a separate assembly-purity test, replacing a whole-report
> byte-identity assertion that would falsely fail against a real (token-varying) LLM; (c) HTTP route now
> covered by TestClient tests (envelope + 422 at the trust boundary); (d) fixture list made
> data-driven so `priya_postpone` (the POSTPONE Core-6 outcome) is exercised end-to-end — it previously had
> **no** report test — plus an unknown-flag guard so a future grey-zone fixture can't silently pass. None
> of the below blocks Phase 5.

**D-12 — Absent source is scored as clean/safe; report `findings` can assert unobserved facts.**
- *What.* Surfaced by Phase-4 report assembly but rooted in **`scoring.py` (Phase 2)**: `safety_score`
  defaults an *absent* source to sub_score ~100, so `report._level` renders it **"Low"** and the section's
  `findings` text asserts a clean state that was never observed. Verified on a minimal bundle (only
  name/age/product/SI): overall Safety Score **96.4 / Low Risk**, `medical_evaluation` → "labs in range, no
  undisclosed conditions" **with zero labs present**, `financial_evaluation` → "income corroborated" with no
  income data. Missing reads as safe, the opposite of §11's "unavailable, reasoned around."
- *Why deferred.* It's a scoring-layer semantic change (distinguish *absent* from *assessed-clean*) that
  needs a policy call — does a missing medical source push medical toward High/step-up, or stay neutral with
  an explicit "not assessed" marker? That is a UW/product decision, and (like D-5) is best made against the
  labeled set rather than guessed now. The report layer already faithfully reflects the scorer; it must not
  invent a distinction the scorer doesn't make.
- *Impact if left.* A sparse bundle can present a falsely reassuring Safety Score + section levels, and the
  `findings` prose can mislead a human reader into thinking a source was checked when it was absent.
  **Not** a decision-safety gap today: routing is driven by hard gates + the grey-zone edge, not the band
  (see D-5); a genuinely missing hard-gate/medical fact still can't auto-ISSUE past the zero-soft-flag
  requirement. Display/telemetry risk, not mis-issue risk — but material for anyone reading the report.
- *How.* In `scoring.py`, thread each source's `SourceStatus` into its sub-scorer: when a group's inputs are
  `unavailable`/`consent_declined`/`not_requested`, return a distinct sub_score sentinel (or a
  `assessed: false` flag on the breakdown row) instead of 100; add a `"Unavailable"` / `"Not assessed"`
  level in `report._level` for that sentinel and suppress the clean-state `findings` prose. Add a fixture:
  medical source absent → medical section reads "Not assessed", not "Low / labs in range". Recompute the
  §4A composite to decide whether unavailable groups are dropped-and-reweighted or floored.
- *Trigger.* Before go-live report review / before the report is shown to a human underwriter; coordinate
  with D-5 (weight/penalty re-fit) since both touch `scoring.py`.

**D-13 — `run_metadata` omits Appendix A's `per_stage` token/cost breakdown; `latency_seconds` is null.**
- *What.* `report.build_report` stamps `rules_version`/`prompt_version`/`model`/`total_cost_usd`/`tags`/
  `judge_cycles`/`input_tokens`/`output_tokens`, but **not** Appendix A's `per_stage` map (per-cycle
  input/output tokens + cost) and leaves `latency_seconds` `null` (verified 2026-08-10). Aggregate token
  counts also read 0 for the same DSPy-history reason as **D-9** (cost is reliable, tokens best-effort).
- *Why deferred.* `latency_seconds` is intentionally omitted from the report body: wall-clock would break
  the idempotency contract (same input → same report bytes at the assembly layer). `per_stage` is a
  reporting nicety that depends on the same per-call token capture D-9 defers; building it now would inherit
  D-9's 0-token limitation. §11 requires **cost** on the output, which is present.
- *Impact if left.* The report is Appendix-A-shaped at the top level (all 13 keys present) but its
  `run_metadata` is a strict subset of Appendix A's illustrative block — fine for audit (cost + versions
  present), thinner for per-stage cost dashboards.
- *How.* Fold in with **D-9**: once a LiteLLM success callback captures per-call `usage`, accumulate it into
  a `per_stage` dict keyed `judge_cycle_1/2` and attach to `run_metadata`. Keep `latency_seconds` **out of
  the report body** (idempotency); if latency telemetry is needed, emit it on a side channel (logs/metrics)
  or a clearly non-idempotent envelope field, never inside the report the idempotency test compares.
- *Trigger.* With D-9 (per-case token/cost dashboards) / Phase 6 eval-harness reporting.

**D-14 — `cited_evidence_chain[].claim` carries the flag_id, not a human claim; `cycle` is null.**
- *What.* `report._cited_chain` sets `claim = ruling.flag_id` (e.g. `"flg_001"`) and never populates
  `cycle`. Appendix A shows `claim` as a human phrase (`"medical misrepresentation"`, `"income
  authenticity"`) with the gather `cycle` filled in.
- *Why deferred.* Cosmetic/traceability polish, not a correctness issue — the chain still links each cited
  source to its ruling and flag, and grounding is enforced on the real paths regardless of the `claim`
  label. A human phrase would need a flag_type→phrase map (or the judge to emit a short claim string), and
  per-ruling `cycle` isn't threaded through the pipeline today (rulings don't carry which cycle produced
  them).
- *Impact if left.* The evidence chain is machine-accurate but less readable than Appendix A's illustration;
  a report reader sees `flg_001` instead of "medical misrepresentation" and can't tell cycle-1 from cycle-2
  citations from this block alone (the `audit_log` still records the cycles).
- *How.* Add a `flag_type → human_claim` lookup (or carry a `claim`/`cycle` on `FlagRuling` set when the
  pipeline records each judge cycle) and populate both fields in `_cited_chain`. Small, localized to
  `report.py` + the ruling shape.
- *Trigger.* When the report is prepared for human/underwriter presentation (I-Adore parity pass), or
  alongside D-11's report-presentation work.

### 13.5 From Phase 5 (eval harness & real-data readiness) — review 2026-08-10

> Phase 5's done-when is **met and verified end-to-end**: `eval.replay()` runs the whole labeled fixture
> set through the real pipeline and scores each case on the accuracy triad; the offline suite (`test_eval.py`
> = 6 tests) proves a seeded bad-prompt, a seeded bad-rule, AND a right-verdict-wrong-reason change are all
> caught before prod; the real **live cached replay** (`UW_EVAL_MODE=1 python -m underwriting.eval`) was run
> twice against the gpt-4o gateway → **6/6 CLEAN, triad all zero, reproducible across runs** (caching on).
> Vendor adapters (`sources/`, `test_sources.py` = 6 tests) map a real raw response → the internal contract,
> fail safe on unknown status, drop verdict fields (§1.8), and the mocks pass unchanged (pipeline takes no
> dependency on the adapter layer). **Fixed in the review (NOT deferred — recorded so they aren't re-raised):**
> (a) the harness was upgraded from **verdict-only** to also check `expected_bre_outcome`, `expected_flag_types`,
> and `expected_rulings`/`must_cite` — a rule/prompt change landing the right verdict for the wrong reason now
> fails (`test_right_verdict_wrong_reason_is_caught`); (b) adapters probed against empty/None/garbage raw input
> — robust, no crash; (c) unknown vendor PAN status verified to fail safe to `invalid`, never `valid`. None of
> the below blocks Phase 6.

**D-15 — Harness cannot verify the CYCLE-1 (triage) ruling on a resolved two-cycle case.**
- *What.* `expected_rulings` in a fixture labels the **cycle-1** triage ruling — the one that decides the
  next step (anjali: `thin_file → needs_income_corroboration → gather`). But `PipelineResult.rulings` retains
  only the **last** judge cycle's rulings; on a two-cycle case that resolved, the terminal ruling is
  `benign_explained` (post-gather), not the labeled cycle-1 value. So `eval.evaluate_case` **skips** the
  `expected_rulings` check when `judge_cycles >= 2` (guarded explicitly in `eval.py`), validating such cases
  by their terminal verdict + the gather having happened instead.
- *Why deferred.* Closing it is a **pipeline shape change**: `PipelineResult` must retain per-cycle rulings
  (e.g. `rulings_by_cycle: list[list[FlagRuling]]`) so the harness can assert the cycle-1 triage ruling *and*
  the cycle-2 resolution independently. That touches `pipeline.py`, `report.py` (D-14 wants the same per-cycle
  data for `cited_evidence_chain[].cycle`), and the `PipelineResult` contract — worth doing **once**, together
  with D-14, not piecemeal now. The single resolved two-cycle fixture (anjali) is already covered by
  `test_grounding.py::test_anjali_step_up_then_issue` (asserts the full cycle-1→gather→cycle-2 path), so the
  behaviour is tested — just not re-asserted inside the eval scoreboard.
- *Impact if left.* A regression that flipped anjali's **cycle-1** ruling (e.g. straight to `benign` with no
  gather, or to `unresolvable_escalate`) would change `judge_cycles` and/or the terminal verdict and still be
  caught by the harness (verdict + `judge_cycles`), OR be caught by the grounding test — but a regression that
  produced the *same* terminal verdict via a *different* cycle-1 ruling on a two-cycle case would slip past the
  eval scoreboard specifically. Narrow blind spot (one fixture, one cycle), not a decision-safety gap.
- *How.* Thread per-cycle rulings onto `PipelineResult` (fold with D-14); in `eval.evaluate_case`, drop the
  `resolved_two_cycle` guard and match `expected_rulings` against the **cycle-1** ruling set; add a `cycle` key
  to each `expected_rulings` row so a fixture can label both cycles when it wants to.
- *Trigger.* With D-14 (`cited_evidence_chain` cycle/claim polish), or when a two-cycle grey-zone case is first
  mishandled in a way the terminal verdict alone doesn't catch.

**D-16 — Eval "pass/fail" is binary; no accuracy-triad THRESHOLDS or trend tracking yet.**
- *What.* `EvalReport.clean` is all-or-nothing: every label must match and zero hallucinations leak. There is
  no notion of an *acceptable* false-benign / over-escalation **rate** (e.g. "over-escalation ≤ 8% is fine, >
  that fails"), and no persistence of results run-over-run to spot a slow drift. Fine for a 6-case seed where
  the target is 100%; insufficient once the labeled set grows to hundreds and 100% is neither expected nor
  desirable (some genuine grey-zone cases *should* escalate).
- *Why deferred.* Thresholds are a **calibration decision** that needs the grown labeled set to be meaningful —
  setting a "≤ X% false-benign" gate against 6 hand-authored fixtures would encode an arbitrary number, exactly
  the kind of premature threshold §4A/§13 warns against. Belongs with the Phase-6 label-growth + gate-calibration
  work (couples to D-5 confidence/weight calibration).
- *Impact if left.* The harness is a strict regression gate (good — it catches any change to the seed set) but
  not yet an accuracy *dashboard*; it answers "did anything change?" not "are we within tolerance?" and won't
  surface a gradual quality slide across a large set.
- *How.* Add configurable triad-rate thresholds to `EvalReport` (`max_false_benign_rate`, etc., all
  `# TODO(underwriting-manual)`-tagged), compute rates over the set, and fail on breach rather than on any single
  mismatch once the set is large. Persist each run's triad counts (append to a small JSONL under the eval dir,
  keyed by rules/prompt/model version) so drift is visible run-over-run. Reuse the existing version stamps.
- *Trigger.* When the labeled set exceeds ~30 cases (100% stops being the right target) or when calibrating the
  §4A/confidence knobs against real outcomes (Phase 6, with D-5).

**D-17 — Only two vendor adapters shipped (PAN, AA); the rest of the source inventory is un-adapted.**
- *What.* `sources/` ships `identity.py` (PAN) and `income.py` (AA/BSA) as the representative proof that the
  adapter seam holds. The other sources in §3 (EPFO, GST/ITR, CKYC, liveness/face-match, MCA, ABHA, credit
  bureau, IIB, velocity, geography, …) have **no** bespoke adapter — a raw bundle for them passes through
  `adapt` unchanged (identity), which is correct only if the vendor's raw shape already equals the internal
  contract (it generally won't).
- *Why deferred.* Real vendor selection per source is an **open business decision** (§12 #6, files/CLAUDE.md) —
  writing an adapter before the vendor is chosen would map to a guessed raw shape and get thrown away. The seam
  + registry are proven; adding a source is one `@adapter("key")` function against that vendor's actual response,
  authored when the vendor is picked. The current fixtures are authored directly in the internal shape, so
  nothing is blocked in the meantime.
- *Impact if left.* Any source without a registered adapter must receive already-internal data (as the fixtures
  do); a genuinely raw vendor payload for such a source would pass through unmapped and likely fail validation
  or read wrong facts. Not a runtime risk today (no raw ingestion path is wired), but the first real vendor
  integration for an un-adapted source needs its adapter written first.
- *How.* Per chosen vendor: add `sources/<group>.py` with an `@adapter("<internal_key>")` function mapping that
  vendor's raw response → the internal per-source shape (model on `identity.py`/`income.py`); add a canned raw
  payload + a round-trip test to `test_sources.py`; fail-safe any status/label normalization (unknown → the
  safe value, as PAN does). No downstream change — the registry makes it a swap-in.
- *Trigger.* Each time a real vendor is selected for a source (Phase 6, rolling); prioritize the hard-gate
  sources (PAN done; liveness/face-match, mobile-revocation, AML/PEP) since those drive DECLINE/REFER.

**D-18 — ML risk scorer is still the documented heuristic; no trained model in shadow.**
- *What.* `scoring.py` uses the explainable heuristic scorer (real, attributed — §5.1), not a trained
  XGBoost/isolation-forest/graph model. The eval harness scores decisions, but nothing yet runs a trained model
  in shadow against the labeled set to compare.
- *Why deferred.* Requires a **labeled training set** that does not exist yet (the 6 fixtures are a regression
  seed, not training data). Training on 6 cases would overfit to noise. This is the explicit Phase-6 "ML in
  shadow" item; the heuristic is the honest interim per §5.1 and is already marked as such (Phase-2 D-6).
- *Impact if left.* Scores are heuristic (defensible, explainable) rather than learned; acceptable and intended
  for v1. No accuracy claim rests on a trained model yet.
- *How.* Once a labeled outcome set exists (Phase 6 label growth), train the §5.1 models, run them in shadow
  (log model score alongside the heuristic, decide on neither yet), compare against labels via the eval harness,
  then swap `scoring.risk_scores` to the model behind the same interface when it beats the heuristic. Couples to
  D-5 (calibration) and D-6 (real SHAP).
- *Trigger.* When the labeled set is large enough to train/validate without overfitting (Phase 6).

### 13.6 "Later" (post-v1) — durable async & channel, per §10 "Later"

> Explicitly out of v1 scope by the phase plan (§10 "Later (not v1)"). Recorded here in full so the boundary
> is deliberate, not forgotten. These are **not** Phase-6 items — they are the next horizon after the v1
> synchronous engine + eval harness are proven.

**L-1 — Durable async pause/resume across consent/upload waits (Temporal).**
- *What.* A STEP_UP today returns synchronously as `status: "pending"` + `waiting_on = decision.next_step`
  (Phase 4, `api.py`) — the caller must poll/re-submit. A durable workflow that **pauses** the case for hours/days
  waiting on a customer ABHA-consent or document upload and **resumes** exactly where it left off (re-judge with
  the gathered evidence) is not built.
- *Why later.* §2 "Async note" + §10 defer it explicitly: v1 builds the synchronous core and returns `pending`;
  the durable engine is a separate infrastructure workstream (Temporal Python SDK per files/CLAUDE.md tech stack).
  The judgment layer (rules/judge/decision/report) is complete and unaffected — durability wraps *around* it.
- *Impact if left.* Grey-zone cases needing evidence surface as `pending` and rely on an external
  poll/resubmit loop; there is no server-side timer, no automatic resume on upload, no durable state if the
  process restarts mid-wait. Fine for v1 pilot; needed before high-volume production where waits are common.
- *How.* Wrap `pipeline.run` in a Temporal workflow: the gather step becomes a durable activity that emits the
  consent/upload request and **awaits a signal**; on the signal (evidence arrived) the workflow resumes into the
  re-judge + grounding gate + mapper it already has. The `EvidenceGatherer` seam (`pipeline._fixture_gather`) is
  the exact injection point — swap the fixture gatherer for a signal-awaiting activity; nothing else changes.
  Persist the `PipelineResult`-so-far as workflow state. Idempotency (§11) already holds on `proposal_id`.
- *Trigger.* Before production volume where STEP_UP waits are common, or when a customer-upload/consent callback
  channel exists to deliver the resume signal.

**L-2 — WhatsApp Flows channel + human-review dashboard.**
- *What.* The engine is channel-agnostic (one JSON in → one report out); the WhatsApp Cloud API + Flows front
  end (the actual customer onboarding conversation N0–N18) and the human-underwriter review dashboard (where a
  REFER lands) are stubbed behind an interface, not built (files/CLAUDE.md tech stack, §10 "Later").
- *Why later.* Needs a BSP/WABA business decision (open, files/CLAUDE.md) and is a separate product surface from
  the risk engine. The engine's contract (`POST /underwrite` → report) is the boundary; the channel calls it.
- *Impact if left.* No end-customer conversational channel and no UI for the human underwriter to action a REFER
  in v1 — cases are driven via the API directly. Expected for a v1 engine build.
- *How.* Build the WhatsApp Flows journey as a client of `POST /underwrite` behind the channel interface already
  posited; build a thin review UI that reads the report's `decision` + `cited_evidence_chain` + `audit_log`
  (all already produced) for the REFER queue. Neither requires an engine change — they consume its output.
- *Trigger.* When the BSP/WABA is selected (channel) and when a human-review team needs a UI (dashboard) — both
  after the v1 engine is validated.
