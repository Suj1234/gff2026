# PRD — Onboarding Risk Assessment Agent
## Individual Retail Health Insurance · WhatsApp-Native Journey · India

---

## 1. Problem & Objective

**Problem (underwriter view):** Today's onboarding form captures self-declared facts and checks them against thresholds. It cannot catch what only shows up in the *relationship between facts* — a mobile number ported 20 days ago next to an undisclosed diagnosis, an income that doesn't support the requested sum insured, three policies bought across insurers in six weeks. Non-disclosure of pre-existing disease is the leading cause of health claim repudiation in India, and the 60-month moratorium means whatever isn't caught at onboarding may never be contestable again.

**Objective:** Build a WhatsApp-native onboarding journey where every step captures the minimum data needed, every fact is verified against a real data source the moment it's given (not batched for later underwriting), and a case is auto-issued, stepped-up, referred, or declined at the end of one continuous conversation — with a human underwriter and an LLM agent involved only where the deterministic rules and ML scores genuinely can't resolve the case alone.

**Not the objective:** Automating for automation's sake. Every API call, every rule, and every point where the LLM agent is invoked below is justified by what it catches that the previous step could not.

---

## 2. Scope & Assumptions

- **Product:** Individual retail health indemnity (not group/corporate — PED/non-disclosure underwriting applies at the individual level).
- **Channel:** WhatsApp Business Platform (Cloud API), using WhatsApp Flows for structured multi-step data capture and document/selfie upload, template messages for session-initiated and post-24-hour-window messages, and a Business Solution Provider (BSP) for delivery. *Assumption, stated explicitly: this matches the WhatsApp-native product direction already in motion — flag if a web/app channel should be primary instead, as several steps (DigiLocker OAuth redirect, high-resolution selfie capture) behave differently outside WhatsApp.*
- **Personas covered:** **Salaried** and **Self-Employed** (proprietor/professional/director). Group/corporate, NRI, and minor/dependent-only proposals are out of scope for this version.
- **Underwriting thresholds used in this document (income multiples, BMI bands, sum-insured ceilings) are illustrative placeholders**, structured the way a real rule must be structured, but the actual numbers must come from the insurer's underwriting manual before go-live. This is called out again wherever a number appears.

---

## 3. The Agent — What It Is, What It Can Do

This is the one genuinely agentic component in the system. Everything else in this document (N0–N14, N16–N18) is a deterministic workflow with fixed steps — the agent is the single place where the LLM controls what happens next, and it exists **only** because different grey-zone cases genuinely need different follow-up evidence, so a fixed pre-computed enrichment step can't serve every case well without wasting calls, time, and — for health records specifically — collecting more sensitive data than a given case actually needs.

- **Name:** Onboarding Risk Assessment Agent.
- **When it runs:** only when N13's deterministic BRE routes a case to GREY-ZONE. It never sees AUTO-ISSUE, HARD-DECLINE, or HARD-REFER cases — those are resolved without it.
- **Goal:** resolve the specific ambiguity that caused this case to land in grey-zone, using the least additional evidence necessary, then emit one grounded, cited verdict.
- **What it is given at the start:** the full evidence bundle already captured through N13 (every fact, every flag, every ML score with attribution) plus the specific BRE rule(s) that triggered grey-zone routing — so it starts already knowing *why* it's looking at this case, not guessing.
- **What it can do — its toolset (closed set, nothing outside this list):**

| Tool | What it does | Guardrail |
|---|---|---|
| `request_abha_consent()` | Triggers the ABHA/HIE-CM consent flow; pulls linked health records if granted | Real customer consent required — the agent cannot bypass or assume consent |
| `trigger_rppg_scan()` | Offers the rPPG screening step to the customer | Output is a step-up trigger only, never a scoring input |
| `request_additional_document(doc_type)` | Asks the customer for one specific document via WhatsApp | `doc_type` drawn from a fixed enum — no free-form requests |
| `query_graph_detail(entity)` | Pulls more detail on an already-flagged velocity/cover-stacking graph edge | Read-only against existing data — no new external pulls |
| `ask_clarifying_question(template_id)` | Sends one pre-approved WhatsApp template question to the customer | Fixed, pre-approved template set — the agent cannot generate its own question text |
| `emit_verdict(verdict, confidence, cited_evidence, rationale)` | Terminal action — ends the loop with a recommendation | Schema-validated, grounding-checked, confidence-gated (Technical Plan §6) |
| `escalate_to_human(reason)` | Terminal action — explicit abstention | Used when no further tool call would resolve the ambiguity |

- **How it decides — a staged pipeline, not an open loop:** the BRE doesn't just say "grey-zone" — it deterministically identifies the *specific* ambiguous flags (e.g. "income thin-file," "identity mismatch") that need judgment. The agent's **Judge** step rules on each flagged item in one call, choosing from a fixed, bounded set of rulings (`benign_explained`, `needs_income_corroboration`, `needs_medical_check`, `needs_identity_reverification`, `unresolvable_escalate`) — it never freely picks its own next action. A deterministic decision table reads those rulings and decides what happens: if every flag resolves to `benign_explained`, finalize; if a specific, named condition is unresolved, gather exactly the evidence that resolves it and re-run the Judge **exactly once more** — never an open retry loop. Anything still unresolved after that one cycle escalates to a human, full stop.
- **What it can never do:** touch the deterministic hard gates (identity fraud, AML/PEP/sanctions, STP age/SI eligibility), alter pricing or sum insured, emit a final verdict directly (that's always deterministic code reading its rulings), or issue a ruling whose only cited basis is geography or occupation.
- **Where N11 (health-record and rPPG enrichment) went:** it isn't a fixed pre-step anymore. Requesting ABHA consent or an rPPG scan happens only when the deterministic decision table sees a `needs_medical_check` ruling for a specific case — this is both more efficient and a cleaner DPDP purpose-limitation story than pulling sensitive health data for every borderline applicant regardless of what actually flagged them.

---

## 4. Master Journey — One Spine, Two Persona Branches

```
N0 Entry & Consent
 → N1 Mobile-Native Identity Bootstrap
 → N2 Identity Resolution (PAN)              [branches on mobile→PAN hit/miss]
 → N3 Aadhaar / DigiLocker e-KYC
 → N4 CKYC Cross-Check
 → N5 Liveness + Face-Match + Deepfake Gate   [HARD GATE — deterministic]
 → N6 Persona Determination                  [SALARIED branch | SELF-EMPLOYED branch]
 → N7 Product & Cover Selection
 → N8 STP Eligibility Hard-Gate              [HARD GATE — deterministic, unchanged]
 → N9 Enrichment Fan-Out (parallel — cheap, always-fire, no extra consent)
 → N10 Health Declaration
 → N11 [retired as a fixed step — see below]
 → N12 Classical ML Scoring
 → N13 Deterministic BRE Final Pass          [outputs: AUTO-ISSUE / HARD-DECLINE / HARD-REFER / GREY-ZONE]
 → N14 Routing
      ├─ AUTO-ISSUE ───────────────────────────────────→ N17 Decision Communication
      ├─ HARD-DECLINE / HARD-REFER (compliance) ───────→ N16 Human Underwriter Queue (NO LLM, NO AGENT)
      └─ GREY-ZONE ─────────────────────────────────────→ N15 Onboarding Risk Assessment Agent (Section 3) → loop of tool calls → emit_verdict (confidence ≥ threshold → N17) or escalate_to_human (< threshold, or by choice → N16)
 → N16 Human Underwriter Review (as routed)
 → N17 Decision Communication (WhatsApp)
 → N18 Audit Trail Persistence (every case, every path)
```

**N11 is deliberately not a fixed pipeline step.** In the original draft it was a pre-condition rule ("if borderline, pull ABHA/rPPG"). That's now folded into the Agent's toolset (Section 3): `request_abha_consent()` and `trigger_rppg_scan()` are tools the Onboarding Risk Assessment Agent calls itself, only for the specific grey-zone case that needs them, only after it's reasoned about which ambiguity it's trying to resolve. This is more efficient and a cleaner DPDP purpose-limitation story than a blanket "if borderline, pull everything" rule.

Persona divergence happens **only** at N6 (how income/employment is verified) and, downstream, in which N9 enrichment calls fire (MCA/director checks only for self-employed proprietors/directors). Everything else is a shared spine — this is deliberate: identity, KYC, health declaration, BRE, ML, and the agent are persona-agnostic. Building two separate journeys would double the surface area for no underwriting benefit.

---

## 5. Step-by-Step Specification

### N0 — Entry & Consent
- **Trigger:** Customer messages the business WhatsApp number, or clicks a "Click to WhatsApp" ad/link.
- **What happens:** Template message with a brief insurer disclosure + DPDP-compliant consent (purpose: health insurance proposal processing; explicit mention that identity, financial and — later, separately — health data will be verified). Consent is logged with timestamp, message ID, and consent text version.
- **Agent involvement:** None.
- **Fallback:** No consent → conversation ends with an opt-out-safe message; no data is processed or stored beyond the WhatsApp business number log Meta itself retains.

### N1 — Mobile-Native Identity Bootstrap
- **What happens:** The WhatsApp sender number *is* the verified mobile number — no need to ask for it. Fire, in parallel: (a) mobile number vintage/porting check, (b) mobile fraud/revocation-list check, (c) device/behavioural signal capture (silent, via the Flow's client fingerprint).
- **APIs:** Mobile Number Vintage & Porting Check; Mobile Fraud/Revocation List Check; Device Fingerprinting (Bureau.id-class vendor).
- **Deterministic rule:** `IF mobile_on_revocation_list = TRUE → HARD-DECLINE (fraud), route to N16, no further steps.` `IF mobile_vintage < 30 days → flag "new_mobile" (soft flag, carried forward, not a gate).`
- **Agent involvement:** None. This is pure signal collection + one hard rule.

### N2 — Identity Resolution (PAN)
- **What happens:** Attempt a mobile→PAN reverse lookup first, since we already have a verified mobile number and want to minimize typing on a phone keyboard.
  - **Branch A — Mobile→PAN lookup succeeds (single high-confidence match):** Pre-fill PAN; ask the customer to confirm via a WhatsApp Flow ("Is this you? [Masked name], PAN ending XXXX") rather than re-typing it. On confirmation, proceed to N3.
  - **Branch B — Mobile→PAN lookup fails, returns no match, or returns multiple candidates:** This is expected, not an error — reverse lookups are probabilistic and coverage is incomplete, especially for newer mobile numbers or numbers not linked to a lending/KYC bureau. Fall back to a WhatsApp Flow text-entry field asking the customer to type their PAN directly, then verify it.
- **APIs:** Mobile-to-PAN Reverse Lookup (bureau-network based, e.g. via the KYC vendor's data partnerships); PAN Verification — Advanced tier (returns holder name, DOB, gender, masked Aadhaar number, Aadhaar-seeding/linking status, mobile number on record, email, address).
- **Deterministic rule:** `IF PAN status ≠ valid → HARD-DECLINE (invalid identity), route to N16.` `IF PAN-on-record mobile number ≠ current WhatsApp number → soft flag "mobile_pan_mismatch" (not a decline; common when people change numbers — carried forward as a signal, not a gate).`
- **Agent involvement:** None.

### N3 — Aadhaar / DigiLocker e-KYC
- **What happens:** WhatsApp Flow triggers a DigiLocker-based (or Aadhaar-OTP-based) consented e-KYC to get verified full DOB, current address, and a government-source photo.
- **APIs:** DigiLocker e-KYC / Aadhaar e-KYC.
- **Deterministic rule:** `IF e-KYC fails after 2 attempts → route to N16 for manual document upload + human review (not an auto-decline — failure here is often a UX/connectivity issue, not a risk signal).`
- **Agent involvement:** None.

### N4 — CKYC Cross-Check
- **What happens:** Query CERSAI's CKYC registry for an existing KYC record; reconcile name/address/DOB against what's been captured so far.
- **Deterministic rule:** `IF CKYC record exists and conflicts materially (different DOB, different name) → soft flag "ckyc_mismatch", carried forward — do not gate here; this is exactly the kind of multi-signal conflict the grey-zone agent later reasons about.`
- **Agent involvement:** None at this step — but this is the first signal that may later need LLM reasoning if it clusters with others.

### N5 — Liveness + Face-Match + Deepfake Gate (HARD GATE)
- **What happens:** WhatsApp Flow requests a selfie (media upload) or short liveness video. Run face-match against the KYC photo, liveness detection, and deepfake/injection-attack detection.
- **APIs:** Liveness + Face-Match + Deepfake Detection (HyperVerge/Signzy-class vendor).
- **Deterministic rule:** `IF liveness fails OR deepfake detected OR face-match score < threshold → HARD-DECLINE (identity fraud), route to N16.` This is a hard, non-negotiable gate — no LLM, no override without human review.
- **Agent involvement:** None. Identity fraud detection must never depend on a probabilistic LLM judgment.

### N6 — Persona Determination
- **What happens:** WhatsApp Flow question: "Are you Salaried / Self-Employed / Other?" A single tap, not free text.

**Branch A — Salaried:**
- **APIs:** EPFO check (via UAN, if the customer has one, matched on PAN/Aadhaar) → employer name, employment tenure, contribution history (a proxy for salary band).
  - **N6-A-i — EPFO record found:** Employer + tenure + contribution-implied salary band captured. Proceed.
  - **N6-A-ii — EPFO record NOT found** (new job, informal-sector salaried, employer doesn't remit PF, contractual staff): This is common, not exceptional — do not treat as a red flag by default. Fall back to **N6-A-Fallback**: Account Aggregator (AA) consent request → 6–12 months of bank statement data → salary-credit pattern detection (recurring monthly credit from a single source, consistent date, consistent-ish amount) used to estimate income. Tag the case `"salaried_thin_file"` — this tag routes it into a slightly higher-scrutiny BRE band later (not a decline, a scrutiny tier), because self-declared-only income with no EPFO corroboration is exactly the income/SI-mismatch fraud pattern documented in the market research.
- **Deterministic rule:** `IF neither EPFO nor AA-derived income is available (consent declined + no EPFO) → route to N9 income-verification-pending state; product allowed only up to the no-income-proof STP sum-insured ceiling (illustrative placeholder), anything above requires N16 human review.`

**Branch B — Self-Employed:**
- **APIs:** GST record check (if GST-registered) and/or ITR-based income check → declared turnover/income.
  - **N6-B-i — GST/ITR found and internally consistent:** Income accepted, proceed.
  - **N6-B-ii — GST/ITR unavailable or inconsistent** (proprietor below the GST registration threshold, cash-heavy business, ITR not filed or mismatched): Fall back to **N6-B-Fallback**: AA consent request → 12-month bank statement inflow analysis (this window is longer than the salaried fallback because self-employed income is inherently more variable month-to-month) used to estimate income. If the applicant is a company/LLP director or partner (not a sole proprietor), also fire an **MCA/Director check** (director defaults, disqualifications, litigation) — this only fires for self-employed, since it's not applicable to salaried individuals. Tag `"self_employed_thin_file"` if AA is the only source — routes to the same higher-scrutiny BRE band as the salaried thin-file case.
- **Deterministic rule:** same no-income-proof ceiling logic as Branch A.

**Agent involvement at N6:** None. Persona and income-path selection is deterministic branching, not judgment.

### N7 — Product & Cover Selection
- **What happens:** WhatsApp Flow: sum insured (slider/preset bands), plan type, individual vs. family floater, tenure.
- **Agent involvement:** None.

### N8 — STP Eligibility Hard-Gate (unchanged, deterministic)
- **What happens:** The existing deterministic STP gate — age band, sum-insured ceiling for the chosen product, KYC-complete flag, AML/PEP/sanctions screen.
- **Deterministic rule:** `IF AML/PEP/sanctions hit → HARD-DECLINE, route to N16 (compliance), mandatory — never an LLM decision.` `IF outside age band or SI ceiling for STP eligibility → route to N16 for manual underwriting (this case was never going to be auto-issued regardless of the AI layer).`
- **Agent involvement:** None. This gate is explicitly out of scope for any AI component, matching the source document's own separation.

### N9 — Enrichment Fan-Out (parallel calls, no user-facing wait beyond a "verifying your details…" message)
- **What happens:** Everything gatherable without further consent fires in parallel: geography/pincode risk index, mobile/email vintage-and-fraud score, application-velocity/cover-stacking graph check (has this device/bank-account/nominee/mobile appeared on another recent proposal?), occupation hazard class (from N6 declared occupation), and — self-employed only — the MCA/director/litigation check if not already fired in N6-B.
- **Agent involvement:** None. This is data collection; scoring happens at N12–N13.

### N10 — Health Declaration
- **What happens:** WhatsApp Flow, multi-screen: height/weight (auto-computes BMI), existing diagnosed conditions (structured checklist, not free text — free text is harder to ground later), tobacco use, family medical history, existing health cover held (self-declared, to be cross-checked against whatever cover-stacking signal N9 produced).
- **Agent involvement:** None yet — this is data capture.

### N11 — retired as a fixed step
ABHA consent and rPPG are no longer pulled on a blanket "if borderline" pre-rule. They are now `request_abha_consent()` and `trigger_rppg_scan()` — tools available **only** to the Onboarding Risk Assessment Agent at N15, invoked only for the specific grey-zone case where they'd actually resolve the ambiguity that put the case there. See Section 3.

### N12 — Classical ML Scoring
- **What happens:** All signals gathered so far (N1–N10) feed a trained model layer:
  - **Morbidity/fraud risk score** (gradient-boosted, e.g. XGBoost) with SHAP-based feature attribution stored alongside the score.
  - **Anomaly score** (isolation forest) on application-velocity/behavioural signals.
  - **Cover-stacking graph score** — has this identity, device, bank account, or nominee co-occurred with other recent proposals across products in a pattern consistent with stacking?
- **Agent involvement:** None. This is a trained model, evaluated on precision/recall/AUC — not the LLM.

### N13 — Deterministic BRE Final Pass
- **What happens:** The rule engine takes every captured fact, every enrichment flag, and every ML score as input and evaluates a fixed rule set (full rule table in the Technical Implementation Plan). Rules include: income-to-sum-insured multiple caps, BMI×age×occupation loading matrix, waiting-period trigger logic, specified co-morbidity combinations, "cover bought within X days of a detectable health event" hard-refer, and thresholds on the ML scores from N12.
- **Output:** Exactly one of four states — **AUTO-ISSUE**, **HARD-DECLINE**, **HARD-REFER** (compliance-triggered, e.g. AML — already caught at N8, but re-asserted here for defense-in-depth), or **GREY-ZONE**.
- **Agent involvement:** None. The rules decide; they do not ask the LLM for help.

### N14 — Routing
- **AUTO-ISSUE** → N17 directly. This should be the outcome for the large majority of clean applicants — the whole point of the deterministic layers above is to resolve as much as legitimately can be resolved without judgment.
- **HARD-DECLINE / HARD-REFER (compliance)** → N16 directly, **never through the agent**. A compliance hit or a hard-declined identity/fraud gate is not a judgment call.
- **GREY-ZONE** → N15, the Onboarding Risk Assessment Agent. This is the *only* path that reaches the agent, and it is reserved for cases where signals are present, partially conflicting, or partially missing in a way no single rule anticipated — exactly the *undisclosed illness + IIB-style cover-stacking + prior-repudiation* pattern the source document's worked examples describe.

### N15 — Onboarding Risk Assessment Agent (grey-zone only)
This is the agent defined in full in Section 3 — a staged pipeline, not a single classification call and not an open tool-choosing loop. At the journey level:
1. The BRE hands the agent the evidence bundle plus a deterministically-identified list of *specific* ambiguous flags — never a vague "figure this out."
2. The Judge (one LLM call) rules on every flagged item, choosing from a fixed set of bounded outcomes with cited evidence for each.
3. Deterministic code reads those rulings: if all resolve cleanly, it finalizes; if a specific unresolved condition warrants it, it gathers exactly the evidence needed and re-runs the Judge **exactly once more** (this second cycle may itself pause the WhatsApp conversation — e.g. waiting on a document upload — which is why this runs inside the durable Temporal workflow, not a single request/response); anything still unresolved, or ruled `unresolvable_escalate` at any point, routes straight to N16.
4. The agent never emits the final verdict itself — a deterministic gate (grounding-checked, every citation verified against real evidence) is what actually produces AUTO-ISSUE-adjacent outcomes like STEP-UP, or an escalation.
- **Full technical spec (DSPy signature, the decision table, the grounding gate) is in `Agent_Build_Specification.md`, Section 6.**

### N16 — Human Underwriter Review Queue
- **What happens:** Every case that reaches here (hard-decline/refer for compliance, KYC failures needing manual document review, low-confidence agent outputs, agent `escalate_to_human` calls, or agent-recommended REFER/DECLINE) lands in a dashboard showing the full evidence bundle, the ML scores with SHAP attribution, and — where applicable — the agent's full tool-call trace and cited reasoning, side by side. The human underwriter makes the final call; the system never auto-declines an applicant without a human having the ability to review the exact evidence.
- **Agent involvement:** The agent's output and full reasoning trace (where present) is shown as one input among several, clearly labeled as a recommendation, not a decision.

### N17 — Decision Communication (WhatsApp)
- **What happens:** Template message to the customer: policy issued + document link, or a step-up request (specific, e.g. "please share your last 3 months' pharmacy bills") without exposing internal scoring language, or a referral/decline notice with a customer-facing reason category (never exposing model internals, SHAP values, or anything that reveals *which specific signal* triggered the outcome in a way that could be gamed or that constitutes an unexplained adverse decision under the "utmost good faith" doctrine — the reason given must be substantive and defensible, not a black-box "risk score too high").
- **Agent involvement:** None — this is templated communication, informed by the upstream decision but not itself a judgment step.

### N18 — Audit Trail Persistence
- **What happens:** Every case, regardless of path, writes an immutable record: every input captured, every API response, every rule fired, every ML score, the full LLM input/output (if invoked) including cited evidence, the human reviewer's decision (if invoked), and the final outcome. This is not optional or a "nice to have" — it is what makes an adverse decision defensible under Indian consumer-forum scrutiny and is the baseline the EU AI Act / NAIC-style regimes already expect internationally.

---

## 6. Persona Comparison — Where Salaried and Self-Employed Diverge

| Step | Salaried | Self-Employed |
|---|---|---|
| N6 primary income source | EPFO (employer, tenure, contribution-implied salary) | GST/ITR (declared turnover/income) |
| N6 fallback (primary unavailable) | AA bank-statement salary-credit pattern (6–12 months) | AA bank-statement inflow analysis (12 months, longer window for variability) |
| Additional check | — | MCA/Director check (only if company/LLP director or partner) |
| Thin-file tag | `salaried_thin_file` | `self_employed_thin_file` |
| BRE scrutiny band on thin-file tag | Elevated (same treatment as self-employed thin-file) | Elevated |
| Everything else (N0–N5, N7–N18) | Identical spine | Identical spine |

---

## 7. Edge Case & Fallback Matrix

| Scenario | Handling |
|---|---|
| Mobile→PAN reverse lookup returns no match | Fall back to manual PAN entry via Flow (N2, Branch B) — not an error state |
| Mobile→PAN lookup returns multiple candidates | Treat as no-match; manual entry required |
| PAN-on-record mobile ≠ current WhatsApp number | Soft flag, carried forward — not a gate (people change numbers legitimately) |
| e-KYC (DigiLocker/Aadhaar OTP) fails twice | Route to human review for manual document upload, not an auto-decline |
| CKYC record conflicts with captured demographic data | Soft flag, carried forward as evidence the agent may draw on if it clusters with others |
| Liveness/face-match/deepfake fails | Hard decline, human review only — never agent-mediated |
| EPFO record not found (salaried) | AA fallback; tag `salaried_thin_file`; not a decline |
| GST/ITR unavailable or inconsistent (self-employed) | AA fallback; tag `self_employed_thin_file`; not a decline |
| AA consent declined and no primary income source available | Cap eligible sum insured to the no-income-proof STP ceiling; anything above routes to human review |
| Agent calls `request_abha_consent()` and it's declined | Agent receives "declined" as an observation — reasons with missing evidence, does not treat it as negative evidence |
| Agent calls `trigger_rppg_scan()` and it's declined or unavailable | Agent receives "unavailable" as an observation; does not block the journey |
| AML/PEP/sanctions hit at any point | Immediate hard decline/refer to compliance queue — never passes through the agent |
| Agent confidence below threshold, or agent calls `escalate_to_human` | Forced human referral regardless of the agent's tentative verdict |
| Agent reaches 3 tool-call cycles without resolving | Forced `escalate_to_human` — no unbounded loops |

---

## 8. Consent & Compliance Touchpoints

| Step | Consent required | Basis |
|---|---|---|
| N0 | General proposal-processing consent | DPDP Act 2023 — purpose limitation |
| N3 | Aadhaar/DigiLocker e-KYC consent | Aadhaar Act / DigiLocker terms |
| N6-A-Fallback, N6-B-Fallback | Account Aggregator consent (specific purpose: income verification) | AA framework / Sahamati consent artifact |
| N15 — agent calls `request_abha_consent()` | ABHA/HIE-CM consent (specific purpose: underwriting risk assessment), requested only for the specific grey-zone case that needs it | ABDM consent-manager framework, revocable |
| N15 — agent calls `trigger_rppg_scan()` | rPPG facial scan consent (explicit, separate from ABHA) | DPDP — sensitive biometric-adjacent data |
| Every step | Underlying identity/financial data processing | DPDP Act 2023 |

Every consent is independently revocable and independently logged with timestamp and consent-text version at N18.

---

## 9. Non-Functional Requirements

- **End-to-end turnaround target:** clean auto-issue path completes in a single WhatsApp session (well within the 24-hour customer service window); grey-zone/human-review cases communicate an interim status message rather than leaving the customer waiting silently.
- **WhatsApp platform constraints:** Cloud API only (on-premise deprecated); Flows for structured capture and document/selfie upload; template messages required for any business-initiated message outside the 24-hour window; all templates pre-approved by Meta.
- **Availability:** enrichment API fan-out (N9) and ML scoring (N12) must degrade gracefully — a single vendor timeout routes that specific signal to "unavailable" (not a false negative or false positive), and both the BRE and the agent must be built to reason with partial evidence bundles as a normal case, not an exception.
- **Data retention:** aligned to DPDP-mandated retention limits; audit trail (N18) retained per IRDAI record-keeping requirements.

---

## 10. Success Metrics

- **Safe STP rate** — share of applicants auto-issued (target: rises as deterministic + ML layers mature, without the agent being the reason clean cases are delayed).
- **Grey-zone agent agreement rate** — % agreement between the agent's final verdict and the human underwriter's eventual decision on the same case (the core quality gate for trusting the agent with more autonomy over time).
- **Ungrounded-claim rate** — % of audited agent verdicts found to cite evidence not actually present in the bundle or gathered via its own tool calls (target: zero-tolerance, sampled continuously).
- **Tool-call relevance rate** — % of the agent's tool calls that were actually justified by the specific grey-zone flag they targeted, per the logged "why am I calling this" trace (catches an agent that pulls ABHA for a purely financial flag, for example).
- **Non-disclosure catch rate** — validated via back-testing against historical claims-repudiation data where available.
- **Time-to-decision** by path (auto-issue / grey-zone / human-review).
- **Step-level drop-off rate** — where in the WhatsApp journey applicants abandon, to catch friction the underwriting logic itself didn't intend to cause.
