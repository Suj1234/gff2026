# Technical Implementation Plan — Onboarding Risk Assessment Agent
## Companion to the PRD · Individual Retail Health Insurance · WhatsApp-Native

---

## 1. High-Level Architecture

```
WhatsApp Cloud API (Business Solution Provider)
   │  Flows (structured capture, media upload) + Template messages
   ▼
Channel Adapter / Webhook Handler
   ▼
Orchestrator  (LangGraph subgraph for the reasoning/routing logic,
               running inside a Temporal workflow for durable,
               resumable, long-running execution across API calls,
               human-review pauses, and consent waits)
   ├──▶ Identity/KYC API Gateway  (PAN, Aadhaar/DigiLocker, CKYC, liveness/face-match/deepfake)
   ├──▶ Income Verification Gateway  (EPFO, GST/ITR, Account Aggregator)
   ├──▶ Enrichment Gateway  (mobile/email intel, device fingerprinting, geography risk,
   │                          MCA/director/legal, cover-stacking graph)
   ├──▶ Consent-Gated Medical Gateway  (ABHA/HIE-CM, rPPG vendor — invoked BY the agent, not pre-fetched)
   ├──▶ Deterministic Rule Engine (BRE)
   ├──▶ Classical ML Service  (XGBoost risk/fraud score + SHAP, isolation forest, graph model)
   ├──▶ Onboarding Risk Assessment Agent Service  (a staged pipeline — deterministic
   │      flag identification → DSPy `GreyZoneJudge` call → deterministic decision
   │      table → at most one bounded evidence-gathering + re-Judge cycle →
   │      deterministic grounding gate. No open tool-choosing loop.)
   └──▶ Human Review Dashboard / Queue
   ▼
Decision + Audit Store (immutable, append-only)
```

**Three separate concerns, kept separate on purpose:** the Orchestrator decides *what runs when and how the pipeline resumes after a pause*; the BRE and ML Service decide *the score/verdict for anything that doesn't need judgment*; the Agent Service is the only component that decides *what to do next* — it chooses, from a closed toolset, which additional evidence to gather for a specific grey-zone case, then produces a grounded, cited verdict that is either accepted (above confidence threshold) or forced to human review (below it). It never decides anything the BRE or ML layer already owns. DSPy lives entirely inside the Agent Service as a compile-time optimization step for the ReAct loop; it is not the orchestrator and does not touch the BRE or ML layers.

---

## 2. API Inventory

| # | API / Data Source | Vendor category (illustrative) | Purpose | Key output variables | Consent? | Consumed by |
|---|---|---|---|---|---|---|
| 1 | Mobile Vintage & Porting Check | Mobile-intel vendor | Fraud proxy | vintage_days, ported_recently (bool) | No (fraud-check, not personal-data-sharing) | BRE, ML |
| 2 | Mobile Fraud/Revocation List Check | Telecom-linked fraud DB | Hard fraud gate | on_revocation_list (bool) | No | BRE (hard gate) |
| 3 | Device Fingerprinting | Bureau.id-class | Fraud/behavioural signal | device_id, emulator_flag, device_reuse_count | No | ML (graph) |
| 4 | Mobile-to-PAN Reverse Lookup | KYC vendor bureau network | Reduce manual typing | pan_candidate(s), match_confidence | No | N2 pre-fill only, not a decision input |
| 5 | PAN Verification (Advanced) | Perfios/Karza/Digitap-class | Identity verification | name, DOB, gender, masked Aadhaar, Aadhaar-seeding status, mobile-on-record, email, address | No | BRE (hard gate), identity resolution |
| 6 | Aadhaar / DigiLocker e-KYC | UIDAI/DigiLocker-integrated vendor | Verified demographic + photo | full DOB, address, photo | Yes — Aadhaar Act | BRE, N5 face-match reference |
| 7 | CKYC Lookup | CERSAI | Existing KYC record cross-check | existing_record (bool), field-level match/mismatch | No | BRE (soft flag), LLM evidence |
| 8 | Liveness + Face-Match + Deepfake Detection | HyperVerge/Signzy-class | Identity-fraud hard gate | liveness_pass, face_match_score, deepfake_flag | Yes — biometric capture | BRE (hard gate) |
| 9 | EPFO Employment Check | EPFO-integrated vendor | Salaried income verification | employer_name, tenure_months, contribution_band | No (UAN-based, deemed necessary for underwriting) | BRE, ML |
| 10 | GST / ITR Check | Perfios/Karza-class | Self-employed income verification | declared_turnover/income, filing_consistency | No | BRE, ML |
| 11 | Account Aggregator (bank statements) | RBI-licensed AA (Sahamati network) | Income fallback (both personas) | inflow_pattern, estimated_monthly_income, account_vintage | Yes — AA consent artifact | BRE, ML |
| 12 | MCA / Director / Legal Check | AuthBridge/Signzy-class | Moral hazard, self-employed only | director_defaults, litigation_flags, FIR_flags | No | BRE (soft flag), ML |
| 13 | Geography / Pincode Risk Index | Ambee-class / internal index | Location-risk weighting | morbidity_index, fraud_hotspot_flag, hospital_density | No | ML **only** — fairness-tested, never a standalone LLM rationale |
| 14 | Application-Velocity / Cover-Stacking Graph | Internal graph model | Adverse-selection detection | shared_device/bank/nominee_count, velocity_score | No | ML (graph score) |
| 15 | Occupation Hazard Class | Internal mapping table | Mortality/morbidity loading | hazard_class | No | BRE |
| 16 | ABHA / ABDM Health Records (HIE-CM) | NHA consent-manager framework | Medical evidence, where linked | diagnoses, discharge summaries, prescriptions (as available) | Yes — HIE-CM consent, revocable | **Agent tool** `request_abha_consent()` — called only when the agent decides this specific grey-zone case needs it |
| 17 | rPPG Facial Scan | NuraLogix/Binah.ai/FaceHeart-class | Low-friction medical screening trigger | heart_rate, breathing_rate, BP_estimate, stress_indicator | Yes — explicit, separate consent | **Agent tool** `trigger_rppg_scan()` — triggers a step-up **only**, never a scoring input, and only called when the agent decides it's relevant |

**Explicitly excluded from this inventory (per prior research):** a real-time IIB cross-insurer individual health-claims/aggregate-sum-insured lookup at onboarding — this does not exist as a live product today. The cover-stacking signal in row 14 is built from our own application-graph data, not an external IIB pull.

---

## 3. Orchestration State Machine

Implemented as a Temporal workflow (durable, resumable across hours/days for human-review pauses and consent waits) wrapping a LangGraph subgraph (LLM-native control flow for the N15 reasoning step and the N14 routing decision). Pseudo-structure:

```python
# Temporal workflow (durable outer shell)
@workflow.defn
class OnboardingWorkflow:
    @workflow.run
    async def run(self, proposal_id: str):
        await self.step_n0_consent()
        await self.step_n1_mobile_bootstrap()
        pan_result = await self.step_n2_identity_resolution()   # branches A/B internally
        await self.step_n3_ekyc()
        await self.step_n4_ckyc()
        gate_result = await self.step_n5_liveness_facematch()
        if gate_result.hard_decline:
            return await self.route_to_human(reason="identity_fraud_gate")

        persona = await self.step_n6_persona_and_income()       # branches salaried/self-employed
        await self.step_n7_product_selection()
        stp_result = await self.step_n8_stp_hard_gate()
        if stp_result.hard_decline_or_refer:
            return await self.route_to_human(reason=stp_result.reason)

        enrichment = await self.step_n9_enrichment_fanout()      # parallel activities — cheap, unconsented signals only
        health_decl = await self.step_n10_health_declaration()

        ml_scores = await self.step_n12_ml_scoring(enrichment, health_decl)
        bre_verdict = await self.step_n13_bre_final_pass(ml_scores)   # AUTO-ISSUE | HARD-DECLINE | HARD-REFER | GREY-ZONE

        if bre_verdict.outcome == "AUTO-ISSUE":
            return await self.step_n17_communicate(bre_verdict)
        if bre_verdict.outcome in ("HARD-DECLINE", "HARD-REFER"):
            return await self.route_to_human(reason=bre_verdict.reason)   # never touches the agent

        # GREY-ZONE only — the BRE has already identified WHICH flags are ambiguous:
        agent_result = await self.step_n15_grey_zone_pipeline(
            evidence_bundle=self.build_bundle(),
            ambiguous_flags=bre_verdict.ambiguous_flags,   # not just rule IDs — structured, per-flag context
        )
        if agent_result.outcome == "escalated":
            return await self.route_to_human(reason=agent_result.final_verdict.escalation_reason if agent_result.final_verdict else "agent_escalation", agent_rulings=agent_result.rulings)
        return await self.step_n17_communicate(agent_result)

    # --- N15 detail: a staged pipeline, NOT an open agent loop — see Agent_Build_Specification.md §6 ---
    async def step_n15_grey_zone_pipeline(self, evidence_bundle, ambiguous_flags):
        judge = load_compiled_dspy_judge()   # dspy.ChainOfThought(GreyZoneJudge), MIPROv2/GEPA-optimized offline
        rulings = judge(evidence_bundle=evidence_bundle, ambiguous_flags=ambiguous_flags,
                         follow_up_observations={})
        next_step = decide_next_step(rulings.rulings, cycle=1)   # deterministic lookup table, Agent Build Spec §5

        if next_step.kind == "GATHER_EVIDENCE":
            # each call here may pause the workflow (Temporal signal/wait) waiting
            # on a real customer response — this is the ONLY point evidence is
            # gathered dynamically, and it happens exactly once, never in a loop
            observations = await self.gather_flagged_evidence(next_step)
            rulings = judge(evidence_bundle=evidence_bundle, ambiguous_flags=ambiguous_flags,
                             follow_up_observations=observations)
            next_step = decide_next_step(rulings.rulings, cycle=2)   # cycle 2 is terminal — no further gathering, ever

        return deterministic_final_gate(rulings.rulings, next_step, evidence_bundle)  # grounding-checked, never trusts a self-reported confidence alone
```

**Human-review pauses and consent waits inside the single evidence-gathering cycle** (e.g., waiting on a real ABHA consent grant, or a WhatsApp document upload) use Temporal's signal/wait mechanism so the workflow can sit idle for hours without holding compute, then resume with the real observation, not a guess. There is no `interrupt()`-driven multi-step agent loop here — LangGraph is used for the N14 routing decision, not for an open tool-choosing cycle inside N15.

**Failure handling:** every external API call (Section 2's inventory), including the single evidence-gathering cycle, is a Temporal Activity with its own retry policy and timeout; a persistent failure marks that specific signal `"unavailable"` rather than failing the whole workflow — both the BRE and the Judge must be built to reason with partial bundles as the normal case, not the exception (this is explicitly tested in the eval/regression harness, Agent Build Spec §10).

---

## 4. Deterministic Rule Engine (BRE) — Illustrative Rule Table

**Every threshold below is a placeholder in the correct structural shape, not a final number** — actual values must come from the insurer's underwriting manual before go-live.

| Rule ID | Layer | Condition | Action |
|---|---|---|---|
| R-001 | Hard gate | `mobile.on_revocation_list = true` | HARD-DECLINE |
| R-002 | Hard gate | `pan.status ≠ valid` | HARD-DECLINE |
| R-003 | Hard gate | `liveness.pass = false OR deepfake.flag = true OR facematch.score < 0.90` (illustrative) | HARD-DECLINE |
| R-004 | Hard gate | `aml_pep_sanctions.hit = true` | HARD-REFER (compliance) |
| R-005 | Hard gate | `age NOT IN [product.eligible_age_band]` | HARD-REFER (manual UW — outside STP scope regardless of AI) |
| R-006 | Hard gate | `sum_insured > product.stp_ceiling` | HARD-REFER (manual UW) |
| R-007 | Soft/income | `requested_SI > income.verified_annual × N` (illustrative multiple, persona- and income-tier-dependent) | GREY-ZONE (not auto-decline — income mismatch alone is not fraud) |
| R-008 | Soft/thin-file | `income_source = "AA_fallback_only"` (either persona) | Route into elevated-scrutiny BRE band; lowers the auto-issue SI ceiling |
| R-009 | Loading matrix | `BMI × age_band × occupation_hazard_class → loading_class` (lookup table, illustrative bands) | Apply loading OR step-up if combination exceeds standard-rate matrix |
| R-010 | Non-disclosure | `declared.tobacco = "no" AND health_evidence.pharmacy_history CONTAINS smoking_cessation_script` (only fires if ABHA/pharmacy evidence has been gathered via the agent's single evidence-gathering cycle — see Section 6) | GREY-ZONE, re-evaluated after that evidence returns |
| R-011 | Waiting period | `product.waiting_period_trigger conditions met` | Apply appropriate exclusion/waiting-period flag at issuance (not a decline) |
| R-012 | Adverse selection | `application_velocity.cross_product_count_45d ≥ K` (illustrative) `AND` `time_since_last_health_signal < 30d` (if available) | GREY-ZONE |
| R-013 | ML threshold | `ml.fraud_score ≥ high_threshold` | GREY-ZONE (never auto-decline off the ML score alone — always at least a grey-zone review) |
| R-014 | ML threshold | `ml.fraud_score < low_threshold AND all hard gates pass AND no soft flags` | AUTO-ISSUE |
| R-015 | Cluster rule | `≥ 2 soft flags from {ckyc_mismatch, mobile_pan_mismatch, thin_file, moderate_ml_score}` | GREY-ZONE (this is the rule that operationalizes "the risk lives in the relationship between variables, not any single field") |
| R-016 | Geography guardrail | `geography.fraud_hotspot_flag = true` alone, with no other flag | **Does NOT gate or decline on its own** — feeds the ML score only, per the proxy-discrimination guardrail; never a standalone BRE action |
| R-017 | rPPG trigger | `rppg.consented = true AND rppg.BP_estimate/stress_indicator outside normal range` | Trigger step-up (request a proper medical check) — **never** a loading or decline input |

Everything that isn't a hard gate (R-001–R-006) and doesn't cleanly clear (R-014) or cleanly cluster into a known bad pattern lands in GREY-ZONE by design — that's the deliberate, narrow surface the LLM agent operates on.

---

## 5. Classical ML Layer

- **Morbidity/fraud risk model:** gradient-boosted trees (XGBoost), trained on historical proposal-to-outcome data (issued/declined/later-claimed/later-repudiated) once available; until then, run in shadow mode against the BRE and use SHAP-explained outputs purely as *evidence* for the LLM, not as an autonomous gate, until back-tested.
- **Anomaly model:** isolation forest on application-velocity and behavioural features (time-of-day pattern, form-completion speed anomalies, device-reuse).
- **Graph model:** entity-resolution graph linking applications by shared device ID, bank account, nominee, mobile, or (self-employed) MCA director ID — surfaces cover-stacking rings even without a live IIB lookup.
- **Every model output ships with its explanation** (SHAP for the gradient-boosted score, the specific graph edges for the graph score) — this is what R-015 and the LLM's evidence bundle actually consume; a bare numeric score with no attribution is not usable input for either the BRE cluster rule or the LLM.

---

## 6. Onboarding Risk Assessment Agent — Full Spec

**The full pipeline spec now lives in `Agent_Build_Specification.md`, Section 6, and this section is a pointer, not a duplicate — to avoid the two documents drifting apart.** Summary of the design, revised against a proven production pattern (a live claims-correction system built the same way): a staged pipeline, not an open tool-choosing agent loop.

- **The BRE identifies specific ambiguous flags**, not just "grey-zone" — each flag has a type, the rule that raised it, and the relevant slice of evidence (Agent Build Spec §2).
- **One `GreyZoneJudge` LLM call** rules on every flagged item in a single pass, choosing from a fixed, bounded outcome set per flag (`benign_explained`, `needs_income_corroboration`, `needs_medical_check`, `needs_identity_reverification`, `unresolvable_escalate`) with cited evidence (Agent Build Spec §6.1).
- **A deterministic decision table** — not a second LLM call — reads those rulings and decides what happens next: finalize, gather specific evidence and re-Judge exactly once, or escalate (Agent Build Spec §5, §6.2).
- **At most one bounded evidence-gathering + re-Judge cycle**, triggered only by a named, specific unresolved condition — mirrors a production system's gap-closing loop, "bounded; ONLY on [a specific named condition]," never a general retry (Agent Build Spec §6.3).
- **A deterministic grounding gate** — every citation is checked against real evidence before anything is treated as resolved; the model's own stated confidence is never trusted on its own (Agent Build Spec §6.4).

This is a materially more conservative design than an open tool-loop: it will escalate more cases to human review rather than let the model freely decide how much evidence is "enough." That's intentional — it matches the "least agentic design" principle as it's actually been proven out, not as a looser interpretation of it.

### 6.1 Runtime guardrails (unchanged in spirit, revised in mechanism)
- **Schema validation** on the Judge's `rulings` output — malformed output triggers one bounded re-prompt, then forced escalation.
- **Grounding check** — every `cited_evidence` path must resolve against real evidence_bundle/observation data; this is the deterministic gate in Agent Build Spec §6.4, and it is what stands in for the model's own confidence claim.
- **Hard tool boundary** — the deterministic decision table's action set (Agent Build Spec §5) physically does not include anything touching BRE hard-gate logic, AML/compliance, or pricing — there's no path to reach them, by construction.
- **Cycle bound** — hard-capped at one re-Judge cycle, enforced in the decision table itself (`cycle >= 2` branch), not just as a loop counter that could silently be raised.
- **Proxy-discrimination guardrail** — the Judge may cite geography/occupation-derived ML scores as supporting context but never as the sole basis for a non-`benign_explained` ruling.

### 6.2 Production discipline (new — from the reference system's hard-won lessons)
Before this ships, see `Agent_Build_Specification.md` §10 in full — it is not optional reading. The two most consequential items: **DSPy's default call-history retention causes memory blow-up in production and must be explicitly disabled**, and **a growing, underwriter-labeled regression set of grey-zone cases (replayed on every code change, with LLM response caching on for dev speed) is what makes iterating on this safely possible** — build it from day one, not after the first bad surprise.

---

## 7. Data Model — Proposal Object (evolves through the pipeline)

```json
{
  "proposal_id": "string",
  "persona": "salaried | self_employed",
  "identity": { "pan": "...", "aadhaar_ekyc": {...}, "ckyc": {...}, "facematch": {...} },
  "income": { "source": "epfo | gst_itr | aa_fallback", "verified_amount": 0, "tier_flag": "salaried_thin_file | self_employed_thin_file | null" },
  "product": { "type": "...", "sum_insured": 0, "tenure_years": 0 },
  "enrichment": { "geography": {...}, "device": {...}, "velocity_graph": {...}, "mca_legal": {...}, "occupation_hazard_class": "..." },
  "health_declaration": { "bmi": 0, "conditions": [], "tobacco": false, "family_history": [], "declared_existing_cover": [] },
  "ml_scores": { "fraud_score": 0.0, "shap": {...}, "anomaly_score": 0.0, "graph_score": 0.0 },
  "bre_result": { "ambiguous_flags": [ {"flag_id":"...", "flag_type":"...", "related_rule":"R-00X", "context":{...}} ], "outcome": "AUTO-ISSUE | HARD-DECLINE | HARD-REFER | GREY-ZONE" },
  "agent_result": {
    "rulings": [ {"flag_id":"...", "ruling":"...", "cited_evidence":[...], "reasoning":"...", "cycle":1} ],
    "outcome": "resolved | escalated",
    "final_verdict": {"verdict":"...", "confidence_band":"...", "escalation_reason":null},
    "run_metadata": {"per_stage":{...}, "total_cost_usd":0.0, "tags":[...]}
  },
  "human_review": { "reviewer_id": "...", "decision": "...", "notes": "..." } ,
  "final_outcome": "...",
  "audit_log": [ { "step": "...", "timestamp": "...", "actor": "system | agent | human", "detail": {...} } ]
}
```

`agent_result.rulings` (per-flag, per-cycle) plus `run_metadata` (Agent Build Spec §3) is what makes the agent auditable — every flag it ruled on, what it cited, and what it cost, in order, matching the observability discipline of the reference production system.

---

## 8. Sequence Walkthroughs

**Salaried, clean auto-issue:** N0→N1 (mobile clean, vintage >2yr)→N2 Branch A (mobile→PAN hit, confirmed)→N3/N4/N5 all pass→N6-A-i (EPFO found, tenure 4yr)→N7→N8 passes→N9 all clean→N10 no conditions declared→N12 low ML scores→N13 R-014 fires→**AUTO-ISSUE**→N17. The agent is never invoked — no ambiguous flags exist for the BRE to hand it.

**Self-employed, thin-file, grey-zone:** N0→N1 (mobile ported 18 days ago — soft flag)→N2 Branch B (no reverse-lookup match, manual PAN entry)→N3/N4/N5 pass, but N4 shows a CKYC address mismatch (soft flag)→N6-B-ii (no GST record, ITR inconsistent → AA fallback, tagged `self_employed_thin_file`)→N9 velocity graph shows this bank account also appeared on a proposal at another insurer 3 weeks ago (soft flag)→N13: R-015 fires → **GREY-ZONE**, with three structured ambiguous flags handed to N15. **The full worked trace — the Judge's actual rulings, the decision table's actual output, and why this specific case escalates rather than auto-resolves — is in `Agent_Build_Specification.md` §7.** (Worth noting explicitly: the more conservative staged design escalates this exact case to human review, where the earlier ReAct-loop draft would have resolved it to STEP-UP on its own. That's the intended effect of matching a proven, more conservative pattern.)

**Hard-decline, never reaches the agent:** N5 liveness check fails deepfake detection → R-003 fires immediately → routed straight to N16 for a compliance-flagged human review. The agent is never invoked; there is nothing to reason about — this is an identity-fraud gate, not a judgment call.

---

## 9. Guardrail & Compliance Implementation Notes

- **DPDP consent logging:** every consent event (N0, N3, N6 AA fallback, and the single evidence-gathering cycle's `request_abha_consent`/`trigger_rppg_scan` call, when the decision table triggers it) writes a row with purpose text, version, timestamp, and revocation status — queryable independently of the proposal record for regulator/audit requests. Because these are triggered deterministically off a specific `needs_medical_check` ruling rather than pre-fetched for every borderline applicant, the consent log stays a small, purpose-justified footprint by design.
- **ABHA/HIE-CM integration:** implement the standard consent-request → grant/deny/expire lifecycle; a revoked consent must stop further access to that record immediately, and any data already pulled before revocation is handled per DPDP's retention rules, not silently retained.
- **Fairness testing pipeline:** before the ML models and the compiled Judge are promoted from shadow to production, run adverse-impact-ratio testing on outcomes segmented by geography and occupation proxies (not protected characteristics directly, which aren't collected) — this is a release gate, not a post-hoc audit. Include the *ruling pattern* in this testing, not just final outcomes — e.g., check the Judge isn't disproportionately ruling `needs_medical_check` for applicants from particular geographies.
- **Immutable audit store:** append-only, with the full evidence bundle and the full per-flag ruling trace (Section 7) retained per IRDAI record-keeping and DPDP retention requirements.

---

## 10. Recommended Tech Stack

| Layer | Recommendation | Why |
|---|---|---|
| Channel | WhatsApp Cloud API + Flows, via a BSP | Matches existing WhatsApp-native product direction; Flows natively support structured KYC-style capture and media upload |
| Durable orchestration | Temporal | Long-running, resumable workflows across human-review pauses and consent waits; each API call is a retryable Activity |
| LLM-native routing subgraph | LangGraph | Used for the N14 routing decision (AUTO-ISSUE / human / grey-zone); the N15 pipeline itself is a plain staged sequence, not an agent subgraph — see Agent Build Spec §6 |
| Judge optimization | DSPy (`dspy.ChainOfThought`, not `ReAct`) | Compile-time optimization of the single `GreyZoneJudge` signature against a labeled regression set, via `MIPROv2` or `GEPA` (A/B-test both once enough labeled cases exist — the reference production system uses both) |
| Runtime output validation | Pydantic + a custom grounding-check layer (or Guardrails AI) | Schema enforcement + evidence-citation verification on the Judge's `rulings` output |
| LLM provider for the Judge | Open decision — the reference production system runs Gemini 3.1 Flash Lite (medium thinking) for cost; DSPy's model-agnosticism makes this cheap to A/B before committing | See Agent Build Spec §8 |
| Rule engine | Any maintainable BRE (existing internal rule engine, or a rules DSL/Drools-class tool) | R-001–R-017 must be independently testable and versioned outside application code |
| Classical ML serving | Standard model-serving stack (e.g. a lightweight internal service) for XGBoost + isolation forest + graph scoring | Needs to run in shadow mode from day one to build the labeled dataset the DSPy optimizer and the model training both depend on |
| Audit/decision store | Append-only store (e.g. event-sourced log) | Matches the Temporal event-sourcing model and the immutability requirement in Section 9 |

---

## 11. Build Workstreams (parallel, not phased — the LLM agent ships with everything else)

This section is about **engineering sequencing of work**, not a staged rollout of capability — the BRE, ML layer, and LLM agent are all built and integrated together for a single launch, per the product decision already made. The only genuine dependency is data: the ML model needs a labeled dataset to train against, and the DSPy optimizer needs labeled grey-zone verdicts to compile against. Both are bootstrapped by running the BRE live from day one while the ML and LLM components run in **shadow mode** (fully built, fully wired into the orchestrator, receiving live traffic, producing real outputs) — the outputs simply aren't trusted for an autonomous decision until they clear the quality gate. This is a data-maturity gate on *trust*, not a build-order gate on *existence*.

Parallel workstreams:
1. **Channel & orchestration** — WhatsApp Flows for every N0–N10 capture step; Temporal workflow skeleton, including the `step_n15_agent_loop` sequence; LangGraph subgraph for N14/N15.
2. **Identity/KYC/income API integrations** — rows 1–12 in the API inventory.
3. **Cheap unconsented enrichment integrations** — rows 13–15.
4. **Deterministic evidence-gathering actions** — rows 16–17 (ABHA/HIE-CM, rPPG) plus `request_additional_document`, `request_identity_reverification` — built as callable functions the decision table invokes, not as agent-chosen tools.
5. **BRE** — implement and test R-001–R-017 (or the calibrated real version) independently of everything else; this can go live standalone and immediately starts generating the labeled data workstream 6 needs. This now includes the ambiguous-flag identification logic (Agent Build Spec §2), not just the four-way outcome.
6. **Classical ML** — train/serve in shadow mode against live BRE-routed traffic from day one.
7. **Onboarding Risk Assessment Agent Service** — build the `GreyZoneJudge` DSPy signature, the deterministic decision table, the single bounded re-Judge cycle, and the grounding gate (Agent Build Spec §6); run in shadow mode (evaluated against human underwriter decisions on the same cases) until the agreement-rate and zero-ungrounded-ruling quality gates are met, at which point it starts driving live GREY-ZONE routing.
8. **Eval/regression harness** — build the growing labeled grey-zone case set (Agent Build Spec §10) from day one, not after launch; this is what every subsequent DSPy optimization run and every code change gets tested against.
9. **Human review dashboard, audit store, fairness-testing pipeline** — built alongside, not after, since every shadow-mode output (including the full per-flag ruling trace) needs somewhere to be reviewed and logged from day one.

---

## 12. Open Inputs Needed (not research gaps — decisions only Sujeet/Perfios can make)

- Actual underwriting manual thresholds to replace every illustrative number in Section 4.
- Chosen KYC/AA/ABHA vendor contracts (which specific vendor per API inventory row).
- WhatsApp Business Solution Provider selection.
- Confirmation of whether Temporal is already in the Perfios stack or needs to be introduced — if not, a simpler durable-queue alternative can substitute, at the cost of some resumability guarantees.
- Historical labeled outcome data availability (issued/declined/claimed/repudiated) for ML training and DSPy compilation — if none exists yet, the shadow-mode bootstrap in Section 11 becomes the primary data source, and initial launch timelines should account for the shadow period needed to accumulate enough labeled grey-zone cases.
- **LLM provider/model for the Judge calls**, and whether to pin DSPy to the same beta version (`3.3.0b1`) the reference production system runs, for behavioral parity, or track latest stable — see Agent Build Spec §8.
