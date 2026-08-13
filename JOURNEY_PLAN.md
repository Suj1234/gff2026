# JOURNEY_PLAN.md — Onboarding Journey (Insurer Demo)

**Purpose.** A demo tool for INSURERS/underwriters that shows the underwriting agent
working end to end: an "applicant" walks a step-by-step onboarding journey (identity →
product → financial → health), and at each step the underwriter watches risk signals
assemble live, ending in the agent's decision + full explainable report.

**This is NOT a customer app.** There is no customer-facing view. Vitals and internal
scores are agent-only.

**Relationship to the engine.** The agent already exists and is green
(`underwriting/`, `POST /underwrite`, 182 tests). This journey only COLLECTS the facts
bundle and calls the agent ONCE. See `IMPLEMENTATION_PLAN.md` for the engine, `CLAUDE.md`
for constraints, `docs/vendor_apis.md` for every vendor API request/response + field
mapping.

---

## 0. Screen shape

```
┌───────────────────────────────────────────────┬──────────────────────────┐
│  CENTER — the journey step (data collected /    │  RIGHT RAIL — LIVE        │
│  pre-filled / entered)                          │  "what the agent sees     │
│                                                 │   at this stage"          │
│  1 Identity → 2 Product → 3 Financial →         │  grouped by Safety-Score  │
│  4 Health → 5 DECISION → 6 Nominee → 7 Pay      │  source group; each chip  │
│                                                 │  green/amber/red + reason │
└───────────────────────────────────────────────┴──────────────────────────┘
```

- **Center = the journey.** Each step collects/pre-fills the data points in §2.
- **Right rail = the agent's running read.** As each API returns, a signal lights up
  green/amber/red with a one-line reason. Every chip is backed by a REAL field in the
  bundle — nothing on the rail is theatre.
- **Step 5** is the payoff: accumulated signals resolve into the Core-6 decision + report.

### Right-rail layout (DEFAULT — confirm)
Grouped panel mapped 1:1 to the Safety-Score source groups (`config.SAFETY_SCORE_WEIGHTS`
/ `scoring.py`): **Identity/KYC · Financial · Occupation/Employer · Medical · Lifestyle ·
Fraud · Litigation · Velocity · Geography · Insurance-portfolio · Contactability.** Each
group shows its running sub-score (0–100) + the chips that moved it. This mirrors the
final report, so the underwriter sees the score being built.
> Alt considered: flat chip list (simpler, less impressive). Chosen grouped because the
> audience is underwriters and it maps to the report.

### Legend
`[USER]` user types/selects · `[FETCH]` API returns it, shown pre-filled · `[DERIVED]`
front-end computes · `→ path` = target in `ProposalInput` (schemas.py).

---

## 1. API readiness

| API | Status | Steps | Doc |
|---|---|---|---|
| Mobile → PAN + profile | ✅ Ready (real) | 1 | vendor_apis §1 |
| PAN → profile (fallback) | ✅ Ready (real) | 1 | vendor_apis §2 |
| Email intelligence | ✅ Ready (real) | 1 | vendor_apis §3 |
| iAdore (Perfios) bank statement | ✅ Ready (`bank_statement.py`) | 3, STEP_UP | vendor_apis §4 |
| NuralX face scan | ✅ Ready (Part A on disk; Part B UI = other project) | 4 | vendor_apis §5 |
| DigiLocker Aadhaar e-KYC (Perfios KYC) | 🟡 **To build** (real + keyed-mock fallback) | 1 | vendor_apis §6 |
| **Mock ABHA** | 🟡 **To build** | 4 | this file §5 |
| The agent `POST /underwrite` | ✅ Ready (182 tests green) | 5 | IMPLEMENTATION_PLAN |

---

## 2. Steps

**Sequence (LOCKED 2026-08-11):** a mobile-verification **landing gate** precedes the
stepper; on OTP + Mobile→PAN fetch the console shell appears at Step 1.

```
LANDING (mobile · OTP · DPDP consent)  →  ① Identity & KYC  →  ② Product & SI (+ Riders)
  →  ③ Financial  →  ④ Health (incl. face scan)  →  ⑤ Decision  →  ⑥ Nominee  →  ⑦ Payment
```

Why this order (researched, insurer/UW demo): **Product before Financial** because income
(Step 3) *validates* the SI the applicant already chose (R-007/R-008). **Product before
Health** is fine for a B2B underwriting demo — the Step-2 premium is labelled **"Indicative
— subject to underwriting"**, and Step 5 is the real decision; the arc is intent → evidence
→ verdict. Face scan moved from Step 1 to **Step 4** (it produces clinical rPPG vitals; its
liveness result still lights the Fraud rail, R-003).

### LANDING — Mobile verification gate  (before the stepper)

Generic-insurer branded gate ("Acme Insurance"), console tone — NOT a customer marketing
page. No stepper/rail here.

| Data point | Origin | API | → engine target |
|---|---|---|---|
| Mobile number (+91) | `[USER]` | — | session anchor |
| DPDP / T&C consent (general application) | `[USER]` | — | `consents[]` |
| OTP → Verify | `[USER]` | — | session anchor |

On success the **Mobile→PAN fetch** (vendor_apis §1) fires → populates `application.applicant.*`,
`pan_verify.*`, `epfo.*`, `gst.*`, `mca_director.*`, `litigation_fir`, `mobile_intel.*` → the
console shell opens at Step 1 with those fields pre-filled.

### STEP 1 — Identity & KYC
Sub-steps: **Profile · Aadhaar (DigiLocker) · Consent**

| Data point | Origin | API | → engine target |
|---|---|---|---|
| Name, DOB, gender, age | `[FETCH]` | Mobile→PAN ✅ | `application.applicant.*` + `pan_verify.*` |
| Address, pincode | `[FETCH]` | Mobile→PAN ✅ | `application.applicant.{address,pincode}` |
| PAN + status | `[FETCH]` | Mobile→PAN ✅ | `signals.pan_verify.{pan,pan_status}` |
| Employment type / employer / UAN | `[FETCH]` | Mobile→PAN ✅ | `application.occupation.declared_type` / `epfo.*` |
| GST (self-emp) + alerts | `[FETCH]` | Mobile→PAN ✅ | `signals.gst.*` (+ activeAlerts) |
| Litigation · Director | `[FETCH]` | Mobile→PAN ✅ | `signals.litigation_fir` · `signals.mca_director.director_default` |
| PAN fallback (mobile→PAN empty) | `[USER]`→`[FETCH]` | PAN→profile ✅ | same as above (no mobile intel) |
| Email | `[USER]`→`[FETCH]` | Email API ✅ | `signals.email_intel` |
| **Aadhaar e-KYC (DigiLocker)** | `[USER]` consent → `[FETCH]` | DigiLocker 🟡 | `signals.aadhaar_ekyc.{name,dob,address,photo}` (+ inline `consents[]`) |
| Consent (Aadhaar/DPDP, inline) | `[USER]` | — | `consents[]` |

**DigiLocker sub-flow (real + keyed-mock fallback, vendor_apis §6):** "Verify via
DigiLocker" → 3-call flow (`/link` → user grants Aadhaar consent+OTP on DigiLocker → `/documents`
→ `/download`) → parsed Aadhaar (name/DOB/gender/address/photo) + PAN → `aadhaar_ekyc.*`,
consumed by **R-015** consistency. No raw Aadhaar number is captured. If `DIGILOCKER_API_KEY`
blank → keyed mock, demo never blocks. **Dedicated Aadhaar consent** is legally separate from
the landing-gate DPDP consent (`India_Health_Insurance_Data_Sources.md`).

**Right rail (Identity/KYC · Contactability · Fraud · Litigation · Occupation groups):**
Mobile (ported/vintage) · PAN valid (R-002) · Aadhaar seeded · Identity consistency
(name/DOB/address across PAN/Aadhaar/CKYC, R-015) · Email fraud (disposable/spam/score/name-match) ·
Litigation (R-018, e.g. "10 criminal, 1 pending" vs "clean") · GST alerts (R-019) · Director (R-012).

### STEP 2 — Product & Sum Insured (+ Riders)
Sub-steps: **Type · Sum insured · Tenure · Riders**

| Data point | Origin | → engine target |
|---|---|---|
| Individual vs Family Floater | `[USER]` | `application.product.type` |
| Sum insured (₹5L/10L/25L/50L/1Cr; ₹3L floor) | `[USER]` | `application.product.sum_assured` |
| Tenure (1/2/3 yr) | `[USER]` | `application.product.tenure_years` |
| Riders / add-ons (IRDAI 2024) | `[USER]` | `application.product.riders[]` (extra="allow") |
| Indicative premium (age·SI·smoker·zone + per-rider loading) | `[DERIVED]` | `application.product.premium` |

**Riders (IRDAI Master Circular 29-May-2024 + market standard):** Room Rent Waiver ·
Hospital Cash · Consumables/Non-Medical · OPD · Critical Illness · Personal Accident ·
Maternity & Newborn · Restoration/Recharge · NCB Booster · Wellness/Preventive.
**Journey-only pricing** — the agent never prices riders (CLAUDE.md forbids touching pricing
knobs). Premium is a small journey-side calculator with a documented indicative loading table
(`# indicative — journey only, not actuarial`). Premium is displayed **"Indicative — subject
to underwriting"** so Step 5 never contradicts a firm quote.

**Right rail:** SI-ceiling check (SI > ₹1cr → manual, R-006); income-multiple check
pending (resolves after Step 3).

### STEP 3 — Financial
Sub-steps: **Income · Source · Bank statement**

| Data point | Origin | API | → engine target |
|---|---|---|---|
| Declared annual income | `[USER]` | — | `financial.declared_annual_income` |
| Source of funds · Purpose of cover | `[USER]` | — | `financial.source_of_funds` · `financial.purpose_of_cover` |
| GST turnover slab (cross-check, read-only) | `[FETCH]` | from Step-1 GST | `gst.turnover_slab` |
| Bank statement upload (+ doc-sharing consent) | `[USER]` upload → `[FETCH]` | iAdore ✅ | `signals.account_aggregator.*` |

Bank statement REPLACES Account Aggregator: PDF upload → iAdore submit→poll→report →
imputed income, avg balance, salary credits. Also becomes the STEP_UP income-corroboration
doc (`follow_up_observations.bank_statement`) if the agent asks for it in Step 5.

**Right rail (Financial group):** income vs SI (R-007) · income proof/thin-file (R-008) ·
declared-vs-statement income gap (fraud) · GST alerts (cancelled / txn delay).

### STEP 4 — Health Declaration (DGH)
Sub-steps: **Screeners · Conditions · Vitals & Lifestyle · Face scan · ABHA**

Progressive disclosure (matches real Indian forms — Niva Bupa / HDFC Life):
1. **6 screener Yes/No** (HDFC-term style): symptom >5 days · diagnostic test advised ·
   medication >7 days · surgery advised/planned · ever had
   [heart/cancer/diabetes/hypertension/hepatitis/mental/epilepsy/respiratory/kidney/HIV] ·
   prior policy declined/loaded.
2. **If any Yes** → body-system conditions checklist (Niva Bupa groups); each ticked
   condition **nests** its own deep-dive (diabetes / hypertension → management + years since
   dx; female → pregnancy) — reveal per-condition, not all at once →
   `application.health_declaration.conditions[]`.
3. **Always:**

| Data point | Origin | → engine target |
|---|---|---|
| Height, weight | `[USER]` | `health_declaration.{height_cm,weight_kg}` |
| BMI | `[DERIVED]` | `health_declaration.bmi` |
| Tobacco/alcohol/drugs (+ quantities) | `[USER]` | `health_declaration.{tobacco,alcohol,drugs}` |
| Ongoing medication, past history | `[USER]` | `health_declaration.{ongoing_medication,past_medical_history}` |
| Family history (first-degree) | `[USER]` | `health_declaration.family_history[]` |
| Prior declined/loaded | `[USER]` | `application.existing_cover_declared[]` |
| **Face scan (liveness + rPPG vitals)** | `[FETCH]` async | NuralX ✅ | `signals.liveness_facematch` (R-003) · `signals.rppg_scan.vitals` (R-017) · `facial_bmi_smoking` |
| ABHA records (+ inline ABHA consent) | `[FETCH]` | Mock ABHA 🟡 | `abha_health_records.*` |

**Face-scan sub-flow (NuralX, agent-driven, async):** agent taps Show QR / Send Link →
applicant scans on own phone → NuralX webhook returns liveness + vitals → rail updates.
Vitals are AGENT-ONLY. If NuralX unreachable mid-demo: chip stays "pending",
`liveness_facematch: unavailable` (engine reasons around it — §11, no crash). Liveness/deepfake
is an *identity* gate (R-003 DECLINE) even though the trigger lives in Health.

**Right rail (Medical · Lifestyle · Fraud groups):** non-disclosure (declared clean vs ABHA
evidence — R-010, the LLM headline) · BMI loading (R-009) · postpone (recent surgery/
pregnancy) · rPPG vital out of range (R-017) · face liveness/deepfake (R-003) · tobacco/lifestyle.

### STEP 5 — THE AGENT DECISION  (single `POST /underwrite`)

All of Steps 1–4 assembled → ONE call. Demo climax.

**Center:** the Core-6 verdict + reason summary, then the full underwriter report
(`report.py`): Safety Score 0–100 + band; per-source risk sections (Low/Mod/High);
fraud/anomaly/graph scores + attribution; every BRE flag; and for grey-zone the LLM's
cited evidence chain (each ruling + the exact grounded data path).

**Right rail:** the accumulated Steps 1–4 signal timeline, now resolved into the verdict.

**The 6 outcomes:**

| Outcome | Meaning | Next |
|---|---|---|
| ISSUE | clean auto-issue | → Step 6 |
| ISSUE_WITH_LOADING | BMI×age loading (actuarial %) | accept → Step 6 |
| STEP_UP | needs a doc/medical (`status:"pending"` + `waiting_on`) | agent requests bank statement (iAdore) or ABHA → re-judge → resolves |
| POSTPONE | recent surgery/pregnancy | ends politely |
| REFER | grey-zone unresolved / fraud-adjacent / litigation | human underwriter queue |
| DECLINE | deepfake/liveness fail (R-003 only) | ends |

### STEP 6 — Nominee  `[USER]`  (display-capture)
Name, DOB, relationship (Spouse/Son/Daughter/Father/Mother/Brother/Sister/Other),
% share, address. Appointee block appears only if nominee DOB < 18 (Insurance Act §39).
→ `application.nominee` (dict, extra="allow"). Captured into the bundle; no gateway.

### STEP 7 — Payment → issuance → free-look  `[USER]`  (display-only)
UPI / card / netbanking → `application.product.payment_mode`. **Section 64VB — risk cover
starts only on premium payment success.** Payment is a **mocked success** (no real gateway)
→ e-policy issued → 30-day free-look copy (IRDAI PPI Regs 2024, uniform).

---

## 3. Every scenario the agent can produce (the demo must render each)

- **Instant DECLINE** — hard gate only (R-001 mobile revocation, R-002 PAN invalid,
  R-003 deepfake/liveness/facematch). Short-circuits before soft rules/LLM.
- **Hard REFER** — R-004 AML/PEP, R-005 age outside 18–55, R-006 SI > ₹1cr. No LLM.
- **POSTPONE** — acute event ≤90 days or active pregnancy.
- **ISSUE (clean)** — all gates pass, zero soft flags, ML clean. No LLM.
- **ISSUE_WITH_LOADING** — BMI×age (+occupation) loading from actuarial table.
- **STEP_UP** — beyond-matrix (senior 46–55 medicals R-005b / high BMI / rPPG R-017) OR
  LLM ruled a flag needs income/medical/identity. `status:"pending"`. One gather → re-judge.
- **Grey-zone → LLM → REFER** — surviving soft flags (non-disclosure, velocity, identity
  cluster, litigation) the LLM can't clear; or fail-safes (LLM down / low confidence /
  ungrounded citation / coverage mismatch). All → human.
- **Grey-zone → LLM → ISSUE** — LLM rules every flag benign_explained, grounded (e.g.
  income corroborated by the uploaded bank statement).

Edge behaviour: partial bundles are normal (missing source never crashes — but a "Low"
section is NOT proof a source was checked, see report.py KNOWN LIMITATION); LLM-down on a
grey-zone case fails safe to REFER.

---

## 4. Live personas (real API data — no identity mocking)

The underwriter enters a real mobile/PAN; the identity/litigation/GST/employment half is
genuinely live. Health + product/SI are entered in the journey. So the outcome is real,
not scripted (except ABHA + the one loading fixture).

- **Salaried, clean** (e.g. mobile `8884609090` / PAN `EKOPS9572K`) → 0 litigation, EPFO
  verified, PAN Active → **ISSUE** if health clean + income covers SI.
- **Self-employed, high litigation** (e.g. mobile `9739780007` / PAN `BHYPM4927Q`) → 10
  criminal cases (1 pending), cheque-bounce (NI Act §138), GST cancelled + txn delay →
  **REFER** on moral hazard — ONCE the litigation adapter exists (§6 gap #1; today it
  scores clean — a silent miss).

---

## 5. Mock ABHA API (to build)

Returns exactly the fields the engine reads (`schemas.AbhaHealthRecords` +
`rules.postpone_check`):
`{status, diagnoses[], icd_codes[], prescriptions[], unstructured_notes[],
days_since_acute_event, active_pregnancy}`.

Powers the non-disclosure demo (R-010 + LLM): "declared no conditions, ABHA shows
undisclosed diabetes/cardiac." A live person's real ABHA is unavailable, so ABHA is
**scripted/keyed** — flagged as mock.

**Keying (DEFAULT — confirm):** keyed off entered PAN/mobile via a small lookup table, so
specific test identities return specific records (feels live on stage).
> Alt: scenario-picker ("non-disclosure case"). Keyed chosen because it's more convincing
> in a live walk-through.

---

## 6. Engine changes required (data/rules only — NO architecture change; deferred until "build")

Each ships with a test; the eval harness already fails a bad change before prod.

1. **Litigation adapter + rule** (`sources/litigation.py`, `rules.py`, `scoring.py`) —
   map the vendor `litigation` payload → `litigation_fir` shape the scorer reads
   (`firs_registered` from `cases[].firDetails[]`; `civil_criminal` from `type`;
   pending/severity/cheque-bounce). **Highest priority — Paulson's whole story.**
2. **GST activeAlerts** penalty (`scoring.py` ± `rules.py`) — isGstCancelled /
   isGstTransactionDelay → occupation/financial penalty + soft flag.
3. **Email intel** (`schemas.py`, `sources/email.py`, `scoring.py`) — model `email_intel`;
   invert vendor 1–100 (higher=safer) → 0–1 (higher=riskier); feed fraud sub-score.
4. **NuralX wiring** — map webhook vitals → `rppg_scan` / `liveness_facematch` /
   `facial_bmi_smoking`.
5. **iAdore wiring** — map bank-statement report → `account_aggregator` /
   `follow_up_observations.bank_statement`.
6. **Mock ABHA API** — §5.
7. **1 loading fixture** — overweight ~48, otherwise clean → ISSUE_WITH_LOADING (so all 6
   outcomes are demoable). Data-only, no engine change.

---

## 7. Open items to confirm before build

- Right-rail layout: grouped-by-source-group (default) vs flat chip list.
- Mock ABHA keying: keyed off PAN/mobile (default) vs scenario-picker.
- Steps 6–7: real nominee capture + real payment gateway, or display-only to complete
  the story.
- NuralX secrets handoff + Part-B UI ownership (other project).

---

## 8. Phase-wise build plan

Sequencing principle: **each phase ends at something demoable**, and earlier phases
unblock later ones. The right rail can only show a signal truthfully once the engine
reads it — so engine gaps (Phase A) precede the UI that displays them (Phases C–D).
Every engine change is data/rules only — no architecture change; each ships with a test
(the eval harness fails a bad change before prod).

### Phase A — Close the "silent miss" engine gaps  (§6 items 1–3, 7)
The right rail would lie without these (e.g. a self-employed applicant's criminal cases
currently score "clean").
- A1. **Litigation** adapter + rule + Safety-Score wiring — *highest value: the
  self-employed REFER story.*
- A2. **GST activeAlerts** — isGstCancelled / isGstTransactionDelay penalty.
- A3. **Email intel** — model it, invert 1–100 polarity, feed fraud sub-score.
- A4. **Loading fixture** — overweight ~48, otherwise clean → ISSUE_WITH_LOADING.
- **Done when:** engine tests green; a self-employed bundle → REFER on litigation;
  ISSUE_WITH_LOADING reachable.
- **Demoable:** `POST /underwrite` (curl/Postman) shows all 6 outcomes correctly. No UI.

### Phase B — Wire the real vendor APIs into the bundle  (§6 items 4–6)
- B1. **iAdore** bank-statement report → `account_aggregator` / STEP_UP `bank_statement`.
- B2. **NuralX** webhook vitals → `rppg_scan` / `liveness_facematch` / `facial_bmi_smoking`.
- B3. **Mock ABHA** API (keyed off PAN/mobile) → `abha_health_records` — independent of A.
- **Done when:** a live mobile number produces a real bundle; bank upload + face scan +
  ABHA feed the engine.
- **Demoable:** the journey's data layer is live end-to-end (API-level, no screens).

### Phase C — The journey UI (center = steps)  [decisions LOCKED 2026-08-11]
- Stack: **FastAPI + Jinja2 + vanilla JS** on the existing app (`GET /journey`); no build step.
- **Landing gate** (mobile · OTP · DPDP consent) precedes the stepper; on Mobile→PAN fetch the
  Shell-A console (DESIGN.md §5) opens at Step 1.
- Steps 1–4 collection screens writing into `ProposalInput` (only the underwriter's action
  inputs on screen — fetched enrichment lands in the bundle and surfaces in the Step-5 report,
  not echoed back). Step 5 single `/underwrite` call + decision/report render; STEP_UP →
  `status:"pending"` + `waiting_on` → gather → re-POST → resolve.
- Step 1 **DigiLocker** Aadhaar e-KYC (real, key in `.env`); Step 2 **riders + journey-only
  indicative premium**; Step 4 **face scan** (NuralX); **Steps 6–7 display-only** (nominee
  captured; payment mocked success → §64VB copy).
- Every component consumes `design-tokens.css` / DESIGN.md — no per-page hex/type/spacing.
- **Done when:** an underwriter can walk a full applicant through on screen to a decision.
- **Demoable:** the whole journey, minus the live signal rail.

### Phase D — The right rail (live agent signals)
- Grouped-by-source-group panel; each API return lights a chip green/amber/red with its
  reason; accumulates into the Step-5 decision.
- **Done when:** the underwriter watches risk assemble step by step.
- **Demoable:** the full GFF demo.

### Phase E — Polish / demo-hardening
- Failure states (NuralX pending/timeout, iAdore poll, LLM-down → REFER fail-safe); the
  two live personas pre-loadable; disclaimers (NuralX "not for underwriting"); free-look /
  §64VB copy.

### Critical path & risk
- **Critical path:** A → B → C → D. A and B partly overlap (B3 mock ABHA is independent
  of A). C cannot start until the bundle shape is final (end of A). D needs C to exist and
  A/B to make signals real.
- **Risk:** Phase B depends on external factors — NuralX secrets / Part-B UI ownership
  (other project) and iAdore reachability. If those slip, A + C + D still demo with
  ABHA-mock + injected face/bank signals.

**Starting point (agreed):** Phase A, beginning with A1 (litigation).

---

## 9. Deferred ledger — raised during Phase B, carry into later phases

Same What/Why/Impact/How/Trigger shape as `IMPLEMENTATION_PLAN.md §13`. Phase B (B1/B2/B3)
is complete and green; these are enhancements surfaced during the Phase-B deep-recheck
that were deliberately deferred (not blockers for the Phase-B done-when). **Do these when
their trigger fires — not before.**

### E2 — Pin the iAdore adapter to a REAL captured report  *(trigger: iAdore reachable + a sample PDF)*
- **What:** `sources/bank_statement.py` maps the iAdore report against *plausible*
  Perfios-style field spellings (`imputedAnnualIncomePaise`, `salaryCreditMonthly`,
  `avgMonthlyBalancePaise`, `incomeBasis`, plus rupee/`summary`/`report` fallbacks). No
  real iAdore JSON report was ever captured — `docs/IAdore Sample Report.pdf` is a
  RENDERED report (layout reference), not the JSON contract, so the true field names are
  unverified.
- **Why:** The adapter's tests feed our own assumed shape back to our own adapter
  (circular). If the live report uses different keys, income maps to all-None.
- **Impact:** A real report in an unknown schema → `account_aggregator` /
  `follow_up_observations.bank_statement` come back with `verified_annual_income: null`.
  R-007/R-008 then read "no income proof" and the STEP_UP re-judge can't corroborate — a
  **silent miss** of the exact kind Phase A fixed for litigation. **Mitigated now** by E3
  (a `logging.WARNING` fires when a report parses but matches no income/balance/salary
  field — the tripwire), but the fix is real field names.
- **How:** Run the shipped repo-root `bank_statement.py analyze(pdf)` once against the
  live iAdore gateway with a sample statement; save the JSON as a fixture
  (`tests/fixtures/iadore_report_real.json`); pin the adapter's `_first(...)` key lists to
  the actual field names; assert the adapter against that fixture (not our synthetic one).
- **Trigger:** iAdore gateway reachable in this environment AND a sample bank-statement PDF
  is available. (Deferred now by decision — no live call made.)

### E6 — Grounding gate checks path EXISTENCE, not value presence  *(engine-owner decision)*
- **What:** `decision._resolve(path, root)` returns True when the dotted path's final key
  EXISTS in the bundle, regardless of whether its value is `None`. So a judge citing
  `follow_up_observations.bank_statement.verified_annual_income` passes the grounding gate
  even if that value is `null` (e.g. iAdore returned nothing — see E2).
- **Why:** Grounding was designed as "the cited path is a real path into the bundle"
  (anti-hallucination), NOT "the cited value is non-null". That's a defensible line, but
  Phase B's income-corroboration flow is exactly where a null-but-present value could let
  a benign ruling through to ISSUE on empty data.
- **Impact:** Low today (the fixture/real gatherer only cite fields they populated), but a
  latent path to a wrong ISSUE if a future gatherer returns a present-but-null field and
  the judge cites it. **Not introduced by Phase B** — pre-existing engine behaviour.
- **How:** Decide the intended contract with the engine owner. If "grounded ⇒ non-null",
  tighten `_resolve` to reject a `None` terminal value (and re-run the eval set — some
  legitimate citations may point at intentionally-null facts). Do NOT change unilaterally.
- **Trigger:** First case where a judge cites a present-but-null gathered field, OR the
  §13 `CONFIDENCE_MIN` calibration pass (fold the decision in then).

### E7 — Carry NuralX secondary vitals through for the right-rail  *(trigger: Phase D)*
- **What:** `sources/nuralx.py to_signals()` maps only the vitals the engine reads today
  (heart_rate, respiratory_rate, spo2, bp → R-017; liveness/deepfake/facematch → R-003;
  bmi/smoking → lifestyle). The webhook's `stressIndex`, `wellnessIndex`, `sdnn`, `rmssd`
  are DROPPED.
- **Why:** The engine has no rule for them, so they're correctly not modeled as decision
  inputs. But the Phase-D right rail ("what the agent sees") may want to DISPLAY them.
- **Impact:** None on any decision. Purely a display-completeness gap for the live panel.
- **How:** When building the right rail, pass the secondary vitals through as
  non-decision `context` (e.g. `rppg_scan.vitals_extra`) so the panel can render them
  without any rule consuming them. Keep them clearly separated from the R-017 vitals.
- **Trigger:** Phase D (the right-rail build), if the design shows secondary vitals.

---

## 9. To do later (deferred — not blocking any phase)

Items surfaced during the Phase-A build + end-to-end re-audit that we consciously chose
**not** to do now, with the reason and the trigger for picking each up. None change a
current verdict; Phase A's four deliverables are done and green (201 passing, 3 skipped).
Format mirrors the engine's own deferred ledger (What / Why deferred / How / Trigger).

### L-A1 — De-duplicate the offline LLM-judge test stub
- **What.** Three test files each carry their own near-identical copy of the offline
  judge stub + `_RULING_BY_FLAG` map: `underwriting/tests/test_pipeline.py`,
  `test_eval.py`, and `test_grounding.py`. Consolidate into one shared helper
  (e.g. `underwriting/tests/_fake_judge.py`) that all three import.
- **Why deferred.** Pure test refactor, zero behaviour change, touches three files. No
  risk in waiting. It carries a real footgun though: a new grey-zone flag (like the
  Phase-A `adverse_litigation` / `gst_alert`) must be added to **every** copy or a fixture
  silently mis-resolves via the escalate default — this already bit once during Phase A.
- **How.** Extract the stub + the ruling map to one module; each test file imports it and
  extends the map in one place. Keep the `test_pipeline.py` unknown-flag guard
  (`assert not unknown …`) pointing at the shared map.
- **Trigger.** Next time a new grey-zone flag is added, OR the start of a test-cleanup pass —
  whichever comes first. Do it before Phase D wires many more signals into the rail.

### L-A2 — Headline Safety Score vs. a litigation/GST REFER (visual contradiction)
- **What.** A case REFERRED purely on criminal litigation still shows a **Safety Score
  ~97 / Low Risk** headline, because `litigation_fir` weight is only 0.05
  (`config.SAFETY_SCORE_WEIGHTS`). The per-group sub-score correctly reads red (litigation
  50/100), but the composite headline looks green on a case going to a human. Same shape
  for a lone GST-cancelled alert.
- **Why deferred.** This is a **calibration** question (the §4A weight table), and CLAUDE.md /
  IMPLEMENTATION_PLAN.md §13 forbid touching the §4A weight knobs before the Phase-6 labeled
  eval set (deferred item D-5). Changing weights now = guessing without ground truth.
  It is also *documented behaviour* — "Safety band ≠ decision" (CLAUDE.md) — so it is not a
  bug, only a demo-clarity concern.
- **How (two independent options, pick at trigger time).**
  1. **No weight change:** add a one-line "moral-hazard / referred" banner to the report's
     `risk_and_fraud_verdict` (report.py) so the narrative doesn't read "Low Risk" next to a
     REFER. Cheap, safe, no calibration.
  2. **Weight re-look:** re-fit `SAFETY_SCORE_WEIGHTS` (esp. `litigation_fir`, `fraud_check`)
     against labeled outcomes — this is exactly Phase-6 D-5, do it *with* the labels, not before.
- **Trigger.** Phase D right-rail polish (for option 1, the banner) OR Phase 6 weight
  calibration with real underwriter labels (for option 2). Do NOT touch weights before then.

### L-A3 — Feed the inverted email fraud score into the ML fraud *score* + SHAP, not only the sub-score
- **What.** A3 wired `email_intel.fraud_risk_score` (the inverted 0-1 vendor number) into the
  Safety **fraud sub-score** (`scoring._s_fraud_check`) — which is what A3 asked for. It does
  NOT yet feed the `risk_scores()` fraud heuristic / its `shap` attribution, so a disposable /
  high-risk email moves the Safety sub-score but not the headline fraud_score or its explanation.
- **Why deferred.** A3's done-when ("feed the fraud sub-score") is met; extending into
  `risk_scores` changes the heuristic scorer's output and its attribution reconciliation —
  scope beyond Phase A, and it overlaps the shadow-ML scorer work (IMPLEMENTATION_PLAN.md §13
  D-6, "swap the heuristic for real SHAP when the models train").
- **How.** Add an `email_fraud` feature to `_FRAUD_FEATURES` + `_fraud_features()` in scoring.py
  (a bounded contribution when `email_intel.fraud_risk_score` is high / disposable), and keep the
  `_reconciled_attribution` sum-to-score invariant. Ship with a test asserting the new feature
  appears in `shap` and the reconciliation still holds.
- **Trigger.** When the fraud/anomaly/graph shadow ML scorer is built (Phase 6, D-6) — fold this
  in with that work so the heuristic and the trained model are changed together, not twice.

### L-A4 — Email adapter: stricter "available" + numeric coercion (minor robustness)
- **What.** `sources/email.py` treats a degenerate envelope like `{"success": true}` (no `data`
  block) as `status: "available"` with all-None facts, rather than `"unavailable"`. Type-confused
  input and stringified scores are already handled (fixed in the Phase-A re-audit); this is the
  remaining borderline case only.
- **Why deferred.** Minor and low-impact: it mislabels an empty vendor envelope as
  "assessed-clean" rather than "not assessed" — the same class as the report.py KNOWN LIMITATION
  (absent source scored clean), which is itself already deferred to the scoring layer. Making it
  stricter risks breaking the intentional pass-through of already-internal-shape fixtures.
- **How.** Require a non-empty `data` payload (or an explicit known field) before returning
  "available"; otherwise "unavailable". Add one test for the empty-envelope case. Do it together
  with the broader absent-source-vs-assessed-clean fix in scoring.py so the two stay consistent.
- **Trigger.** When the scoring layer's absent-vs-clean distinction is addressed
  (IMPLEMENTATION_PLAN.md report.py KNOWN LIMITATION / Phase-5 scoring deferral), OR at Phase B
  when a real email vendor's live responses are first ingested.
