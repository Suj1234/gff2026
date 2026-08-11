# GRAPH.md — Build Plan (Node Graph)

Status values: `not_started | in_progress | blocked | done`. Update this file as work happens — Claude Code should read current status before proposing what to do next, and update it after finishing a node.

## Node Graph

| Node | Name | Depends on | Status | Spec reference |
|---|---|---|---|---|
| N0 | Repo & project scaffolding | — | not_started | this file |
| N1 | Shared schemas (EvidenceBundle, AgentResult, tool I/O) | N0 | not_started | Agent Build Spec §2, §3, §5 |
| N2 | Internal API gateway, mocked vendor adapters | N1 | not_started | Agent Build Spec §4 |
| N3 | Deterministic Rule Engine (BRE) — R-001–R-017 + ambiguous-flag identification | N1, N2 | not_started | Tech Plan §4, Agent Build Spec §2 |
| N4 | Deterministic evidence-gathering actions (4 actions) against mocked ABHA/rPPG/doc-request | N2 | not_started | Agent Build Spec §5 |
| N5 | `GreyZoneJudge` DSPy signature + decision table + grounding gate (staged pipeline, NOT `dspy.ReAct`) | N4 | not_started | Agent Build Spec §6 |
| N6 | Orchestration skeleton — Temporal workflow + LangGraph subgraph (N0–N18 shape) | N1 | not_started | Tech Plan §3 |
| N7 | Wire N3 (BRE) + N5 (staged pipeline) + N6 (orchestrator) into one runnable pipeline | N3, N5, N6 | not_started | PRD §4 (full journey) |
| N8 | Classical ML shadow scoring (XGBoost + SHAP, isolation forest, graph score) | N1, N2 | not_started | Tech Plan §5 |
| N9 | Human review dashboard + immutable audit store | N1 | not_started | Tech Plan §9, Agent Build Spec §3 |
| N10 | WhatsApp channel adapter (Flows, templates) — stubbed until WABA is live | N6 | not_started | PRD §8, Agent Build Spec §5 templates |
| N11 | End-to-end integration test using the worked example | N7, N8, N9 | not_started | Agent Build Spec §7 |
| N12 | Eval/regression harness — labeled grey-zone case set, replayed on every change | N1 | not_started | Agent Build Spec §10, CLAUDE.md "Eval discipline" |

**Suggested build order for a solo PM+Claude Code workflow:** N0 → N1 → N2 → (N3 and N4 in parallel, both only depend on N2) → N5 → N6 → N7 → N11 with mocks, then layer in N8, N9, N10 before touching any real vendor/WABA integration.

## Definition of done, per node

- **N0:** repo initialized, Python project structure, linting/formatting configured, `/docs` populated with the three spec files, `CLAUDE.md` and this file in the repo root, empty test scaffold in place.
- **N1:** Pydantic models for `EvidenceBundle`, `AgentResult`, and every tool's request/response, generated to match Agent Build Spec §2/§3/§5 exactly — field for field, including nullable/optional handling. Unit tests validate the worked example in Agent Build Spec §7 parses cleanly against these models.
- **N2:** one internal endpoint per row in Agent Build Spec §4, each backed by a mock adapter returning example payloads from the spec (and at least one edge-case payload per endpoint — e.g. the mobile→PAN "no match" case). No real vendor calls yet.
- **N3:** every rule R-001–R-017 implemented as an independently testable function/module, each threshold marked `# TODO(underwriting-manual)`, unit tests covering both the "fires" and "doesn't fire" case for each rule.
- **N4:** all 4 deterministic evidence-gathering actions callable with the exact signatures in Agent Build Spec §5, backed by N2's mocks, each returning the documented response shapes (including declined/timeout/unavailable cases). These are plain functions the decision table calls — not agent tools.
- **N5:** `GreyZoneJudge` signature (Agent Build Spec §6.1) runnable against a hand-built `EvidenceBundle` + `ambiguous_flags`, the deterministic decision table (§5) correctly routing to finalize/gather-once/escalate, the single re-Judge cycle firing at most once, and the grounding gate (§6.4) rejecting any ungrounded citation — proven against at least the worked example in §7 before moving on.
- **N6:** Temporal workflow shell with N0–N18 as named steps (most still stubs), LangGraph subgraph wired for the N14 routing decision and N15's agent-loop shape, `interrupt()` proven to work with a trivial pause/resume test.
- **N7:** the worked example in Agent Build Spec §7 runs start to finish through the real (mocked) pipeline — evidence bundle in, BRE routes to GREY-ZONE, agent runs its actual loop against N4's mocked tools, `AgentResult` comes out matching §7.4.
- **N8:** models trained/stubbed and callable; SHAP output present on every score; running in shadow mode against N7's pipeline without influencing the routing decision yet.
- **N9:** every case reaching human review (however triggered) is visible in a dashboard with the full evidence bundle and, where present, the full `tool_call_trace`; every case of every kind writes an audit record per the schema in Tech Plan §9 / Agent Build Spec §3.
- **N10:** WhatsApp Flow definitions exist for every customer-facing capture step and every agent-tool-triggered template in Agent Build Spec §5, testable against the Meta sandbox once WABA access exists (see the signup checklist Sujeet has separately).
- **N11:** the full worked example passes end to end with real (not hand-built) evidence flowing from N2's mocks through N3/N5/N6/N8/N9, matching Agent Build Spec §7's expected output.
- **N12:** at least a handful of labeled `EvidenceBundle` + expected-ruling pairs exist and are automatically replayed (with LLM caching on) as part of the test suite; a broken BRE/decision-table/prompt change fails this suite before it fails in production. Grows continuously — every production case that gets mishandled is added here.
