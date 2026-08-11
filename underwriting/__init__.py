"""Onboarding Risk Assessment System — underwriting judgment layer.

One API in → one detailed report out. Deterministic rules do the bulk; a narrow
LLM (Phase 3) resolves only the grey-zone residue.

See IMPLEMENTATION_PLAN.md — the operative build contract. Phase 1 ships the
deterministic rule engine (schemas, config, rules, decision) with NO AI.
"""

__version__ = "1.0.0-phase1"
