# Product Note — Agentic Underwriting for Indian Life Insurance

> **Purpose of this document.** A build contract, not a pitch. It answers, concretely:
> what to build, the real role of agentic AI in *life* underwriting (not health), the exact
> use cases, whether it is worth building, who is actually solving this today, and the exact
> rules. It is grounded in (a) how Indian individual-life underwriting actually works in
> 2024–2026 and (b) the engine already in this repo (`underwriting/`), so "how to build"
> means "what you keep vs. what changes," never a fantasy rebuild.
>
> **Status.** Concept pivot from the built HEALTH engine to LIFE. The spine
> (intake → hard gates → grey-zone LLM judge → decision mapper → report) is unchanged and
> correct. The change is confined to Layer 2 (the three underwriting pillars) and one
> gather action. Numbers below are convergent industry practice, tagged for calibration.
>
> **Sources.** Every non-obvious claim traces to a named source in §9. This is not from memory.

---

## 1. The one-paragraph thesis

Indian life insurers already auto-issue the clean, young, low-cover, salaried majority — straight-
through processing is a solved commodity (~54% same-day at ICICI Prudential). The money and the
pain are **not** in decisioning; they are in the **grey zone that falls out of STP**, where a case
waits **2–8 weeks** for a human underwriter and a medical, and where the industry is structurally
**blind** to the three things that actually cause loss: undisclosed medical history, true income /
over-insurance, and moral-hazard / early-claim fraud (life is **~86% of India's ~₹300bn/yr insurance
fraud**). Because of **Section 45 of the Insurance Act** — a policy becomes **incontestable after 3
years, even for fraud** — a wrong "yes" surfaces as a *repudiated early-death claim in court*, so
underwriters over-refer defensively. **The role of an agentic system is to work that grey zone:
read the unstructured evidence a rules engine cannot (ABHA/APS/tele-MER free-text, financial docs),
reason across the combined signals for moral hazard, and produce a Section-45-defensible, cited
reasoning trail — shrinking the human queue and making the cases that still reach a human arrive
pre-reasoned and litigation-ready.** It never sets the sum assured, never sets the loading, never
auto-declines; its most severe output is *refer to a human*. That boundary is not conservatism — it
is the documented consensus of NAIC, EIOPA, the incoming IRDAI framework, and every major reinsurer.

---

## 2. Why LIFE, and why it's different from the HEALTH engine we built

The built engine assesses risk for **one policy year**. Life is a bet on **decades**, at 10–50× the
sum at stake, and the loss driver is different. This table is the whole reason the pivot is worth doing:

| Dimension | Health (built) | Life (this note) |
|---|---|---|
| Risk horizon | 1 year | 20–40 years |
| Sum at stake | ₹5L–₹1Cr | ₹50L–₹25Cr+ |
| Dominant loss driver | claims fraud, over-utilization | **§45 non-disclosure → early-claim repudiation & litigation** |
| The expensive step | pre-auth medicals | **tele/video-MER, physical MER + labs, APS, financial docs** |
| The bottleneck | claims adjudication | **the underwriting decision itself** (days→weeks at higher SA) |
| What a wrong YES costs | one year's claims | a **court case 1–3 years later** under §45 |
| Financial check | income × SI multiple | **HLV + age-multiple + PAN-aggregate cover** (over-insurance = moral-hazard proxy) |

**Key consequence:** the report is not a nicety. In life it *is* the product — it is the file the
insurer defends the decision with when an early death claim is contested. Reproducibility, grounded
citations, and an append-only audit trail stop being "good engineering" and become the deliverable.

---

## 3. The role of agentic AI here — stated precisely (what is AI, what is NOT)

The single most important discipline: **most of underwriting is rules, and must stay rules.** The
agent is a narrow subroutine, not the system. The taxonomy the whole industry now uses:

| Layer | What it is | Who owns the decision | In this system |
|---|---|---|---|
| **Rules / STP** | Deterministic decision tree encoding the manual | Rules | Layers 1, 2, 4 — the bulk |
| **ML scoring** | Trained models (GBM/iso-forest/graph) + SHAP | Rules consume the score | Layer 2 scoring (heuristic interim → shadow ML) |
| **LLM (generative)** | Reads/extracts/reasons over *unstructured* evidence | **Never decides** — reads & reasons | Layer 3, grey-zone only |
| **Agentic** | Orchestrates read → reason → gather → re-judge | Deterministic orchestrator; human owns adverse | Layer 3 loop + human queue |

**The three things the agent does that a rules engine structurally cannot** (this is the entire
justification for the LLM's cost — if a task isn't one of these, it stays deterministic):

1. **Read unstructured evidence → structured facts.** ABHA free-text notes, Attending Physician
   Statements (APS), tele-MER narrative, scanned labs, financial docs. A rules engine cannot parse a
   messy physician narrative; an LLM can. *This is the killer app that is actually shipping today*
   (Swiss Re Underwriting Ease, Aug 2025 — claims 50% manual-effort cut on referred cases). The repo
   already has the seed: `judge.extract_condition`.

2. **Reason across the COMBINATION for moral hazard.** Each signal is individually clean; the *pattern*
   is the risk (over-insurance vs HLV + third-party premium payer + proxy nominee = fronting). You
   cannot write the answer for every input in advance → by definition not a rule → the LLM earns its
   cost here. This is the differentiation window: reinsurers have NOT productized this end-to-end.

3. **Produce a §45-defensible reasoning trail.** Grounded (every claim cites a real evidence path),
   confidence-gated (low → refer, never guess), idempotent (same input → same decision), audited. This
   converts the underwriter's fear — an undefendable YES — into a defensible one.

**The hard lines the agent NEVER crosses** (regulator-mandated, not stylistic):
- Never sets the sum assured. Never sets the loading %. Never auto-declines.
- Never touches AML/PEP/sanctions, the identity/liveness gate, or the age×SA eligibility gate.
- Most severe possible output = **REFER to a human**. DECLINE only ever comes from a deterministic
  hard gate (identity fraud / failed liveness).
- Every adverse or policyholder-affecting decision is reviewed by a named human before action
  (NAIC Model Bulletin; MeitY: no safe-harbour, deployer bears liability).

---

## 4. The use cases we are actually solving (concrete, ranked by value)

Not "faster underwriting." These are the specific, currently-blind seams where the agent creates value.

### UC-1 — Undisclosed medical history at issue-time  *(the §45 driver)*
**Problem.** Underwriting is blind to conditions the applicant doesn't declare. ABHA (525M+ accounts)
and NHCX (47+ insurers) exist but are **not wired into life underwriting at scale** — the insurer
discovers the undisclosed diabetes/cardiac only when the early death claim is investigated. This is
THE dominant repudiation and litigation ground under §45.
**Agent's job.** On a grey-zone case, read the ABHA free-text / APS / prescription history, extract the
condition, run the R-010 crosswalk against the declaration, and rule material vs benign — cited.
**Why agentic.** The evidence is unstructured; the materiality judgment needs context.

### UC-2 — Financial over-insurance & true-income authenticity  *(moral-hazard proxy)*
**Problem.** SA far above Human Life Value / income multiple is the classic over-insurance red flag,
but cash-economy income, inflated ITRs, and cover stacked across insurers (PAN-aggregate) hide it. A
rules engine checks one number; it cannot reason about whether the *financial story coheres*.
**Agent's job.** Reason across declared income vs bank inflows vs ITR vs the SA requested vs existing
PAN-aggregate cover vs who pays the premium — is the SA economically justified, or is this stacking?
**Why agentic.** Cross-document authenticity is a judgment, not a threshold.

### UC-3 — Moral hazard / early-claim fraud patterns  *(the ₹300bn problem)*
**Problem.** Life = ~86% of India's insurance fraud, and detection is **still mostly manual and
post-claim** (only ~2 of ~20 insurers used AI for it). The patterns — early-death (proposal on an
already-ill life), proxy/impersonation at the medical, benami/fronted proposals, backdating — all
**pass individual checks** and surface only at the contested early claim.
**Agent's job.** The cross-signal read: holder-name mismatch + third-party premium + proxy nominee +
sudden large SA + timing = fronting pattern → REFER, cite the combination. No single rule fires.
**Why agentic.** The risk is in the co-occurrence; that is the definition of what rules can't do.

### UC-4 — Defensible auto-clearing of the grey zone  *(the ROI, but validation-gated)*
**Problem.** Every case a human touches costs money and days. Many grey-zone cases are *explainable*
(a declared-vs-imputed income gap explained by variable freelance credits, corroborated by 11 months
of consistent inflow). Today they still wait for a human.
**Agent's job.** Build the grounded, cited case for clearing so a would-be-REFER becomes an auto-ISSUE
*with a trail that survives §45*. **Metric: % of would-be-REFERs safely auto-cleared.**
**Caveat.** This is the biggest ROI *and* the one that requires real labeled false-benign data to be
credible. Demo-only, it is a secondary talking point, never the headline (see §6).

### UC-5 — Section-45 defense-file generation  *(cross-cutting, always on)*
Every decision — YES included — emits the reproducible, cited, audited reasoning trail. Not a use case
you "run"; it's the substrate that makes UC-1–4 trustworthy and deployable.

---

## 5. How to build it — what you KEEP, what CHANGES

**Architecture verdict: the spine is right. Life is a re-parameterization of Layer 2, not a rebuild.**
Layers 0 (intake/consent), 1 (hard gates), 3 (LLM judge + gather/re-judge), 4 (decision mapper +
grounding/confidence gates), 5 (report + audit) are architecturally identical to the built engine.

### 5.1 The flow (target)

```
0 INTAKE & CONSENT GATE   validate · DPDP consent (ABHA = revocable, purpose-bound) · assemble
1 HARD GATES (rules)      identity/liveness/deepfake · AML/PEP · age×SA band → DECLINE/REFER, stop
2 THREE PILLARS (rules)   A. FINANCIAL  max SA = min(age-multiple×income, HLV) + PAN-aggregate
                          B. MEDICAL    declared × age×SA grid → tele-MER / MER+labs / clean
                          C. MORAL HAZARD  over-insurance · stacking · proxy/benami · early-claim band
                          → all clean & within STP grid → ISSUE, stop; else emit grey-zone flags
3 THE AGENT (LLM, grey-zone only)
    ① READ unstructured (ABHA/APS/tele-MER/financial docs) → facts  [prompt-injection guarded]
    ② REASON across the combination for moral hazard
    ③ RULE each flag grounded+cited
    ④ if needs evidence → GATHER ONCE (tele-MER / bank stmt / ABHA consent) → RE-JUDGE  [cap 1]
4 DECISION MAPPER (rules)  rulings → Core-6 · grounding gate · confidence gate · LLM never prices/declines
5 REPORT                   decision + reason trace + append-only audit = the §45 defense file
```

### 5.2 Concrete delta against the current code

| Area | Current (health) symbol | Life change |
|---|---|---|
| Financial pillar | `config.INCOME_SI_MULTIPLE_BY_AGE`, `NO_INCOME_PROOF_SI_CEILING` | Re-value multiples to life bands (§7.2); **add HLV ceiling** = min(multiple×income, HLV); **add PAN-aggregate cover cap** across all policies |
| SA ceiling | `config.STP_SI_CEILING = ₹1cr` | Keep as STP auto-issue ceiling; life high-SA band (>₹1cr) → mandatory financial + moral-hazard review |
| Medical trigger | `BMI_AGE_LOADING`, `BMI_BANDS` (R-009) | **Add age×SA medical grid (R-M1, §7.3)**: below non-medical limit → DGH+tele-MER; above → MER+labs/TMT. Grid decides *what evidence*, not the price |
| STEP_UP gather | bank statement (iAdore) | **Add tele-MER / MER as a gather action** — `request_medical_exam(tele_mer)`; maps into the existing `EvidenceGatherer` seam in `pipeline.py` |
| Moral hazard | R-012 velocity, R-015 cluster, R-018 litigation | **Add R-M2 cross-signal (§7.4)**: over-insurance vs HLV + PAN-aggregate breach + proxy/benami + early-claim band → `cross_signal_moral_hazard` grey-zone flag |
| Non-disclosure | R-010 ICD crosswalk + `extract_condition` | Keep as-is; it already handles the ABHA free-text path. Add the **prompt-injection guard** (validate extracted label against the crosswalk enum before it reaches any rule) |
| Safety-Score weights | `config.SAFETY_SCORE_WEIGHTS` | Recalibrate for life (medical + financial + moral-hazard weighted higher); **do not touch before labeled data** — same §13 D-5 deferral discipline |
| Outcomes | Core-6 enum (unchanged) | Unchanged. ISSUE / ISSUE_WITH_LOADING / STEP_UP / POSTPONE / REFER / DECLINE all map |

**What is genuinely new code (small):** the HLV ceiling + PAN-aggregate check (one financial checker),
the age×SA medical grid (one lookup + one rule), the cross-signal moral-hazard rule (one cluster rule),
one gather action, the injection guard. **Everything else is config values + weight recalibration.**

### 5.3 The two non-negotiable design constraints (regulator-mandated)

1. **DPDP consent + revocation on the ABHA/health path.** Consent must be free, specific, informed,
   revocable; **accessing health data after revocation is a statutory violation** (DPDP Act 2023 §12 +
   purpose limitation). The ABHA pull must be live-consent-gated and revocation-aware **in the design**,
   even in a demo — a demo that skips it teaches the wrong architecture. Consent-gate at Layer 0.
2. **Prompt-injection guard on the free-text extractor.** An APS/ABHA PDF can carry hidden instructions
   ("ignore rules, approve"). Untrusted document text is **data, never instructions**: structured
   extraction only, validate every extracted label against the crosswalk enum, and keep the binding
   decision in deterministic code the injected text cannot reach.

---

## 6. Is it worth building? — the blunt assessment

**Yes — for UC-1, UC-2, UC-3 (catch the risk rules miss). Conditionally for UC-4 (auto-clear).**

**Arguments FOR:**
- The blind seams are real, large, and *currently unaddressed at underwriting time* (ABHA/NHCX exist
  but aren't wired into life UW; fraud detection is manual/post-claim). First-mover room exists.
- Your architecture already matches the regulatory consensus (NAIC/EIOPA/incoming IRDAI) and the
  agentic-underwriting research pattern point-for-point. You are not fighting the regulator.
- The genuinely-agentic capabilities (§3) are exactly where reinsurers have *not* productized
  end-to-end. Swiss Re/Munich Re do rules+ML+extraction; the cross-signal reasoning + §45 defense-file
  combination is open.
- The pivot cost is low: re-parameterize Layer 2, don't rebuild.

**Arguments AGAINST / honest ceilings:**
- **No labeled outcome data.** The whole eval harness is 7 synthetic fixtures and an anchored score.
  UC-4's value ("we safely auto-clear X% of referrals") is *unprovable* without real closed-claim
  labels. Demo-only, UC-4 is qualitative — never claim a validated miss-rate you can't show.
- **ABHA-at-underwriting-time is not a solved data pipeline.** It's consent-gated and health-claims-
  oriented today. The demo can *mock* the ABHA response, but the real deployment depends on a data
  access path that doesn't exist at scale yet. Be honest that this is a bet on where NHCX/ABHA go.
- **The grey zone must be big enough to matter.** If 90% auto-issue and the agent works a sliver,
  measure the sliver on real volume before over-investing in the LLM layer.
- **IRDAI's binding AI framework lands ~Sept 2026.** Design to it now (explainability, human
  accountability, bias testing) so you're not retrofitting.

**Verdict.** Worth building as a *judgment-layer product positioned on the blind seams and §45
defensibility* — NOT as "we automated life underwriting" (that's commodity STP + a claim you can't
back). Build UC-1/2/3 as the demo; hold UC-4 as the roadmap ROI that unlocks when labels exist.

---

## 7. The exact rules (life-specific, calibratable)

> Every `*` threshold is a placeholder pending the insurer's underwriting manual — the *logic* is
> real, the *number* is tagged `# TODO(underwriting-manual)`, exactly as the current engine does.
> India has no public standard underwriting manual; each insurer keeps its own grid, so these are
> the convergent industry practice (±1 band by insurer) from §9's sources.

### 7.1 Hard gates (rules only — can DECLINE) — reuse from built engine
- **R-001** mobile on revocation list → DECLINE (fraud)
- **R-002** PAN status ≠ valid → DECLINE (invalid identity)
- **R-003** liveness fail OR deepfake OR facematch < 0.85* → DECLINE (identity fraud)
- **R-004** AML/PEP/sanctions hit → REFER (compliance; never LLM)
- **R-005** age outside eligible band* → REFER (manual UW)
- **R-006** SA above STP ceiling* → REFER (manual UW)

### 7.2 Financial underwriting (R-007 re-parameterized + new HLV/aggregate)
**Income → max sum-assured multiple, by age** (replaces health multiples):

| Age band | Multiple of annual income* |
|---|---|
| ≤ 35 | 25–35× |
| 36–40 | 20–25× |
| 41–45 | 20× |
| 46–50 | 15× |
| 51–60 | 10× |
| 60+ | 5× |

- **R-F1 (income multiple).** requested_SA > age_multiple × verified_annual_income → `over_insurance`
  soft flag. (This is the current R-007 with life multiples.)
- **R-F2 (HLV ceiling).** max_SA = **min**(age_multiple × income, HLV). HLV = PV of future earnings net
  of self-consumption to retirement (≈20–30× income young, tapering). SA above HLV → `over_insurance`.
- **R-F3 (PAN-aggregate).** total cover across ALL policies (PAN-linked) > age_multiple cap → flag.
- **R-F4 (income proof).** self-employed → ITR 2–3 yrs + CA computation + GST; salaried → Form 16 +
  3mo slips + 6mo bank. Missing proof → no-income-proof SA ceiling ₹50L–₹1Cr* → step-up/refer.
- **R-F5 (income authenticity).** declared income vs bank inflows vs ITR mismatch → grey-zone
  (this is where the LLM reasons, not a threshold decline).

### 7.3 Medical underwriting (R-M1 age×SA grid — new; replaces health BMI-only trigger)
The grid decides **what evidence is required**, never the price. Convergent practice (±1 band):

| Age | Sum Assured | Requirement* |
|---|---|---|
| < 35 | < ₹50L (clean → up to ₹75L–₹1Cr) | DGH + **tele/video-MER**, no labs |
| 35–45 | ₹50L–₹1Cr | MER + FBS + lipid + CBC + **ECG** + urine (CUE) |
| 45–55 | > ₹1Cr or any adverse disclosure | Full panel + ECG + **TMT** + chest X-ray |
| 55+ | any meaningful SA | Complete medicals + specialist/consultant reports |

- **R-M1 (medical trigger).** age×SA above the non-medical limit OR any adverse DGH disclosure →
  STEP_UP with gather action = `request_medical_exam(tele_mer | full_mer)`.
- **R-009 (loading matrix).** BMI × age × occupation-hazard → loading class (actuarial table sets the
  %, not the LLM). Beyond standard matrix → step-up. *(reuse from built engine)*
- **R-010 (non-disclosure).** declared condition set vs ABHA/pharmacy/APS evidence via ICD crosswalk;
  free-text ABHA → `extract_condition` first, then compare. Confirmed material non-disclosure → REFER
  (not a load). *(reuse + injection guard)*
- **R-011 (waiting period / exclusion).** trigger met → apply exclusion, not a decline. *(reuse)*
- **R-017 (tele-MER/rPPG vital).** consented vital outside normal range → step-up (never a
  loading/decline input). *(reuse)*

### 7.4 Moral hazard / adverse selection (R-M2 cross-signal — new; the differentiation)
- **R-012 (velocity/stacking).** cross-product cover count in 45d ≥ K* AND time since last health
  signal < 30d* → `velocity_anomaly`. *(reuse)*
- **R-015 (cluster).** ≥ 2 soft flags co-occur → GREY-ZONE. *(reuse)*
- **R-018 (litigation/FIR).** criminal cases / cheque-bounce / director-default → moral-hazard flag.
  *(reuse)*
- **R-M2 (cross-signal moral hazard — NEW).** fires `cross_signal_moral_hazard` (routes to the LLM,
  does NOT decide) when a suspicious COMBINATION of individually-benign signals co-occurs, in one of
  three patterns:
  - **Fronting/proxy:** mobile holder-name mismatch + premium paid from 3rd-party account + proxy
    nominee + sudden large SA.
  - **Over-insurance/stacking:** SA far above HLV + PAN-aggregate breach + multiple recent proposals.
  - **Early-claim risk:** large SA + recent policy + timing near a health signal + backdating request.
  The judge reasons across the combination and cites the multiple facts; no single rule flags it.

### 7.5 Decision mapping (Core-6, unchanged) — first matching row wins
| # | Condition | Layer | Outcome |
|---|---|---|---|
| 1 | fraud / failed liveness / invalid identity | rules (hard gate) | **DECLINE** |
| 2 | AML / PEP / sanctions | rules (hard gate) | **REFER** |
| 3 | age / SA outside band | rules (hard gate) | **REFER** |
| 4 | recent medical event in postpone window* / active pregnancy | rules | **POSTPONE** |
| 5 | BMI×age×occupation (or confirmed condition) exceeds standard matrix, else acceptable | rules(+LLM fact) | **ISSUE_WITH_LOADING** (table sets %) |
| 6 | all clean, low score, zero flags, within STP grid | rules | **ISSUE** |
| 7 | grey-zone → LLM rules all flags benign, grounded | LLM→rules | **ISSUE** |
| 8 | LLM says a flag needs a doc / medical (tele-MER, bank stmt, ABHA) | LLM→rules | **STEP_UP** (gather once, re-judge) |
| 9 | after the one STEP_UP cycle, still unresolved | LLM→rules | **REFER** |
| 10 | LLM rules a flag unresolvable_escalate | LLM→rules | **REFER** |

**Gates in the mapper (unchanged):** grounding (every citation resolves in the real bundle) → explicit
escalate → confidence (low → REFER) → rows 8/9/7. LLM never sets SA/loading; DECLINE only from row 1.

---

## 8. Who is actually solving this (real deployments vs. hype)

| Player | What they actually do | Real or hype |
|---|---|---|
| **Swiss Re Magnum** | Rules-based STP + ML risk scoring (Milliman Rx). Decides accept + rating. ~75% STP. | **Real, mature.** Rules+ML, not LLM. |
| **Swiss Re Underwriting Ease** (Aug 2025) | LLM extracts/summarizes disclosures, MVR, Rx, EHR, physician statements into one dashboard **for a human**. ~50% manual-effort cut on referred cases. | **Real, new.** LLM as *reader*, not decider — only on already-referred cases. |
| **Munich Re ALLFINANZ + Predictor** | Rules engine core for point-of-sale STP + ML predictive models, explicit explainability emphasis. | **Real.** Rules+ML; generative exploratory. |
| **RGA** | GenAI *update* Q2 2025 names **no deployed UW use case**; discusses potential, stresses "substantial human involvement." | **Cautious / forward-looking.** Don't overstate. |
| **ICICI Prudential Life** (India) | AI doc processing + ML underwriting at scale; ~54% same-day savings issuance; claims settled ~1 day. | **Real, deployed.** Assist + STP, not autonomous LLM. |
| **HDFC Life** (India) | Enterprise gen-AI platform across UW/claims/fraud. | **Real, deployed.** Assist. |
| **Tata AIA** | Underwriting Rule Engine (URE) blends ML + GenAI to auto-decision. | **Real.** Rules+ML+GenAI. |
| **Tele-MER / Video-MER vendors** (QuicSolv etc.) | Remote medical interview with "AI-driven" capture/QA. Supplements, does not replace labs. | **Real** but the AI is capture/QA assist, not underwriting. |
| **Perfios CAM AI** (Aug 2025) | LLM + engines for **credit/lending** underwriting (bank stmt, GST, docs; source traceability; 85% time cut). | **Real** — but **lending, NOT life.** Architectural analogy only. |

**The gap nobody has closed end-to-end:** LLM confined to the grey zone + **cross-signal moral-hazard
reasoning** + **§45-defensible cited reasoning trail** + deterministic-only DECLINE + idempotent audit.
Reinsurers do rules+ML+extraction; the reasoning-over-combination + defense-file layer is open. **That
is the differentiated position — everything else in the stack is commodity (buy, don't build).**

---

## 9. Raw vs. analyzed data — what the agent ingests (the Q1 boundary)

**Core rule: the agent ingests ANALYZED *facts*, and RAW only where no analyzer exists or where
reading IS the judgment.** This is the built engine's "facts in, judgments out" boundary (§1.8),
made concrete for life. It is not a source-by-source rule — it's a *type* rule.

| Data | To the agent as | Why |
|---|---|---|
| Lab values (chol 228, ref <200), BMI, vitals | **ANALYZED** — number + ref range | Extraction/OCR is commodity. Don't make the LLM read a lab PDF — that's a parser you shouldn't own. |
| Bank statement | **ANALYZED** — imputed income, categorized txns | A BSA engine (Perfios/Finbox) does this. Agent reasons about the *result*, not the PDF. |
| PAN status, AML hit, liveness score | **ANALYZED** — structured facts | Deterministic. Rules read directly; LLM never sees unless grey-zone. |
| **APS / physician statement, tele-MER note** | **RAW free-text → agent extracts** | No upstream analyzer for messy clinical prose. See §10 for the exact OCR-vs-extraction split. |
| **ABHA free-text / unstructured notes** | **RAW → `extract_condition`** | Coded ABHA = a rule (crosswalk); un-coded free text needs the LLM to extract the fact first. |
| **The moral-hazard "story"** | **RAW combination of analyzed facts** | Each fact is analyzed; the *judgment about their co-occurrence* is the LLM's job (R-M2, §11). |

**Why NOT "raw everything to the agent" (the tempting mistake):**
- **Cost/latency** — an LLM reading a 40-page MER every case is ~100× a parser doing it once.
- **Reproducibility** — raw→LLM→number is non-deterministic; analyzed→rule→decision is idempotent, and
  §45 defensibility *requires* idempotency.
- **Prompt injection** — every raw doc fed to the LLM is an attack surface (hidden "approve this" text);
  the fewer raw docs on the binding path, the smaller the surface.
- **You don't own analysis** — OCR/BSA/CV are commodities (buy); judgment is the product (build). Feeding
  raw data to the agent means rebuilding the commodity inside the expensive layer.

**The life-specific headline (confirmed by the §13 inventory): MEDICAL is the RAW-heavy pillar for life;
identity, financial, and fraud arrive mostly ANALYZED.** An agent for life must assume an OCR/NLP layer
sits in front of medical; the other pillars consume the internal-contract seam directly.

---

## 10. The APS / free-text extraction capability — precisely (the "killer app," corrected)

**Correction to an earlier overstatement:** the killer app is the *extraction capability*, not "APS." And
APS as a data source barely exists digitally in India — the capability's real Indian home is ABHA free-text.

**Two distinct operations — only the second is agentic:**
1. **PDF/scan → text = OCR.** A commodity parser. **NOT the agent's job.** If a document arrives scanned,
   an OCR engine (or the vendor) turns pixels into text first.
2. **Text → structured, coded medical fact = extraction + interpretation.** *This* is the LLM's job, and
   it is categorically not a parser. An APS/discharge note is unstructured clinical prose:
   > *"Pt is k/c/o T2DM since ~2018, poorly controlled per last HbA1c, on Metformin + recently added
   > Glimepiride. H/o one episode chest discomfort 2022, TMT advised, pt did not follow up. Ex-smoker,
   > quit ~3 yrs back."*
   A parser returns that sentence. It cannot tell you: *Type-2 diabetes (E11), poorly controlled, since
   2018; an unresolved cardiac workup; ex-smoker* — because that mapping requires resolving abbreviations,
   **negation** ("did not follow up" ≠ has cardiac disease), **implied conditions from drug names**, and
   **temporal reasoning**. **OCR reads the ink; the agent reads the meaning.** Only the second step is agentic.

**Where do we get an APS? — the honest answer:**
- An APS is a treating doctor's/hospital's record, requested with the applicant's **signed medical-records
  release**. Mature in the US (vendors like ExamOne pull it).
- **In India there is NO standardized APS API** — manually requested, written-authorization-gated, always
  raw, and the pillar most starved of structured data (§13).
- **Consequence:** for a **demo**, an APS is a realistic mocked scanned note. For **production**, the Indian
  equivalent that actually exists on a rail is **ABHA discharge summaries / free-text notes** — the *same
  extraction problem*, which the engine already handles via `extract_condition`. **Build the capability
  around ABHA free-text; present APS as the US framing of the identical capability.**

---

## 11. The moral-hazard "story" — what it means and why it needs the LLM

A **rule** looks at one fact and asks "is this bad?" A **moral-hazard story** looks at several
individually-fine facts and asks "do these, together, describe a person who shouldn't be insured at this
amount?" The risk lives in the *co-occurrence*, which is why no single rule can see it.

**Worked example — fronting / proxy proposal** (every fact passes its own rule):

| Fact | Rule verdict in isolation |
|---|---|
| PAN valid, face match 0.96, liveness OK | ✅ R-003 passes |
| Age 34, income covers the SA | ✅ R-007 passes |
| Mobile SIM registered to a *different* name | ⚠️ one soft flag — often innocent (family SIM) |
| Premium paid from a *third-party* bank account | ⚠️ one soft flag — often innocent (spouse pays) |
| Nominee is a 58-yr-old parent; applicant young & single | ⚠️ unusual, not damning |
| Sudden ₹2Cr term cover, no prior insurance | ⚠️ large, but he qualifies |

No single rule fires REFER. Read together they tell one coherent story: **a healthy young identity used to
obtain life cover that benefits (or is really on the life of) an uninsurable elderly parent who pays for
it** — textbook fronting, the ₹300bn-fraud pattern, invisible to any one-field rule.

**Why this is definitionally the LLM's job:** you cannot write the answer in advance. "Third-party premium
+ holder mismatch + elderly nominee" is *sometimes* fronting and *sometimes* an honest joint family where
the father genuinely pays. Distinguishing them requires weighing the combination in context — the §1.3
"LLM, not rule" test exactly.

**The three story-shapes** (one mechanism — risk in the co-occurrence):
1. **Fronting/proxy** — the example above.
2. **Over-insurance/stacking** — SA ≫ HLV + cover stacked across insurers + multiple recent applications.
3. **Early-claim setup** — large SA + brand-new policy + timing after a health scare + backdating request.

**Mechanically (R-M2, §7.4):** the rule does NOT decide — it detects the combination and raises one
grey-zone flag `cross_signal_moral_hazard`. The existing judge reasons over the whole bundle and either
clears it (cited innocent explanation) or refers it (citing the combination). **The rule spots that a story
might exist; the LLM reads whether it holds.** That division is the whole architecture.

---

## 12. The role of ML — and the tech-service-provider reality (the Q4 answer)

> **Read this first — the answer to "why not just skip ML and go rules → LLM?"** You can, and today you
> should. Rules make the decision; if the state is decisive, no ML is needed. ML's *only* possible job is
> triage — pushing a borderline case into the grey zone that no single rule caught — and that only works
> **trained on real past-outcome data you don't have.** Untrained, "ML" is just a fuzzy hand-written rule.
> So the real architecture is **rules → (grey-zone?) → LLM**; ML is a **data-gated future upgrade, not in
> the critical path.** §15.4 states this as the exact line to say when challenged. The rest of §12 explains
> the *one* thing a trained model would add and why the agent (not ML) is the differentiation.

### 12.0 In plain terms: ML is the triage nurse

**ML looks at the whole pile of applications and quietly says "this one smells statistically unusual —
look harder." That's it. It's a sniff test, not a decision.** It produces a number whose only job is to
help decide *which cases deserve a closer look*.

**Worked example — Rajesh, 38, declares ₹18L income, wants ₹2Cr cover:**
1. **Rules (the checklist):** PAN valid ✅ · age in band ✅ · ₹2Cr ≤ 25× ₹18L (=₹4.5Cr) ✅ · AML clean ✅.
   Rules say: *nothing breaks a hard limit — on paper, issuable.*
2. **ML (the sniff test):** looking at Rajesh *against thousands of past applicants*, it notices things no
   single rule checks — his device was used by 3 other applications this month; his declared-vs-bank income
   gap sits in an odd band; the timing matches a cluster with more early claims. None breaks a rule. ML
   rolls them into **one number: 0.68.** The number's *entire* job is this fork:
   - ML said **0.05** → "boring, looks like the clean majority" → **issue, don't spend a human or the LLM.**
   - ML said **0.68** → "smells off, don't auto-issue" → **send to the grey zone for the agent to reason.**
3. **Agent (the doctor):** picks up Rajesh *because ML flagged him*, reads the combination (shared device +
   income gap + sudden large cover), reasons whether it's fronting or innocent, clears-with-citation or refers.
4. **Rules again (the decision):** maps it all to the final Core-6 outcome.

**The division:** Rules = *"does he break a rule?"* (checklist) · ML = *"does he smell off vs everyone
else?"* (triage nurse) · Agent = *"is the story actually suspicious?"* (the doctor) · Rules = *"so what do
we do?"* (decision). **ML sits between the checklist and the doctor; its only output is "skip" vs "look closer."**

### 12.0.1 The honest verdict: you don't need a trained ML model today

- ML's whole job is triage — *which cases go to the agent.* **But your rules already do triage**
  (grey-zone detection: R-013 score threshold, R-015 cluster). The rules already route unusual cases in.
- A *trained* model would make that triage smarter — **but only if trained on real labeled outcomes**,
  which as a tech service provider you will not have (§12.1). **An untrained ML model is not a sniff test —
  it is a random number in a lab coat, and that is worse than no ML** because it gives false confidence.
- So keep the **heuristic scorer** you already have: a transparent, hand-written unusualness score
  (shared-device count + income gap + velocity, sensible weights). It does the *same triage job*, is
  explainable, and needs no data. **Call it a heuristic, not ML — that is the honest and more defensible label.**

> **Plain bottom line:** the triage nurse is not the differentiation — every insurer has one. The
> differentiation is **the doctor who can reason about a patient they've never seen before** (the agent),
> and it needs no past data to do its job. Today: **rules triage · heuristic sharpens · agent is the doctor
> · no trained ML needed.**

### 12.1 The technical framing (same thing, precisely)

**What ML's role is:** produce a calibrated risk **score** (probability 0–1) + attribution that the
rules read as a **triage input** — fraud, anomaly, cover-stacking-graph. It is **never** a decision-maker;
it is a feature. It sits *before* the LLM (helps route to grey-zone), never replaces it.

```
ML   → a NUMBER ("fraud-like: 0.71") + why  → feeds TRIAGE (is this grey-zone?)
LLM  → a RULING + citations                 → RESOLVES the grey-zone
Rules→ the DECISION                          → reads both, owns the outcome
```

**Why the agent is NOT just "a model on past data" (the key distinction):** an ML model can only recognize
patterns *frequent in its training history* — it scores a novel fronting scheme "clean" because it has never
seen it. The **agent reasons from world knowledge about *this* case**, so it catches the rare/novel/ambiguous
that no past-data model can, needs none of your data, and explains itself. ML recognizes the frequent-and-
known; the agent reasons about the rare-and-novel. **The differentiation is the reasoning layer, never the model.**

**The tech-service-provider truth (stated plainly):** a fraud/mortality model is only as good as the
labeled outcomes it trains on (closed cases with known fraud/early-death). **As a tech service provider you
will likely never hold that data — the insurer does.** So:

1. **You cannot train a proprietary model. That is a structural data-access gap, not a skills gap.** The
   repo does NOT currently use ML — `scoring.py` is a *documented deterministic heuristic* stand-in
   (`score_source ∈ heuristic|upstream_model`, D-6). That was the right call. **Do not call it "ML" — call
   it a transparent heuristic scorer, which is *more* defensible to a regulator, not less.**

2. **"Use an existing model" — three real options, ranked for your position:**
   | Option | Reality |
   |---|---|
   | **A. Keep the heuristic scorer** | **Best for your position.** Honest, explainable, needs no data, defensible. Not "ML" — a transparent feature-weighted score. |
   | **B. Train inside the insurer's environment on THEIR data** | **The actual product play.** Ship the *architecture* (scoring scaffold + SHAP + shadow-mode harness); their data trains it behind their firewall; you never hold it. This is how Milliman IntelliScript / RGA sell — methodology + engine, run on the client's book. |
   | **C. License a pre-trained mortality score (Milliman/RGA Rx-based)** | Exists but US-trained; reselling, not building. Not your differentiation. |

3. **Where your value is if you can't own the model — NOT in the ML.** Weighting features is commodity;
   everyone can. Your value is the **judgment layer that consumes the score**: grey-zone LLM reasoning,
   grounding gate, §45-defensible audit trail. Rules+ML+extraction is what reinsurers already sell; the
   cross-signal-reasoning + defense-file combination is the open ground (§8). **ML is not your moat. Don't
   invest there. Invest in the layer that doesn't need the data you'll never get.**

   > **Positioning one-liner:** *"We don't need to own a mortality model. We provide the judgment engine
   > that turns any risk score — the insurer's own, or a transparent heuristic — into a defensible,
   > auditable underwriting decision. The score is a plug-in; the judgment is the product."*
   > For a tech service provider, the no-data constraint isn't a weakness — it's *why* you build the layer
   > that doesn't require it.

---

## 13. Data source inventory (India, by pillar, RAW vs ANALYZED)

> RAW = a document/media blob a rules engine cannot read directly (scanned MER PDF, free-text APS, bank
> PDF, selfie/video, raw court text) — needs an OCR/CV/NLP/BSA engine first. ANALYZED = already-structured
> facts (JSON field, lab value + ref range, coded status). Life differs from the built health engine
> mainly in MEDICAL (raw-heavy) and in the cover-stacking registry (arriving April 2026, see below).

**1 · Identity & KYC — mostly ANALYZED**
| Source | India vendor | Raw/Analyzed |
|---|---|---|
| PAN verify | Karza/Perfios, Signzy, HyperVerge | ANALYZED |
| Aadhaar e-KYC (DigiLocker) | DigiLocker via Perfios/Signzy | ANALYZED (embedded photo = raw → face-match) |
| CKYC | CERSAI CKYCRR | ANALYZED |
| Mobile→PAN profile | Karza/Perfios, Digitap | ANALYZED |
| Liveness/face-match/deepfake | HyperVerge, IDfy, NuralX | ANALYZED score **from raw media** — stays deterministic, LLM never touches |
| Video-KYC (VCIP) | Signzy, HyperVerge | raw video → ANALYZED pass/fail |

**2 · Medical — the RAW-heavy pillar (life's differentiator)**
| Source | India vendor | Raw/Analyzed |
|---|---|---|
| DGH (self-declared) | proposal form | ANALYZED (the baseline everything else is checked against) |
| Tele-MER / Video-MER | Medi Assist, Vidal, MediBuddy | **RAW** — physician free-text needs NLP |
| **Physical MER + lab panel** | Dr Lal, Metropolis, Thyrocare via TPA | **BOTH** — old labs = PDF (raw/OCR), new TPA = JSON. **Most reliable life medical source** (gates issuance) |
| ABHA/ABDM records | ABDM HIE-CM, Eka Care | ANALYZED (FHIR) — **but linkage ~15% → <1%** at small clinics |
| **APS / prior-insurer medical** | **no API — manual, written auth** | **RAW** — the classic life deep-dive; see §10 |
| rPPG vitals / facial BMI | NuralX | ANALYZED estimate from raw video — **step-up only, never a rate** |

**3 · Financial — mostly ANALYZED (BSA does the raw→analyzed)**
| Source | India vendor | Raw/Analyzed |
|---|---|---|
| Bank statement / AA | 16 NBFC-AAs (Finvu, CAMS, Perfios Anumati…) + BSA (Perfios iAdore) | AA JSON = ANALYZED; uploaded PDF = raw → **BSA turns raw→analyzed** |
| ITR / Form 26AS | IT e-filing via Karza | ANALYZED (API) / raw (PDF) |
| Form 16, salary slips | Perfios/Signzy parsers | RAW → ANALYZED |
| GST, EPFO, MCA | GSTN, EPFO, MCA21 | ANALYZED |
| CIBIL / credit | TransUnion, Experian, CRIF | ANALYZED — **use as FINANCIAL only, see §5/§15** |

**4 · Moral hazard / fraud — ANALYZED signals, two adapter gaps**
| Source | India vendor | Raw/Analyzed |
|---|---|---|
| Litigation / FIR | eCourts/NJDG via Karza | **Semi-RAW → needs adapter** (repo gap: 10 criminal cases score "clean" without it) |
| Velocity / device | TMT-ID, Bureau, Seon | ANALYZED — **strongest cover-stacking signal** (sidesteps the IIB gap) |
| Email intel | AuthBridge, Digitap, Seon | ANALYZED — **polarity must invert** (vendor higher=safer; engine higher=riskier) |
| PEP / sanctions | Refinitiv, Karza | ANALYZED — stays deterministic; LLM never touches |
| Mobile vintage/porting | mobile-intel aggregators | ANALYZED |

**5 · Industry-shared infrastructure (the life cover-stacking answer)**
- **IIB (Insurance Information Bureau)** — the life cross-insurer body. Per-applicant lookup was historically
  weak, **but the IRDAI Fraud Monitoring Framework mandates real-time sharing by ~April 2026** — the life
  analogue of the motor-IIB query, arriving now. **Until then, catch cover-stacking with velocity/device
  signals, not an IIB lookup.**
- **Insurance Repositories (eIA)** — policies dematerialized, but **not exposed as an underwriting API**.
  Infrastructure without a feed.
- **Bima Sugam** — forward-looking; not a per-applicant UW risk feed yet.

**Two adapter gaps the repo already flags (wire these first):** the **litigation adapter** (without it,
adverse litigation scores clean — a silent miss) and **GST activeAlerts** handling.

---

## 14. Alternate / non-traditional data — what's defensible, what's a landmine (the Q5 answer)

**Bottom line: the validated mortality stack is Rx + medical-claims + credit. Climate/flood/hazard/pincode
is a landmine — do not wire it into a life model.**

| Alternate source | Mortality signal? | India-viable? | Verdict |
|---|---|---|---|
| **Prescription / Rx history** | **Strong, validated** (drugs → conditions) | Nascent, consent-clean | **Wire in** |
| **ABHA / NHCX medical records** | **Gold-standard** (actual history) | **Live, consent-native** | **Wire in — the right rail for India** |
| **Credit / CIBIL** | Validated for mortality (US: RGA TrueRisk Life) | Used for *financial* UW today | **Wire in as FINANCIAL only** — using it as a *mortality* score in India is an unproven proxy-discrimination risk; keep to SI ceilings, log the actuarial justification |
| **Tele-MER vitals / rPPG** | Indirect (a *channel* for BP/BMI) | Growing | **Wire in — step-up only**, never a rate; rPPG accuracy/skin-tone bias unproven |
| **Climate / AQI / flood / heat / geo** | **None at individual level** | No India life program (checked null) | **LANDMINE — do NOT.** Predicts risk at the geography not the life; **pincode proxies caste/religion/income → fails IRDAI's actuarial-justification + anti-discrimination rule** |
| **Wearables / fitness** | Weak, self-selecting, gameable | HDFC Life etc. | **Pricing/rewards + evidence-tier routing only, never the mortality rate** |
| **E-commerce / lifestyle spend** | None validated | — | **Hype + landmine** (DPDP-specificity + proxy risk, no signal) |
| **Telematics (driving)** | None for term life | Motor only | **Category error** — only marginal for accidental-death riders |

**Why climate/flood/hazard is a landmine (since it was specifically asked):** the population epidemiology is
real (≈10 µg/m³ PM2.5 rise ≈ 8.6% higher annual mortality in India) but that is a **portfolio-mortality-
trend** fact, not an individual signal — it can't distinguish two applicants in the same pincode, and its
effect is dominated by confounders (income, cooking fuel, occupation) that are either collected directly or
are protected-class proxies. Climate/CAT data belongs to **property & general** insurance (the asset *is*
the geography); in life the asset is a person — a category error. And **pincode is the single highest
fairness-risk feature in an Indian life model** (proxies caste/religion/income). The repo already treats
geography as ML-only, never a standalone gate (R-016) — **for life, keep it out of the rating path entirely.**

**Regulatory guardrails that make this binding (not stylistic):**
- IRDAI **prohibits discrimination on caste/religion/race** and requires **actuarial justification** for
  every rating factor (Protection of Policyholders' Interests Regs 2024). A factor that proxies a protected
  class fails this — that's the teeth, NOT §45 (§45 is post-issue repudiation, a different thing).
- **DPDP Act 2023**: consent must be free, specific, informed, withdrawable; health data needs explicit
  purpose-bound consent + honored withdrawal. Blanket consent fails the specificity test. This is a hard
  Layer-0 design constraint for the ABHA/Rx path.
- **Incoming IRDAI AI framework** (working group formed ~2025, framework pending): ethical/transparent/
  explainable AI + pre/post-deployment audit. Your grounding gate + audit log + refer-to-human boundary
  are already aligned with where this lands — design to it now.

**Terminology caution for slides:** "Vitality" is NOT a clean named life-underwriting program in India.
Verifiable: **HDFC Life wearable-linked discounts** and **Aditya Birla *Health* HealthReturns** (both
rewards, not underwriting). Do not assert "Aditya Birla Sun Life Vitality" or "Max Life Vitality."

---

## 15. The end-to-end agentic flow — where it's called and how it runs

This is the "how it plugs in + how it runs" section. §15.1 = where the insurer calls it (Q1). §15.2–15.3 =
the internal flow with the exact handoff gate on every arrow (Q2). §15.4 = the plain answer to "why ML at all."

### 15.1 Where the insurer calls the agent (the integration point)

You are **one stateless box** the insurer calls over REST — **not** the customer journey, **not** the case
queue, **not** the long-running wait. The industry statement of this (Higson, 2026): *"The rules engine sits
as a stateless decisioning service behind an API gateway. The PAS or quote-channel calls in… returns a
structured response… **routing is rule-driven, not workflow-driven — workflow systems consume the routing
result, they do not produce it.**"* That sentence is your product boundary.

```
   CHANNELS  (INSURER OWNS)   agent portal · D2C web/app · POSP/banca · broker
        │  proposal submitted
        ▼
   NEW-BUSINESS / PAS  (INSURER OWNS)   TCS BaNCS · DXC Ingenium · LifeAsia · Oracle OIPA · Sapiens
        │  — validates, persists, assigns policy no., ASSEMBLES the bundle
        ▼
   ORCHESTRATION  (INSURER OWNS — Camunda/Appian or PAS-native)   owns the long case, the wait, the queue
        │  canonical underwriting request (REST)
        ▼
   ╔══════════════════════════════════════════════╗
   ║  YOUR AGENT — POST /underwrite (stateless)     ║   ← the ONLY box you build
   ║  intake → rules(+score) → [grey-zone?] → LLM   ║
   ║  → decision + reasons + required-evidence list ║
   ╚══════════════════════════════════════════════╝
        │  structured response
        ▼
   back to orchestrator → PAS → channel;   REFER → underwriter workbench  (INSURER OWNS — separate system)
```

**Who calls you:** the insurer's **New-Business/PAS or the workflow orchestrator in front of it**, over REST,
*after* the journey (theirs) assembled the bundle. This is exactly how a cedent consumes **Swiss Re Magnum** —
Magnum is the decision core; the NB system + Appian workbench are separate layers. **You are the Magnum-
equivalent decision core, minus the journey.** `api.py` already is this shape.

**The week-long medical wait — the rule that matters:** the engine does **NOT** stay open for a week. It
returns *immediately* with a non-terminal decision ("refer / need evidence: medical exam"), names the
requirement, and **the long-running wait lives in the insurer's orchestrator, not in you.** Division of labor:
- **You decide *to* gather** — emit the required-evidence list (this is STEP_UP).
- **The insurer's workflow drives the gather** — orders the medical, waits days, and **re-calls you** with the
  enriched bundle for a fresh decision. You stay stateless; state lives in their workflow.
- **Correction to the demo model:** in production you do NOT hold the case during the wait — the insurer
  re-POSTs `/underwrite` when evidence lands. The repo's `_fixture_gather` simulates the loop synchronously
  for the demo; the `EvidenceGatherer` seam in `pipeline.py` is the exact swap point. One-line conceptual
  change, not a rebuild.

**Where REFER goes:** your job **ends at "REFER + reasons + requirements."** The case lands in an **underwriter
workbench/queue the insurer owns** (Appian Connected Underwriting, Munich Re Workbench, Infosys McCamish); the
human decides; the outcome is written back to the **PAS**, not to you. **You do not build the workbench** — that
keeps you a decision core, not a case-management platform.

**API contract:** *In* = the analyzed data bundle (proposal facts + enrichment; India uses proprietary JSON +
India Stack rails — PAN/AA/ABHA — not ACORD). *Out* = decision + reason codes + **the rule-version that fired**
+ audit trail + the **required-evidence list** (machine-readable STEP_UP). Outcomes: auto-bind / conditional-
with-docs / refer-with-reason / decline — the Core 6.

### 15.2 The internal flow — and the handoff gate on every arrow (Q2)

The order is `intake → rules(+scoring) → [grey-zone?] → LLM → decision mapper → report`. **Correction to a
common assumption:** it is NOT "rules → ML → LLM" as three filters the case flows *through*. **Scoring runs
alongside the rules and is just one input the rules read to decide "is this grey-zone?"** The LLM is reached
only if the *rules* labelled the case grey-zone.

```
0 INTAKE      validate bundle vs ProposalInput        → valid? proceed. invalid → 422, never enters.
     ↓
1 RULES       run_bre(): R-001..R-020 hard gates+flags → emits an OUTCOME LABEL:
  (determ.)                                              DECLINE | REFER | POSTPONE | CLEAN | LOADING | GREY-ZONE
     ↓  ── THE MASTER FORK (pipeline.py:119) ──
     ├── label ∈ {DECLINE,REFER,POSTPONE,CLEAN,LOADING} ──→ SKIP to step 4.  NO LLM, ever.
     └── label == GREY-ZONE  AND  flags not empty       ──→ step 3.
2 SCORING     risk_scores()+safety_score() ALWAYS runs  → a FEATURE the rules already consumed in step 1
  (heuristic) (report needs a Safety Score every case)    (only changes routing via R-013's score flag)
     ↓  (only grey-zone cases arrive here)
3 LLM JUDGE   run_judge(bundle, flags) — grey-zone only → a RULING per flag:
              reasons over the flagged combination        benign_explained | needs_income/medical/identity |
              │                                            unresolvable_escalate
              ├─ any ruling needs evidence? ──→ GATHER ONCE (bank stmt / tele-MER / ABHA) → RE-JUDGE (cycle=2)
              └─ all resolved / escalate / cycle==2 ──→ step 4.
4 DECISION    map_decision(bre, rulings): §7 table,      → FINAL Core-6 verdict, AFTER:
  MAPPER      first-matching-row wins                      GROUNDING gate (every citation resolves) +
  (determ.)                                                CONFIDENCE gate (low → REFER)
     ↓
5 REPORT      build_report(): decision + reasons + append-only audit trail (the §45 defense file)
```

**What passes to the next step, based on what:**

| Handoff | The gate — the exact condition |
|---|---|
| Intake → Rules | Bundle validates against the schema (else 422) |
| **Rules → skip-to-decision OR grey-zone** | **The BRE outcome *label*.** Decisive label → no LLM. Only `GREY-ZONE` + non-empty flags → LLM. *(The single master fork.)* |
| ML's part in that fork | The score fed R-013 *inside* the rules, before the fork. ML gets no gate of its own — it's an ingredient, not a stage. |
| Grey-zone → LLM | Automatic once labelled grey-zone |
| LLM → gather → re-judge | `decide_next_step`: any ruling ≠ `benign_explained` → gather once (cap 2 cycles) |
| LLM → Decision | Rulings + grounding gate (citations resolve) + confidence gate |
| Decision → Report | Always |

**Two things that surprise people (both correct in the code):** (1) **scoring runs on every case** — because
the report always needs a Safety Score — but it only *changes routing* through one rule (R-013); it is not a
stage the case passes through. (2) **Most cases never reach the LLM** — clean→ISSUE, hard-gate→DECLINE/REFER,
pure BMI-loading→ISSUE_WITH_LOADING all short-circuit at the Rules→Decision skip. The LLM is the exception
path, not the mainline — the cost/latency discipline, already built.

### 15.3 The simplest correct architecture (what to actually run today)

```
rules → decisive state?  ── YES ──→ decision, done            (no ML, no LLM)
                         ── NO (grey-zone) ──→ LLM → decision   (the reasoning path)
```

No ML box required. Rules (including a few fuzzy scoring-style rules) do all triage; the LLM does all
reasoning. This is a complete, honest, defensible system on its own.

### 15.4 The answer to "why ML at all? isn't this just rules → LLM?" (say this verbatim)

People keep asking this because, for your position, **they are half-right.** Give them the honest answer:

> *"You're right — rules make the decision, and if the state is decisive we don't need ML. ML would add ONE
> thing: catching a case where the risk is spread across many small signals and **no single rule is broken** —
> the 'this whole thing smells off' case. But that only works trained on real past-fraud data, which we don't
> have. So today we don't run a trained model — we use a transparent scoring heuristic for that triage, and
> honestly that heuristic is a form of rules. ML becomes real, and adds something rules can't, ONLY when the
> insurer gives us labeled outcome data to train on. Until then, the real intelligence isn't ML — it's the LLM
> reasoning in the grey zone."*

Why this answer holds: it concedes the true point (today it's rules-like), locates the actual value correctly
(the LLM, not ML), and gates ML honestly (future, data-dependent). The one thing ML adds — *"the shape of the
whole application resembles past bad outcomes even though every individual number is within limits"* — is
genuinely beyond a single-line rule, but it is **not load-bearing** and **not in the critical path today.**

| | Needs a clear line? | Learns from past data? | Explains itself? |
|---|---|---|---|
| **Rules** | Yes | No | Yes |
| **ML** | No — sees patterns | **Yes — and you have none** | Poorly |
| **LLM** | No — reasons | No — reasons from world knowledge | Yes |

The ML column is the whole argument: it needs the one thing you don't have (data) and does the two things
that matter (reason, explain) worse than the LLM. **For your position, skipping ML today is the correct call,
not a compromise.**

---

## 16. Data ingestion architecture — many sources, many vendors

Answers: "do we need an adapter *and* a rule for every source? Vendor A's PAN ≠ Vendor B's — how do we
manage that?" and "is ingestion part of the agent or a separate layer?" The headline: **the repo's existing
`sources/` package is already the industry-standard pattern — hold the line, don't rebuild.**

### 16.1 The pattern (named + sourced)

Three overlapping patterns, one idea — insulate the judgment engine from vendor mess:
- **Canonical Data Model** (Hohpe, *Enterprise Integration Patterns*) — one internal, vendor-neutral shape
  *per source*; every vendor translates into it. The math: N systems peer-to-peer need N×(N−1) translators;
  routed through a canonical model, **2N**. The rules are written once and never learn Karza ≠ Signzy.
- **Anti-Corruption Layer** (Evans, DDD; documented by Microsoft) — the formal name for "insulate my clean
  domain from someone else's messy model." Its hard rule: **"focus the layer on translation only; avoid
  placing business rules in it."** Adapters stay dumb; rules stay the only place judgment lives.
- **Normalizer** (Hohpe) — detect which vendor, dispatch to a vendor-specific translator. This is *literally*
  the repo's `adapter(key)` → `adapt` registry.

**"One canonical shape per source, one adapter per vendor" is the standard, unambiguously.** CLAUDE.md
already states it and it is correct.

### 16.2 Do we need a rule per source / per vendor? NO — three independent axes

The fear ("N sources × M vendors = rules explosion") dissolves because these scale on **independent** axes,
never multiplied:

| Axis | Scales with | Adding a 2nd PAN vendor… |
|---|---|---|
| **Adapters** | **vendors** (the only thing that grows) | +1 adapter |
| **Canonical schemas** | **sources** (~15, stable) | +0 |
| **Rules** | **underwriting policy** | **+0 rules** |

**Ten PAN vendors → still one R-002.** The rule never sees vendor shape; it reads `pan_status: "valid"`.
Vendor count touches *only* the adapter count — the explosion cannot happen once the ACL is in place.

Two things that are **normal, not problems:**
- **Zero-rule sources** — pure enrichment feeding the score (mobile vintage, geography). A canonical shape,
  no gate. Legitimate.
- **Cross-source rules** — R-010 crosswalk, the consistency check. Correlation across sources is *trivial*
  precisely because everything is already normalized into one vocabulary.

**One-liner:** *adapters scale with vendors, schemas scale with sources, rules scale with policy — keep the
three decoupled and the explosion never happens.*

### 16.3 OCR / APS — the LLM extractor IS an adapter

An APS from Hospital A ≠ Hospital B is the *same problem* as Vendor A ≠ Vendor B PAN. Same pattern; only the
translator's *implementation* changes from field-mapping code to a schema-bound model:

```
structured vendor:  raw JSON → (code adapter)     → canonical fact → same rule
unstructured/APS:   raw text → (schema-bound LLM) → canonical fact → same rule
```

The extractor converges on the **same canonical shape**, so R-010 can't tell whether the condition came from
a clean field or a scanned note. Discipline: bind it to the schema (structured output, not free prose), keep
it a **translator not a judge** (facts, never ISSUE/REFER), fail-safe to `unavailable` on low confidence.
The repo already does this: `extract_condition → _label_to_condition → R-010`.

### 16.4 Part of the agent, or a separate layer? SEPARATE — in front of the agent

> **The normalization layer sits IN FRONT of the agent, not inside it. The agent only ever sees canonical
> facts and must not know a vendor exists.**

```
   VENDOR RESPONSES  (Karza PAN, Signzy PAN, APS PDF, ABHA JSON…)
        │
   ┌────▼───────────────────────────────┐
   │  NORMALIZATION LAYER  (separate)     │  ← adapters + the LLM extractor live HERE
   │  raw vendor shape → canonical facts  │     impure: vendor calls, OCR, retries
   └────┬───────────────────────────────┘
        │  canonical bundle only
   ┌────▼───────────────────────────────┐
   │  THE AGENT  (rules + score + judge)  │  ← never sees a vendor; PURE function
   └──────────────────────────────────────┘
```

Four reasons it must be separate (each load-bearing):
1. **Different kind of concern.** Adapters = *integration* (vendor shapes change constantly). Agent =
   *judgment* (policy, stable). Merge them and every vendor JSON change forces a touch on the decision engine
   — the exact coupling the layer exists to prevent (Microsoft ACL: no business rules in the translation layer).
2. **Idempotency / §45 defensibility depends on it.** The agent must be a **pure function: same canonical
   bundle → same decision** (locked by `test_idempotency_same_input_same_decision`). Ingestion is impure and
   non-deterministic (network, OCR, retries). Mixing them destroys "reproduce exactly how this decision was
   reached" — the IRDAI/§45 requirement. **Impure ingestion and pure judgment cannot share one box.**
3. **Different ownership.** The insurer's PAS/orchestrator assembles the bundle and calls `/underwrite`
   (§15.1); normalization often lives on *their* side or as a thin pre-step. A clean seam keeps the agent's
   contract simple: *"give me canonical facts, I give you a decision."*
4. **Scales differently (§16.2).** Adapters multiply with vendors; the agent doesn't. Separate → adding a
   vendor touches only ingestion, the agent's eval suite stays green.

**The one nuance — the LLM extractor.** It *is* an adapter (unstructured → canonical), so it lives on the
**normalization side** of the seam even though it uses the LLM — it *translates*, it does not judge. The
grey-zone `GreyZoneJudge` is a different LLM call with a different job and lives in the **agent**. Same tool,
two roles, two sides of the seam — don't merge them because "they're both LLM calls."

**In the repo, this is already correct:** `sources/` is a separate package from `rules.py`/`judge.py`/
`decision.py`; `pipeline.py` takes **no dependency** on `sources/` — the bundle arrives already-canonical.
The seam is `adapt_bundle(raw) → ProposalInput → pipeline.run(...)`: left of the arrow = the layer, right =
the agent. **Keep it exactly that way.**

### 16.5 Managing it at scale — recommendation + the one real gap

- **Keep hand-coded adapters. Do NOT adopt a mapping DSL (JOLT/JSONata) yet** — the mappings carry real logic
  (paise→rupees, fail-safe enum defaulting, dropping vendor verdicts), exactly where DSLs get ugly. Revisit
  only when a *single source* hits ~4+ vendors differing by pure structural renames. *(The "4+" is a
  heuristic, not a cited rule.)*
- **Defensive patterns — two already done, two to add:**
  - ✅ **Fail-safe unknown enums** (unknown PAN status → `invalid`; degrades toward REFER, never ISSUE).
  - ✅ **Tolerant Reader** (Fowler) — read only needed fields, ignore the rest; a new vendor field never breaks you.
  - ⚠️ **ADD: contract test** — one parameterized test asserting *every* adapter's output validates against
    its canonical schema (the lightweight Pact — the guarantee that Vendor A and Vendor B both emit a valid
    shape). Small enough to hand-roll in `test_sources.py`.
  - ⚠️ **ADD (the one substantive gap): three-valued source state** — `clean` / `flagged` / **`unavailable`**.
    Never conflate "we checked, it's fine" with "we never got it." Today an absent source scores ~100/"Low"
    and can assert "labs in range" with zero labs (the known Phase-4/5 debt). **For life, an unread APS must
    never read as "healthy" — this is the highest-priority thing to build.**
- **How the reinsurers solve it:** Swiss Re Magnum's "Data Hub" + pre-integrated vendor connectors *is* this
  pattern, sold as a product moat — validating that owning this layer deliberately is right. **ACORD** is the
  industry canonical vocabulary — borrow its field names where free; do NOT adopt its XML wire format
  internally (over-engineering for an internal engine).

**Recommended 7-layer shape (mostly what the repo has):** raw seam → adapter registry (one per vendor,
hand-coded, Tolerant Reader, fail-safe) → canonical model (one schema per source, versioned backward-
compatibly) → **boundary validation + three-valued state** → schema-bound LLM extractor for unstructured →
source/vendor-agnostic rules → contract test + the eval harness.

---

## 17. Code changes for the life pivot — the evidence-based, phased plan

Derived from a full read of `rules.py` + three parallel file-level audits (scoring/report, schemas/config,
tests/fixtures). **Not a guess — every item is file-and-line referenced.** Headline: **no restructuring, no
new package. One genuinely-new schema model (`Aps`), a set of config re-values, a few new rule functions,
and TWO pre-existing real bugs.** The order below is chosen so each phase leaves the suite green and unblocks
the next.

### 18.1 What the audit CORRECTED in the first-pass list
- **No `sum_insured → sum_assured` rename needed** — the schema already uses `sum_assured` (`schemas.py:80`);
  only comments say "sum-insured."
- **The "absent = clean" bug is bigger than one function** — it's the shared `return (100.0, clean-text)`
  idiom across **all 11 sub-scorers** in `scoring.py`; an all-absent bundle scores ~100/"Low"/auto-issue
  with text like "labs in range" on zero labs. Fix touches 11 scorers + `safety_score` renorm + `report._level`
  + possibly schemas, and **moves the eval anchors** — which is why the repo deferred it.
- **A SECOND real bug found:** `safety_score` (`scoring.py:526`) computes `sum_w` but **never divides by it** —
  it just trusts the weights sum to 1.0. Edit the weights so they don't, and the score silently mis-scales,
  no guard. Same seam the absent-source fix needs (renormalize over present groups) → fix both together.
- **Test footgun confirmed:** `_RULING_BY_FLAG` is duplicated in `test_pipeline.py:45-54` (guarded, loud) and
  `test_eval.py:36-45` (**no guard, silent**). A new R-M2 flag added as a *corroborating* flag on an existing
  REFER fixture resolves via the escalate-default and `test_eval.py` **stays green while testing nothing real**
  — the exact Phase-A litigation silent-miss class.
- **Report sections are 1:1-coupled to scorer groups** (`report._sections`): you cannot add a "financial
  justification / HLV" or "moral hazard" report section without adding a scorer group in `SAFETY_SCORE_WEIGHTS`.

### 18.2 Fixture blast-radius (what breaks when config changes) — the key to ordering
- **`INCOME_SI_MULTIPLE_BY_AGE` change flips NO fixture** — every fixture's SI is ≪ any plausible life cap
  even at 10×. Safe to re-value.
- **`rohit_self_employed.json` is the one config-sensitive fixture** — SI = **exactly** `STP_SI_CEILING`
  (10M). R-006 uses strict `>`, so today it doesn't refer on SI; **lower the ceiling and Rohit trips the
  R-006 hard gate → short-circuits to REFER before the grey-zone path**, breaking `expected_bre_outcome=GREY-ZONE`.
  Its expected block must be revisited the moment the SI ceiling moves.
- **`test_scoring.py` Rohit `=65` anchor** breaks (by design, D-5) if `SAFETY_SCORE_WEIGHTS` change OR the
  absent-source fix re-baselines sub-scores.
- **`test_rules.py:212-247` R-009 BMI-loading asserts + `meena_loading.json`** break if the BMI matrix is
  re-parameterized or superseded by R-M1.
- **`product.type` is inert** — never branched on anywhere; `"individual_health"→"term_life"` changes nothing
  until a rule reads it.

### 18.3 The phased plan

**PHASE 0 — De-risk the tests (do BEFORE any rule/flag change; ~half a day).**
Nothing here changes behaviour; it makes every later phase fail *loudly* instead of silently.
1. Consolidate the two `_RULING_BY_FLAG` copies into one `tests/_fakejudge.py`; replicate the
   `test_pipeline.py:113-115` unknown-flag guard into the eval path. *(This is the repo's own deferred L-A1.)*
2. Extend the stub's cycle-2 flip branch (`test_pipeline.py:72-74`, `test_eval.py:59-61`) so a `needs_medical_exam`
   ruling *can* resolve on re-judge — otherwise no life tele-MER case can reach ISSUE in offline tests.
- **Done when:** suite green; a deliberately-unregistered flag fails loudly in *both* pipeline and eval.

**PHASE 1 — The two real bugs + the ceilings that gate everything (the unblock).**
1. **Three-valued source state** (`clean | flagged | unavailable`) — the shared scorer idiom in `scoring.py`
   (11 fns), `safety_score` renormalize-over-present-groups (`:513-527`), `report._level` +"Not Assessed"
   (`:76-83`), and the `SectionEvaluation`/`SafetyScore` schema if it must allow a null sub-score. **Re-baseline
   the `=65` anchor + section-level asserts** (expected loud break). *For life, an unread APS reading as
   "healthy" is a claims liability — this is the #1 correctness item.*
2. **`safety_score` normalize by `sum_w`** (`scoring.py:526`) — fold into #1; it's the same seam.
3. **Re-value the age/SI ceilings** (`config.py`): `STP_AGE_MAX` (55→life, e.g. 65-70), `AUTO_ISSUE_AGE_MAX`
   (45), `STP_SI_CEILING` (1cr→life), `NON_MEDICAL_SI_LIMIT_*`, `NO_INCOME_PROOF_SI_CEILING`,
   `INCOME_SI_MULTIPLE_BY_AGE` (add a 5×@60+ band). **Fix `rohit_self_employed.json`'s expected block** when
   the SI ceiling moves (the one sensitive fixture).
- **Done when:** an older / larger-SA life applicant reaches the reasoning path instead of an instant R-005/R-006
  REFER; an absent source no longer scores clean; suite green on re-baselined anchors.
- **Why first:** without the ceilings, every interesting life case hard-REFERs before any judgment runs — the
  demo would show nothing. Without the absent-source fix, the demo can assert "healthy" on no data.

**PHASE 2 — The life pillars (new rules + the one new schema model).**
1. **`Aps(_Src)` schema model** (the only new source model) + register in `Signals`; wire its free-text into
   the R-010 extractor path (same as `AbhaHealthRecords.unstructured_notes`).
2. **New fields on existing models** (all ride on `extra="allow"`, but must be *named* to be read):
   `Product.plan_variant/premium_payment_term`; `Application.premium_payer/backdating_requested/dependents_count`;
   `FinancialDeclared.hlv/net_worth/liabilities`; `Iib.total_inforce_sa/life_inforce_sa`.
3. **R-F2 (HLV ceiling)** `max_SA = min(age_mult×income, HLV)`; **R-F3 (PAN-aggregate)** total in-force SA vs
   cap (finally consumes `Iib.policies`, unused today). Wire into `run_bre`'s `soft_results` list (`rules.py:849`).
4. **R-M1 (age×SA medical grid)** — a 2-D grid replacing the binary `NON_MEDICAL_SI_LIMIT_*`; decides
   tele-MER vs full-MER, not price. **Decide explicitly whether it supersedes or stacks on R-009's BMI matrix**
   (`meena_loading.json` + the R-009 tests assume BMI-loading is the loading source).
5. **tele-MER gather** — register the triple: `RULING_TO_ACTION` (`decision.py:41`), `_ACTION_TO_SOURCE`
   (`pipeline.py:32`), and the ruling vocabulary in the `GreyZoneJudge` prompt (`judge.py`). Miss any one → the
   flag is silently dropped and the case REFERs.
- **Done when:** a life fixture exercises HLV + PAN-aggregate + the medical grid + a tele-MER gather→resolve,
  each with an `expected` block and both stub maps updated.

**PHASE 3 — The differentiator + the defensibility hardening.**
1. **R-M2 (cross-signal moral hazard)** — raises `cross_signal_moral_hazard` (routes, never decides); reuses
   the R-015 cluster pattern; add flag type to `CLUSTER_FLAG_TYPES` + **both** stub maps (Phase 0 makes this
   loud if missed) + judge prompt guidance to reason over the combination and cite it.
2. **Prompt-injection guard** on `extract_condition` — output must be a known crosswalk label or `unavailable`,
   never free-form onward. + a few adversarial fixtures (hidden "approve this" in an APS note; present-but-null
   cited field). *Makes the §45 "grounding gate holds" claim true, not asserted.*
3. **Extend the R-010 crosswalk** (`CONDITION_TO_ICD`/`DRUG_TO_CONDITION`) to mortality-relevant conditions
   (cancer, hepatitis, mental health, HIV, respiratory, kidney — the JOURNEY_PLAN screener set). UC-1 is only
   as good as this table.
- **Done when:** the "rules all-green, agent refers on the combination" demo runs end-to-end and cited; the
  injection red-team suite passes.

**EXPLICITLY DEFERRED (do NOT do now):** re-weight `SAFETY_SCORE_WEIGHTS` / re-fit `BMI_AGE_LOADING` to
per-mille EM mortality tables (D-5 — needs labeled data); train ML (§12); durable-async tele-MER pause
(L-1 — the synchronous `pending` stands in); the `_s_financial`/`_s_velocity` triple-recompute (D-7, pure
waste, no output change).

### 18.4 The honest one-paragraph answer to "is this all?"
No single-file eyeball pass would have caught it — the three parallel audits found **two real bugs**
(absent-source-scored-clean across 11 scorers; `safety_score` never normalizing), **one silent test footgun**
(`test_eval.py` unguarded stub), **one hard-gate trap** (Rohit sits exactly on the SI ceiling), and **one
coupling** (report sections ↔ scorer groups) that the first list missed. But the *shape* holds: it is still
a re-parameterization + a handful of new rules + one new schema model + two bug fixes — **no restructuring,
no new package.** Phase 0 (de-risk tests) → Phase 1 (bugs + ceilings) → Phase 2 (pillars) → Phase 3
(differentiator + hardening), each leaving the suite green.

---

## 18. Sources

**How Indian life underwriting works (mechanics, grids, §45, fraud):**
- Medical-test grids: ACKO https://www.acko.com/life-insurance/medical-test-for-term-insurance/ ·
  Algates https://algatesinsurance.in/term-insurance-underwriting-india-guide/ ·
  PolicyBazaar https://www.policybazaar.com/term-insurance/articles/list-of-medical-tests-required-for-term-insurance/
- Income multiples / max SA: Ditto https://joinditto.in/articles/life-insurance/maximum-sum-assured-in-term-insurance/
- HLV: Canara HSBC https://www.canarahsbclife.com/blog/life-insurance/what-is-human-life-value-and-how-to-calculate-it · Kotak Life https://www.kotaklife.com/insurance-guide/about-life-insurance/what-is-human-life-value-and-how-to-calculate-it
- STP reality (ICICI Pru ~54% same-day): https://www.fmlive.in/technology-is-helping-icici-prudential-life-bring-down-its-savings-cost-to-premium-ratio/
- Tata AIA URE: https://www.tribuneindia.com/news/business/future-ready-protection-tata-aias-digital-leap-redefining-life-insurance-experience/amp
- Tele-MER/Video-MER: PolicyBazaar https://www.policybazaar.com/term-insurance/articles/everything-about-tele-medical-checkup-for-term-insurance/ · QuicSolv https://www.quicsolv.com/blog/insurance-verification/video-mer-help-insurance-companies-offer-higher-sum-assured-lower-premium/
- **Section 45**: Insurance Act 1938 §45 https://indiankanoon.org/doc/695200/ · PolicyBazaar https://www.policybazaar.com/life-insurance/section-45-of-the-insurance-act-1938/ · Mondaq https://www.mondaq.com/india/insurance-laws-and-products/1739750/
- Claim settlement ~97.8% FY24: https://www.oquilia.com/news/irdai-claim-settlement-ratio-fy24-25-explained
- Fraud scale (~₹300bn, ~86% life, manual/post-claim detection): RGA https://www.rgare.com/knowledge-center/article/global-claims-views-india---fraud-detection-tools
- Fraud patterns: PolicyBazaar https://www.policybazaar.com/life-insurance/articles/most-common-types-of-life-insurance-frauds-in-india/ · OneAssure https://www.oneassure.in/insurance/life-insurance-guides/top-10-life-insurance-frauds-in-india-lessons-on-prevention-and-detection
- ABHA / NHCX: https://en.wikipedia.org/wiki/National_Health_Claims_Exchange · https://nathealthindia.org/wp-content/uploads/2025/06/National-Health-Claims-Exchange_Latest.pdf

**Agentic AI in underwriting (state of play, architecture, regulation):**
- Swiss Re Underwriting Ease: https://www.insurancebusinessmag.com/reinsurance/news/breaking-news/swiss-re-launches-aipowered-underwriting-ease-to-streamline-life-insurance-reviews-546156.aspx · Magnum: https://www.swissre.com/reinsurance/life-and-health/solutions/magnum.html
- Munich Re ALLFINANZ + Predictor: https://www.munichre.com/automation-solutions/en/our-solutions/ALLFINANZ-automated-life-underwriting-platform.html
- RGA GenAI Q2 2025: https://www.rgare.com/knowledge-center/article/genai-in-insurance-update--q2-2025
- Agentic-RAG underwriting architecture (route/reflect, RAG grounding, refer-on-uncertainty): https://arxiv.org/html/2607.07858
- NAIC Model Bulletin on AI (human review of adverse decisions): https://www.bipc.com/when-algorithms-underwrite-insurance-regulators-demanding-explainable-ai-systems
- IRDAI AI working group (formed 19 Jun 2026): https://www.insurancebusinessmag.com/asia/news/technology/indias-insurance-regulator-steps-in-to-govern-ai-adoption-579846.aspx
- IRDAI Regulatory Sandbox 2025: https://www.mondaq.com/india/insurance-laws-and-products/1773748/
- DPDP Act 2023 × insurance/health data: https://www.mondaq.com/india/privacy-protection/1618790/ · https://datasecure.ind.in/blogs/health-data-spotlight/
- Prompt injection (incl. document-based): https://www.evidentlyai.com/llm-guide/prompt-injection-llm
- Perfios CAM AI (credit, not life): https://fintech.global/2025/08/15/perfios-ai-launches-cam-ai-to-cut-underwriting-time-by-85/

**Data infrastructure / vendors (§13):**
- IIB & IRDAI Fraud Monitoring Framework (real-time April 2026): https://healthapp.iib.gov.in/IIB/AboutUs.htm · https://ankura.com/insights/playbook-to-unlocking-the-power-of-irdais-2025-insurance-fraud-monitoring-framework · https://legistify.com/learn/irdai-fraud-monitoring-framework/
- Account Aggregator ecosystem (16 NBFC-AAs): Sahamati https://sahamati.org.in/account-aggregators-in-india/ · PIB https://www.pib.gov.in/PressReleasePage.aspx?PRID=2162953
- Life MER / lab panel: HDFC Life https://www.hdfclife.com/term-insurance-plans/medical-tests-for-term-insurance · Axis Max Life https://www.axismaxlife.com/blog/term-insurance/why-medical-test-important-term-insurance
- Insurance repositories / eIA / dematerialisation: https://www.pbpartners.com/articles/generic/insurance-repositories-in-india-and-their-benefits · https://www.shankariasparliament.com/current-affairs/dematerialisation-of-insurance-policies

**Alternate data & fairness (§14):**
- PM2.5 → mortality (portfolio-trend, not individual): Lancet Planetary Health 2024 https://www.thelancet.com/journals/lanplh/article/PIIS2542-5196(24)00248-1/fulltext · GeoHealth/PMC https://pmc.ncbi.nlm.nih.gov/articles/PMC11333718/
- Geo/CAT is property & general, not life: https://www.a3logics.com/blog/geospatial-data-for-insurance-industry/ · https://www.esri.in/en-in/industries/insurance/overview
- IRDAI anti-discrimination + actuarial justification: Niva Bupa on IRDAI https://www.nivabupa.com/health-insurance-articles/underwriting-meaning-in-insurance-process-and-guidelines-by-irdai.html · Protection of Policyholders' Interests Regs 2024 https://www.lexology.com/library/detail.aspx?g=f66a1060-3144-4352-927a-949976e2b46b
- Rx / credit → mortality (validated, US): Munich Re Milliman Risk Score 2.0 https://www.munichre.com/us-life/en/insights/product-innovation/alternatives-for-stratifying-mortality-risk-milliman-risk-score-2-0-prescription-drugs.html · RGA TrueRisk Life / credit+mortality https://www.rgare.com/knowledge-center/article/how-credit-data-enhances-mortality-risk-prediction-a-new-study-by-rga
- Wellness = rewards not underwriting: HDFC Life wearable discounts https://www.oneassure.in/insurance/life-insurance-guides/sync-apple-watch-term-insurance-discounts · Aditya Birla Health HealthReturns https://joinditto.in/articles/health-insurance/aditya-birla-health-insurance-healthreturns/

**Verification caveats (carry these — do not overclaim):**
1. No single authoritative life-industry STP %; ICICI Pru ~54% same-day (savings) is the best proxy;
   "95% STP" is vendor material for simplified-issue only.
2. Non-medical-limit grids and income multiples are convergent practice, **not** an IRDAI standard;
   ±1 band by insurer.
3. RGA's ₹300bn / 86%-life fraud figures trace to an older (~2011–12) survey — order-of-magnitude,
   not FY24-fresh.
4. No verified live deployment of ABHA/NHCX pull *inside a life insurer's underwriting engine* yet —
   it is the emerging/intended data source, currently health-claims-oriented and consent-gated.
5. IRDAI's binding AI framework does not exist yet (working group reporting ~Sept 2026); today's
   governance is DPDP + sandbox + the incoming framework.
6. **APS has no standardized API in India** — always manual, written-authorization-gated, raw. The
   deployable Indian equivalent of the extraction capability is **ABHA free-text**, which the engine
   already handles. Do not present APS as a live Indian data source.
7. **No trained ML model exists in the repo** — `scoring.py` is a documented deterministic heuristic
   (D-6). "ML-powered" would be an overclaim; call it a transparent heuristic scorer.
8. **Credit→mortality validation is US/RGA/Milliman context.** The signal likely transfers; the Indian
   *regulatory permission* to use credit as a mortality rate does NOT. Use credit as financial-only.
9. **IIB per-applicant life cover-stacking lookup** is not confirmed at field granularity pre-2026; the
   April-2026 Fraud Monitoring Framework is the fix. Use velocity/device signals until then.
10. "Vitality" is not a verified named *life* underwriting program in India (see §14).
