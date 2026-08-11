"""journey/ — the Phase-C onboarding journey: DB, auth, vendor wiring, and the
server-rendered UI that COLLECTS the facts bundle and calls the underwriting engine
(`underwriting/`) ONCE.

Boundary: the engine stays stateless + DB-free (CLAUDE.md). This package is the
stateful layer AROUND it — it persists journey state + a full tracking trail and
wraps `underwriting.pipeline.run` / `report.build_report`. Nothing here changes
engine logic; the engine's 222 tests are unaffected.
"""
