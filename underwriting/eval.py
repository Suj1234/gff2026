"""eval.py — the labeled regression harness (Phase 5/6, §10 Phase 6, §11).

The fixtures under tests/fixtures/ are the project's "claim master": each carries
an `expected` block (Office-Hours D1 shape — `decision`, `expected_bre_outcome`,
`expected_flag_types`, `expected_rulings` with `must_cite`). This module replays
every fixture through the real pipeline and scores the run against those labels,
so a bad rule / prompt / model change fails HERE before prod (§10 Phase 6 done-when).

The accuracy triad we track (Office-Hours D1) — the three ways this system can be
wrong, each with a different cost:

  - false_benign        : expected a non-clear outcome (REFER/DECLINE/STEP_UP/loading)
                          but the system ISSUEd. The DANGEROUS miss — a risk let through.
  - over_escalation     : expected ISSUE but the system REFER/STEP_UP'd. Friction + cost,
                          not danger, but erodes STP and annoys good customers.
  - grounding_hallucination : a judge ruling cited evidence that does not resolve against
                          the bundle. The grounding gate should catch every one → 0 is the
                          only acceptable count (a non-zero means a hallucinated citation
                          reached a decision ungated).

Reproducibility (§11): grey-zone fixtures need the LLM. Two replay modes:
  - offline (default) : an injected deterministic judge (the same fake the tests use)
                        so `replay()` is network-free and CI-safe.
  - live/cached       : `UW_EVAL_MODE=1` turns LLM response caching ON (judge.py) so a
                        real replay is reproducible — the same proposal returns the same
                        cached ruling until the prompt/model version changes.

This is a plain module (no framework) driven by test_eval.py; run `python -m
underwriting.eval` for a human-readable scoreboard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import pipeline
from .decision import grounding_ok
from .schemas import ProposalInput

FIXTURES = Path(__file__).parent / "tests" / "fixtures"

# Outcomes that mean "cleared / issued" for triad purposes. ISSUE_WITH_LOADING is
# still a clear (the customer is on-boarded) but at a price — it is NOT a false
# benign when a loading was expected, so we treat expected-loading separately.
_ISSUED = {"ISSUE", "ISSUE_WITH_LOADING"}
_NON_CLEAR = {"REFER", "DECLINE", "STEP_UP", "POSTPONE"}


@dataclass
class CaseResult:
    name: str
    expected: str
    actual: str
    passed: bool
    false_benign: bool = False
    over_escalation: bool = False
    grounding_hallucination: bool = False
    note: str = ""
    # Beyond the final verdict (Office-Hours D1): a case only truly passes if it got
    # the right ANSWER for the right REASON. A rule/prompt change that lands the same
    # verdict via the wrong BRE outcome, wrong flags, or wrong rulings is a regression
    # a verdict-only check would miss — these carry those finer labels.
    bre_ok: bool = True       # BRE outcome matched expected_bre_outcome (if labeled)
    flags_ok: bool = True     # ambiguous flag_types matched expected_flag_types (if labeled)
    rulings_ok: bool = True   # per-flag rulings + must_cite matched expected_rulings (if labeled)
    mismatches: list[str] = field(default_factory=list)  # human notes on what diverged


@dataclass
class EvalReport:
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def false_benign(self) -> int:
        return sum(1 for c in self.cases if c.false_benign)

    @property
    def over_escalation(self) -> int:
        return sum(1 for c in self.cases if c.over_escalation)

    @property
    def grounding_hallucination(self) -> int:
        return sum(1 for c in self.cases if c.grounding_hallucination)

    @property
    def clean(self) -> bool:
        """The regression gate: all labels matched AND zero hallucinated citations
        reached a decision. false_benign is a hard fail; over_escalation shows up as
        a label mismatch (a case that should ISSUE didn't) so it is caught too."""
        return self.passed == self.total and self.grounding_hallucination == 0


def load_fixtures() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) | {"_name": p.stem}
            for p in sorted(FIXTURES.glob("*.json"))]


def _expected_terminal(expected: dict) -> str:
    """The Core-6 verdict the pipeline should actually reach. Some grey-zone fixtures
    resolve through a gather cycle (anjali: STEP_UP → ISSUE); the terminal the *full*
    pipeline lands on is `decision_with_llm_after_gather` when present, else `decision`."""
    return expected.get("decision_with_llm_after_gather") or expected["decision"]


def evaluate_case(
    fixture: dict,
    run: Callable[[ProposalInput], "pipeline.PipelineResult"],
) -> CaseResult:
    """Replay ONE fixture and score it against its expected label + the triad."""
    name = fixture.get("_name", "?")
    expected = fixture["expected"]
    want = _expected_terminal(expected)

    inp = ProposalInput(**fixture["input"])
    res = run(inp)
    got = res.decision.verdict
    mismatches: list[str] = []

    # Grounding-hallucination: any ruling that cited evidence which does not resolve
    # against the real bundle. The gate should have caught it (→ grounding_check_failed);
    # a True here on a decision that ISN'T grounding_check_failed means one slipped through.
    root = {**inp.model_dump(), "follow_up_observations": {
        **inp.model_dump().get("follow_up_observations", {}), **(res.follow_up or {})}}
    grounded = grounding_ok(res.rulings, root) if res.rulings else True
    hallucinated = (not grounded) and res.decision.escalation_reason != "grounding_check_failed"
    if hallucinated:
        mismatches.append("grounding: a cited path did not resolve and was not gated")

    # --- Right answer for the right REASON: check the finer labels when present. ---
    # BRE outcome. NOTE: a grey-zone case that RESOLVES through the LLM (anjali) keeps
    # its BRE outcome GREY-ZONE — the BRE label describes the deterministic triage, not
    # the post-LLM terminal. So compare the pre-LLM BRE outcome the fixture recorded.
    bre_ok = True
    if "expected_bre_outcome" in expected:
        bre_ok = res.bre.outcome == expected["expected_bre_outcome"]
        if not bre_ok:
            mismatches.append(f"bre_outcome: got {res.bre.outcome}, want {expected['expected_bre_outcome']}")

    # Ambiguous flag types raised by the BRE (order-insensitive subset check: every
    # expected flag type must have been raised; extras are allowed — the fixture labels
    # the flags that MUST appear, not an exhaustive set).
    flags_ok = True
    if "expected_flag_types" in expected:
        raised = {f.flag_type for f in res.bre.ambiguous_flags}
        missing = set(expected["expected_flag_types"]) - raised
        flags_ok = not missing
        if missing:
            mismatches.append(f"flags: expected {sorted(missing)} not raised (got {sorted(raised)})")

    # Per-flag rulings + must_cite. Match each labeled ruling by flag_type (the fixtures
    # label by type, not id). must_cite=True → that ruling must carry a cited path.
    #
    # `expected_rulings` labels the CYCLE-1 triage ruling — the one that decides the next
    # step (e.g. anjali: thin_file → needs_income_corroboration → gather). But `res.rulings`
    # holds only the LAST cycle's rulings; on a two-cycle case that resolved, the terminal
    # ruling is benign_explained (post-gather), not the cycle-1 label. So enforce
    # expected_rulings only when the case did NOT go through a gather cycle (judge_cycles<2);
    # a resolved two-cycle case is validated by its terminal verdict + the gather having
    # happened (asserted separately in the grounding tests). Recording cycle-1 rulings for
    # replay would need the pipeline to retain both cycles — tracked as a Later item (§13.5).
    rulings_ok = True
    resolved_two_cycle = getattr(res, "judge_cycles", 0) >= 2
    if expected.get("expected_rulings") and not resolved_two_cycle:
        by_type = _rulings_by_flag_type(res)
        for want_r in expected["expected_rulings"]:
            ftype = want_r.get("flag_type")
            got_rs = by_type.get(ftype, [])
            if "ruling" in want_r and not any(r.ruling == want_r["ruling"] for r in got_rs):
                rulings_ok = False
                mismatches.append(
                    f"ruling[{ftype}]: got {[r.ruling for r in got_rs] or 'none'}, want {want_r['ruling']}")
            if want_r.get("must_cite") and not any(r.cited_evidence for r in got_rs):
                rulings_ok = False
                mismatches.append(f"ruling[{ftype}]: must_cite but no cited evidence")

    false_benign = want in _NON_CLEAR and got in _ISSUED
    over_escalation = want == "ISSUE" and got in _NON_CLEAR
    passed = (got == want) and bre_ok and flags_ok and rulings_ok and not hallucinated

    return CaseResult(
        name=name, expected=want, actual=got, passed=passed,
        false_benign=false_benign, over_escalation=over_escalation,
        grounding_hallucination=hallucinated,
        note=res.decision.escalation_reason or "",
        bre_ok=bre_ok, flags_ok=flags_ok, rulings_ok=rulings_ok, mismatches=mismatches,
    )


def _rulings_by_flag_type(res) -> dict[str, list]:
    """Group the pipeline's judge rulings by the flag_type they ruled on. Rulings carry
    a flag_id; map it back to the flag_type via the BRE's ambiguous_flags."""
    id_to_type = {f.flag_id: f.flag_type for f in res.bre.ambiguous_flags}
    out: dict[str, list] = {}
    for r in res.rulings:
        out.setdefault(id_to_type.get(r.flag_id, "?"), []).append(r)
    return out


def replay(run: Optional[Callable] = None, fixtures: Optional[list[dict]] = None) -> EvalReport:
    """Replay the whole labeled set. `run` defaults to the real pipeline; tests inject
    an offline deterministic judge so CI needs no network. Returns an EvalReport."""
    run = run or pipeline.run
    fixtures = fixtures if fixtures is not None else load_fixtures()
    return EvalReport(cases=[evaluate_case(f, run) for f in fixtures])


def _scoreboard(report: EvalReport) -> str:
    lines = [
        f"{'CASE':<26} {'EXPECTED':<10} {'ACTUAL':<10} {'':<4}",
        "-" * 56,
    ]
    for c in report.cases:
        mark = "ok" if c.passed else "XX"
        tags = " ".join(t for t, on in (
            ("FALSE-BENIGN", c.false_benign),
            ("OVER-ESC", c.over_escalation),
            ("HALLUCINATION", c.grounding_hallucination),
            ("BRE", not c.bre_ok),
            ("FLAGS", not c.flags_ok),
            ("RULINGS", not c.rulings_ok),
        ) if on)
        lines.append(f"{c.name:<26} {c.expected:<10} {c.actual:<10} {mark:<4} {tags}")
        for m in c.mismatches:
            lines.append(f"{'':<26}   -> {m}")  # ASCII only: cp1252 stdout on Windows
    lines += [
        "-" * 56,
        f"passed {report.passed}/{report.total}   "
        f"false_benign={report.false_benign}   "
        f"over_escalation={report.over_escalation}   "
        f"grounding_hallucination={report.grounding_hallucination}   "
        f"{'CLEAN' if report.clean else 'REGRESSION'}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":  # python -m underwriting.eval → real replay (needs a key)
    import sys
    rep = replay()
    print(_scoreboard(rep))
    sys.exit(0 if rep.clean else 1)
