"""health_agent/ — the conversational health-triage agent (HEALTH_AGENT_PLAN.md).

Journey-time-only: sits OUTSIDE `underwriting/` (confirmed placement, HEALTH_AGENT_PLAN.md
§1) because its job is to decide what to ASK the applicant, not to judge or decide
anything. Its entire output lands as richer FACTS (`HealthDeclaration.condition_detail`,
`Signals.health_agent_transcript`) — never a verdict, never a bypass of R-009/R-010/R-017.

Given whatever face-scan (NuralX), ABHA, and prescription-OCR facts exist for an
applicant:
  1. `config.py`   — the ONLY tunable knobs: the fixed condition-bucket catalog + the
                      clinical information targets per condition (facts underwriting
                      needs; NOT a fixed question script — see config.py's own docstring).
  2. `signatures.py` — 4 narrow `dspy.Signature`s (triage, next-question, summarize),
                        reusing `underwriting.judge`'s DSPy conventions verbatim.
  3. `engine.py`   — the state machine: triage -> one adaptive conversation thread per
                      flagged condition -> a single bounded second pass for anything the
                      applicant volunteered along the way -> structured summaries.
  4. `lm.py`        — thin re-export of `underwriting.judge.configure_lm` (one LM config
                       path for the whole system, never forked).
"""
