# CLAUDE.md — Onboarding Risk Assessment Agent

## Project
WhatsApp-native onboarding + underwriting risk assessment for individual retail health insurance (India). Deterministic BRE + classical ML resolve the large majority of proposals; a staged pipeline — one narrow `GreyZoneJudge` DSPy call plus a deterministic decision table, never an open agent loop — resolves the residual grey-zone cases only. See `/docs` for full spec — this file is a pointer + hard constraints, not a copy of the spec. Don't duplicate spec content here; update the docs in `/docs` instead and keep this file thin.

## Read these, in this order, every session
1. `/docs/PRD_Onboarding_Risk_Assessment_Agent.md` — the customer journey (N0–N18), personas (salaried/self-employed), why each step exists, Section 3 defines the Agent's role at the product level.
2. `/docs/Technical_Implementation_Plan.md` — architecture, the BRE rule table (R-001–R-017), classical ML layer, tech stack, build workstreams.
3. `/docs/Agent_Build_Specification.md` — the literal contract: JSON schemas, API request/response payloads, the staged pipeline (§6), the actual Judge instruction text, one fully worked example. **This is the file to implement against directly** — if code and this file disagree, this file wins unless a docs update says otherwise.
4. `/docs/India_Health_Insurance_Data_Sources.md` — the real, named 43-source list (not the generic placeholder categories in Agent_Build_Specification.md §4), with exact provider, format, consent basis, and researched population coverage per source, plus 25 worked scenarios showing where the agent's judgment is actually needed. **Known open item:** Agent_Build_Specification.md §4 still has the older, generic 17-row placeholder table — this file is the more accurate, current one for real data-source decisions; the two haven't been merged yet.
5. `/docs/Test_Scenarios_and_Testing_Guide.md` — five named end-to-end scenarios (Suresh/Ramesh/Priya/Anjali/Vikram) showing exactly what's deterministic vs. agent at each step, plus the three real levels of testing (isolated fixture tests, mocked full-journey tests, real WhatsApp sandbox) with literal commands.
6. `GRAPH.md` — current build plan, node status, what to work on next.

## Non-negotiable constraints — do not violate these while generating code
- **The agent is a staged pipeline, not an open agent loop.** Do not implement it as `dspy.ReAct` with a freely-chosen toolset. It is: deterministic flag identification (BRE) → one `GreyZoneJudge` DSPy call ruling on all flagged items → a deterministic decision table (plain code, not a model) → at most ONE bounded evidence-gathering + re-Judge cycle → a deterministic grounding gate. This is matched to a proven production pattern — see `Agent_Build_Specification.md` §0 and §10 before changing this shape.
- Evidence-gathering actions (`request_abha_consent`, `trigger_rppg_scan`, `request_additional_document`, `request_identity_reverification`) are invoked by the **deterministic decision table**, never chosen by the LLM itself. Do not add a new action without updating `Agent_Build_Specification.md` Section 5 first.
- The agent must never touch AML/PEP/sanctions determination, the STP age/SI hard gate, identity-fraud (liveness/deepfake) decisions, or pricing/sum-insured logic. These stay in the deterministic BRE. There is no action in the decision table that reaches them — keep it that way structurally, not just by prompt instruction.
- The Judge never emits the final verdict directly — `final_verdict` is always produced by deterministic code (the grounding gate) reading the Judge's `rulings`. Every `cited_evidence` entry in every ruling must pass the grounding check: a real path into the `EvidenceBundle`/`follow_up_observations` that was actually sent. No exceptions, no "close enough."
- **DSPy call-history retention must be explicitly disabled in the prod path** (default keeps ~10,000 calls in memory — a known cause of OOM in production). Keep it enabled only in the eval/regression harness. Turn LLM response caching OFF in prod, ON in the eval harness.
- Don't default to the largest/most expensive model for the Judge. Test a small, cheap model with a labeled regression set before reaching for a bigger one — cost per case matters at scale.
- Underwriting thresholds (income multiples, BMI/age/occupation loading bands, the confidence cutoff) are **not real numbers yet** — they're illustrative placeholders in the spec pending the actual underwriting manual. Mark every one with `# TODO(underwriting-manual): placeholder value, needs real threshold` in code. Never silently treat a placeholder as final.
- Consent-gated tool calls (ABHA, rPPG, Account Aggregator, additional documents) must go through the actual consent-request/response flow in code, even against mocked vendors in dev. Mock the *response*, never skip the *step* — this is a compliance behavior we want to be true in dev, staging, and prod alike.
- All external data sources are built against the internal gateway contract in `Agent_Build_Specification.md` Section 4. Business logic (BRE, ML, agent) only ever sees the internal contract shape — a real vendor's raw response gets mapped to that shape in an adapter, never consumed directly.
- Vendor selection per API is an open business decision (see Agent Build Spec Section 8) — build against mocked vendor adapters that already conform to the internal contract so vendor choice is a swap-in later, not a rewrite.

## Tech stack (current decision — flag if this should change)
- **Language:** Python throughout for v1 (agent, orchestrator, gateway), since DSPy is Python-only and this avoids a polyglot split for the first build.
- **Orchestration:** Temporal (Python SDK) as the durable outer workflow; LangGraph (Python) as the agent-routing subgraph for N14/N15.
- **Agent:** `dspy.ChainOfThought(GreyZoneJudge)` — a single narrow signature, not `dspy.ReAct`. Compiled offline with `MIPROv2` or `GEPA` (A/B both) once a labeled regression set exists; runs uncompiled/base-prompt in early dev. See `Agent_Build_Specification.md` §6 for the full pipeline this sits inside.
- **Runtime validation:** Pydantic models generated from the JSON Schemas in `Agent_Build_Specification.md` Sections 2–3.
- **Rule engine:** implement R-001–R-017 as an explicit, independently testable module — not inline in orchestration code.
- **Channel:** WhatsApp Cloud API + Flows — stub this behind an interface until a BSP/WABA is set up (see GRAPH.md Node 0 and the signup checklist).

## Current build status
See `GRAPH.md` for the live node graph and what's actively being worked on. Update `GRAPH.md`'s status column as nodes complete — don't let it drift from what's actually in the repo.

## Open decisions still pending — flag and stub, don't invent an answer
- Real vendor selection per API inventory row.
- Actual underwriting manual thresholds.
- WhatsApp BSP selection.
- LLM provider/model for the Judge calls, and whether to pin DSPy to `3.3.0b1` for parity with the reference production system or track latest stable.

## Eval discipline — build this alongside the code, not after
Maintain a growing set of underwriter-labeled grey-zone `EvidenceBundle` + expected-ruling pairs (the project's equivalent of the reference system's "claim master" set). Every code change to the BRE flag-identification logic, the decision table, or the Judge prompt gets replayed against the full set before merging. Use cached LLM responses for this by default; only force fresh calls when specifically testing prompt changes. Every time a real production case is mishandled, add it to this set immediately — don't just patch the code.
