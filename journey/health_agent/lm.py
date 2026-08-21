"""lm.py — thin re-export of `underwriting.judge`'s LM configuration (HEALTH_AGENT_PLAN.md
§1). Do NOT fork this: one `.env` read, one prod/eval caching discipline, one place that
decides whether an LLM is configured, for the WHOLE system (grey-zone judge AND the
health-triage agent both go through it). Importing `underwriting.judge` here (not the
reverse) keeps the layering honest — journey code depends on underwriting code, never
the other way around.
"""

from __future__ import annotations

from underwriting.judge import (  # noqa: F401 — re-exported, not used directly here
    configure_lm,
    has_api_key,
    live_enabled,
)
