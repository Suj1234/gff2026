# Five Scenarios + Exactly How To Test This

## 0. Read this before anything else

A phone number does not go "into the agent." It goes into a **fixed, deterministic pipeline** that runs the same way for every single proposal, agent or no agent:

```
mobile checks → mobile→PAN lookup → PAN verify → e-KYC → CKYC → liveness/face-match
→ persona question → EPFO or GST/ITR (+ AA fallback if needed) → product selection
→ STP hard gate → enrichment fan-out → health declaration → ML scoring → BRE
```

**Every one of those API calls fires exactly once per proposal.** None of them repeat. None of them are "asked again" by the agent. This whole sequence runs for Suresh, for Ramesh, for everyone — it's what fetches "all the detail." By the time it finishes, the BRE has already decided one of four things: AUTO-ISSUE, HARD-DECLINE, HARD-REFER, or GREY-ZONE.

**The agent (the Judge) only exists for the GREY-ZONE outcome.** It is called:
- **Zero times** — if AUTO-ISSUE, HARD-DECLINE, or HARD-REFER (this is most cases).
- **Exactly once** — if GREY-ZONE and every flagged ambiguity resolves in a single pass.
- **Exactly twice, never more** — if GREY-ZONE and one flag needs a specific follow-up (a document, an ABHA consent) gathered once, then re-judged once.

That's the whole answer to "one time, two times, or multiple times" — it's hard-capped at two, and zero is the most common case by far.

---

## 1. Five Scenarios

### Scenario 1 — Suresh Kumar, 28, salaried software engineer, Bangalore
**Outcome: AUTO-ISSUE. Agent invoked: 0 times.**

| Step | API/Action | What comes back (mock) | Layer |
|---|---|---|---|
| Entry | WhatsApp sender number = `+919845XXXXXX` | — | deterministic |
| Mobile checks | `mobile/vintage-check`, `mobile/fraud-check` | `vintage_days: 1204, on_revocation_list: false` | deterministic |
| Identity | `identity/mobile-to-pan` | `match_found: true, pan_masked: "ABCXX1234F", confidence: 0.96` | deterministic |
| PAN verify | `identity/pan-verify` | `status: valid, name_match: Y, dob_match: Y` | deterministic |
| e-KYC | `identity/aadhaar-ekyc` | `status: success, dob: 1998-03-14` (age 28) | deterministic |
| CKYC | `identity/ckyc-lookup` | `record_exists: true, address_match: true` | deterministic |
| Liveness | `identity/liveness-facematch` | `liveness_pass: true, face_match_score: 0.98, deepfake_flag: false` | deterministic |
| Persona | "Salaried" tapped | — | deterministic |
| Income | `income/epfo-check` | `record_found: true, employer_name: "Infosys Ltd", tenure_months: 36` | deterministic |
| Product | ₹10L individual term health | — | deterministic |
| STP gate | age 28 in band, SI in ceiling, no AML hit | pass | deterministic (R-001–R-006) |
| Enrichment | geography, velocity, occupation | all clean, no flags | deterministic |
| Health decl. | BMI 22, non-smoker, no conditions | — | deterministic |
| ML scoring | `fraud_score: 0.04` | low | deterministic |
| BRE final pass | R-014: low fraud score + all gates pass + zero soft flags | **AUTO-ISSUE** | deterministic |

**Customer sees:** policy document, immediately, in the same WhatsApp session. The Judge is never called — there is nothing ambiguous to hand it.

---

### Scenario 2 — Ramesh Iyer, 45, self-employed hardware shop owner, Mumbai
**Outcome: HARD-REFER (compliance). Agent invoked: 0 times.**

| Step | API/Action | What comes back (mock) | Layer |
|---|---|---|---|
| Mobile checks | vintage/fraud | clean | deterministic |
| Identity | mobile→PAN lookup | `match_found: false` (older number, no bureau linkage) → manual PAN entry | deterministic |
| PAN verify | `identity/pan-verify` | `status: valid` | deterministic |
| e-KYC, CKYC, liveness | all pass | — | deterministic |
| Persona | Self-employed | — | deterministic |
| Income | `income/gst-itr-check` | `gst_registered: true, filing_consistency: high` | deterministic |
| STP gate — AML/PEP/sanctions screen | name+DOB proximity match against a PEP list entry | `hit: true` | deterministic (R-004) |

**BRE result:** R-004 fires the instant the AML/PEP screen returns a hit → **HARD-REFER, routed straight to N16's compliance queue.** Nothing downstream of this even runs — no enrichment fan-out, no health declaration, no ML scoring. **The agent is never invoked, and structurally cannot be** — there is no path from a compliance hit into the Judge. A human compliance reviewer sees this, not the agent, ever.

---

### Scenario 3 — Priya Desai, 31, salaried marketing manager, Pune
**Outcome: GREY-ZONE → resolved in one pass. Agent invoked: 1 time.**

Everything through health declaration is clean **except two soft flags:** her mobile is 22 days old (recently ported), and the velocity graph shows this same mobile number appeared on a term life proposal at a different insurer 40 days ago (smaller sum insured, different product). Two soft flags → R-015 fires → **GREY-ZONE**, with two structured ambiguous flags handed to the agent.

**Judge, cycle 1** — one call, rules on both flags at once:
```json
{"rulings": [
  {"flag_id": "flg_001", "ruling": "benign_explained",
   "cited_evidence": ["ambiguous_flags[0].context.related_proposals[0]"],
   "reasoning": "Different insurer, different product (life vs health), smaller SI band, 40 days ago — consistent with routine comparison shopping."},
  {"flag_id": "flg_002", "ruling": "benign_explained",
   "cited_evidence": ["identity.pan_verification.mobile_on_record_masked", "identity.ckyc.field_comparison"],
   "reasoning": "PAN, Aadhaar, CKYC, and liveness all independently confirm identity; the mobile mismatch is explained by a recent number change, not fraud."}
]}
```
Decision table: **both `benign_explained`** → finalize immediately, no follow-up needed → **STEP-UP** (light — a contact confirmation, not a document request).

**Agent invoked: exactly 1 time.** No evidence-gathering action fires at all — the existing evidence bundle was already enough.

---

### Scenario 4 — Anjali Rao, 39, self-employed boutique owner, Ahmedabad
**Outcome: GREY-ZONE → needs one follow-up → resolved. Agent invoked: 2 times.**

Identity is completely clean. Two flags: no GST record and an inconsistent ITR → AA fallback used for income (`self_employed_thin_file`); and a velocity-graph hit (a smaller-band proposal at another insurer, 3 weeks ago). Two flags → **GREY-ZONE.**

**Judge, cycle 1:**
```json
{"rulings": [
  {"flag_id": "flg_001", "ruling": "benign_explained", "reasoning": "Comparison shopping, different SI band — same pattern as Scenario 3."},
  {"flag_id": "flg_002", "ruling": "needs_income_corroboration", "cited_evidence": [], "reasoning": "AA-derived estimate has no document corroboration yet."}
]}
```
Decision table: one flag unresolved → **GATHER_EVIDENCE**, deterministically maps `income_thin_file` → `request_additional_document(doc_type="recent_bank_statement")`. This fires the WhatsApp template, the workflow **pauses for real** waiting on Anjali to actually upload a statement — this could be minutes or hours later, and the Temporal workflow just sits there without holding compute.

**Anjali uploads a statement.** Observation: `{"status": "uploaded", "doc_type": "recent_bank_statement"}`, deterministically parsed and confirmed consistent with the AA estimate.

**Judge, cycle 2** (the one and only allowed re-Judge, now with the bank statement in `follow_up_observations`):
```json
{"rulings": [{"flag_id": "flg_002", "ruling": "benign_explained",
  "cited_evidence": ["follow_up_observations.flg_002.doc_type", "enrichment.bank_statement_income_check.consistent_with_aa"],
  "reasoning": "Uploaded statement corroborates the AA estimate.", "cycle": 2}]}
```
Decision table cycle 2: all `benign_explained` → finalize → **STEP-UP**.

**Agent invoked: exactly 2 times** (cycle 1 and cycle 2), with exactly one evidence-gathering action (the document request) in between. It stops here regardless of outcome — there is no cycle 3.

---

### Scenario 5 — Vikram Mehta, 52, self-employed real estate consultant, Delhi
**Outcome: GREY-ZONE → escalates with strong evidence. Agent invoked: 2 times. This is the case the whole system exists to catch.**

Identity and income are clean. One flag: the velocity graph shows he applied for a very similarly-sized health cover at another insurer just **9 days ago**, and that application shows as "declined" in the graph's cross-reference. At age 52, a second similar-SI application within days of a decline elsewhere is a materially different pattern from Scenarios 3/4's comparison-shopping.

**Judge, cycle 1:**
```json
{"rulings": [{"flag_id": "flg_001", "ruling": "needs_medical_check",
  "cited_evidence": ["ambiguous_flags[0].context.related_proposals[0]"],
  "reasoning": "Near-identical SI application 9 days ago, declined elsewhere, at age 52 — this pattern warrants a medical evidence check before treating it as routine shopping."}]}
```
Decision table: `needs_medical_check` → **GATHER_EVIDENCE** → deterministically maps to `request_abha_consent()`. WhatsApp consent message goes out; Vikram grants consent; ABHA returns a discharge summary from six weeks ago for a cardiac procedure — **not declared anywhere on his health declaration form**, which had said "no conditions."

**Judge, cycle 2** (with the ABHA record in `follow_up_observations`):
```json
{"rulings": [{"flag_id": "flg_001", "ruling": "unresolvable_escalate",
  "cited_evidence": ["follow_up_observations.flg_001.records[0].structured_diagnosis_codes"],
  "reasoning": "ABHA record shows a cardiac procedure 6 weeks prior to application, not disclosed on the health declaration. This is a non-disclosure finding requiring underwriter judgment, not an automated decline.", "cycle": 2}]}
```
Decision table: `unresolvable_escalate` present → **ESCALATE**, reason `unresolvable_ruling`.

**Agent invoked: exactly 2 times**, one evidence-gathering action (ABHA). **The agent never declines Vikram itself** — it routes to N16 with the full ruling trace and the ABHA evidence already attached, so the human underwriter sees the exact finding immediately instead of having to re-investigate from scratch. This is deliberate: a REFER/DECLINE with real contestability consequences stays a human call, even though the agent did the work of catching it.

---

## 2. How you actually run a test — three levels, in order of how early you can start

### Level 1 — Isolated agent-run test (no phone, no WhatsApp, works from day one)
Each scenario above is directly a test fixture. Save one as JSON, e.g. `tests/fixtures/vikram_mehta.json`, containing the `evidence_bundle` + `ambiguous_flags` at the exact point the BRE hands off (Scenario 5's Section 1.5 payload, structured per `Agent_Build_Specification.md` §2). Then:
```bash
pytest tests/test_grey_zone_pipeline.py -k vikram_mehta
```
This calls the Judge + decision table directly — no orchestrator, no Temporal, no WhatsApp — and asserts the output matches the expected ruling/verdict. **This is where you prove the agent's judgment is right**, and it's the fastest possible loop: seconds per run, no external dependencies. All five scenarios above should exist as fixtures here before anything else is built.

### Level 2 — Full journey test, mocked vendors, still no real WhatsApp/phone needed
Once N6/N7 (orchestration + wiring, per GRAPH.md) exist: run Temporal locally (`temporal server start-dev`, free, no account), with N2's gateway returning the mocked responses from `Agent_Build_Specification.md` §4 instead of real vendor calls. You trigger the workflow with a **test harness script**, not your actual phone:
```bash
python -m scripts.run_test_journey --scenario suresh_kumar
python -m scripts.run_test_journey --scenario ramesh_iyer
python -m scripts.run_test_journey --scenario vikram_mehta
```
Each `--scenario` flag maps to a pre-built mock configuration that makes the gateway return exactly the mock payloads shown above for that persona, deterministically, every run. **This is how you test the full N0–N18 orchestration** — routing, pausing, resuming — without needing a live phone number or WhatsApp account at all. This is the right level to prove "does GREY-ZONE actually route to N15, does N15 actually pause correctly waiting on a document upload, does it actually resume."

### Level 3 — Real WhatsApp, your actual phone number, vendor sandboxes
Only once WABA access and vendor sandbox credentials exist (the lead-time items from two turns ago). Meta's WhatsApp test-number sandbox lets you message a real test business number from your own phone; most KYC vendors provide fixed sandbox test-PAN/test-Aadhaar values that reliably return a canned valid/invalid response, so you can still reproduce a specific scenario on demand — e.g., a vendor's documented "always returns invalid" test PAN reproduces something like Scenario 2's identity failure, without needing a real flagged individual. **This is genuinely the last level, not the first** — everything about whether the agent's judgment and the orchestration are correct should already be proven at Levels 1 and 2 before a real phone number is ever involved.

---

## 3. Where do real API endpoint details go?

Not into this chat as secrets, and not required here at all unless you want me to fold a specific vendor's contract into the spec docs. Two options, pick whichever fits a given case:

- **If you want me to update `Agent_Build_Specification.md`'s internal contracts to match a real vendor** (e.g. you've picked HyperVerge for liveness and have their API doc): paste the relevant doc content here in chat, the same way you shared the Notion page — I'll fold it into the adapter mapping.
- **If you want Claude Code to build the real vendor adapter directly**: drop the vendor's API doc (PDF, OpenAPI spec, Postman collection) into a `/docs/vendor-apis/` folder in the VS Code project. Claude Code reads it when building that specific adapter behind the internal contract — no need to also paste it here.

**Never paste an actual API key or secret in either place.** Those go into environment variables (a local `.env`, gitignored) or a secrets manager, referenced by name in code — this should be true even in the earliest mocked-vendor dev stage, so the habit is already correct by the time real credentials exist.
