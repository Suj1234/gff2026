# CLAUDE.md — Onboarding Risk Assessment System

**Start here, every session:** read `IMPLEMENTATION_PLAN.md` (the full build contract —
architecture, data sources, real rules, industry-standard config, scoring + Safety Score,
decision logic, the input/output JSON, and the phase plan) and `files/CLAUDE.md` (hard
constraints). **The plan is the operative document** — if code and the plan disagree, the plan
wins unless a plan update says otherwise.

**Before touching ANY UI / journey page:** read `DESIGN.md` (the design system — principles,
color, type, layout Shell A, components + state model, per-screen center rules, decision log)
and consume `design-tokens.css` (the single source of truth for color/type/spacing/radius/
elevation/motion). **`DESIGN.md` is operative for UI** — style through the semantic tokens
(`--surface`, `--brand`, `--ok`…), never hardcode a hex/font/spacing in a component; reuse the
documented components rather than reinventing them; design both light + dark via the token blocks.
Update `DESIGN.md`'s decision log whenever a design decision changes. See `JOURNEY_PLAN.md` for
the journey steps the UI collects.

## State (as of 2026-08-10)
Planning complete. **Phases 1 + 2 + 3 + 4 + 5 are built and green.** `pytest underwriting/tests/` = 181 passing,
3 skipped (the live-gateway tests — opt-in). **The 3 live tests pass against the real gpt-4o gateway**
(`UW_RUN_LIVE=1`): Vikram → REFER, Anjali → 2-cycle → ISSUE, both real LLM (~$0.006–0.018/case).
- **Phase 1 (deterministic rule engine, NO AI):** `config.py` (thresholds `# TODO(underwriting-manual)`-tagged),
  `schemas.py` (facts-only input + Appendix-A output), `rules.py` (real R-001–R-017 + **R-005b senior-medicals
  46–55 step-up** + R-010 crosswalk + consistency check), `decision.py` (Core 6 mapper §7 + `decide_next_step`),
  6 fixtures (incl. `priya_postpone` covering the POSTPONE Core-6 outcome). **Phase-1 audit (2026-08-10)**
  fixed: R-005b (§4A 46–55 medicals), R-008 now really applies `NO_INCOME_PROOF_SI_CEILING`, POSTPONE + R-011
  tests added. Remaining known gaps tracked in **IMPLEMENTATION_PLAN.md §13** (name matcher D-1,
  cluster-routing D-2/D-3, non-medical SI limits D-4).
- **Phase 2 (scoring & Safety Score, NO AI):** `scoring.py` — `risk_scores` (fraud/anomaly/graph
  as a **real explainable heuristic with per-feature attribution**, never random; uses upstream
  `signals.ml_scores` as base when present, `max(base, contrib)`) + `safety_score` (per-source
  sub-scores from 100 minus documented penalties, §4A weighted composite + bands). `pipeline.py`
  wires intake→rules→scoring→decision. `test_scoring.py` added. **Rohit → Safety Score 65.6 / High
  Risk** from the §4A weight table (sub-scores track Appendix A; Rohit fixture enriched with the
  canonical litigation + lifestyle-spend facts from Appendix B so the ~65 falls out of real data,
  not a hardcode). Penalty magnitudes are the only knobs, all `# TODO(underwriting-manual)`-tagged.
  Note: Safety band ≠ decision — DECLINE (fraud_deepfake) and REFER still come from the hard
  gates / grey-zone edge, independent of the score. **Post-Phase-2 review hardening:** risk-score
  attribution now RECONCILES with the score (`score_source` ∈ heuristic|upstream_model +
  `attribution_note`; heuristic-driven → `shap` sums to the score, upstream-driven → note says it
  corroborates but does NOT sum) — locked by `test_attribution_reconciles_with_score`; a real
  `test_safer_case_scores_higher` (clean > non-disclosure) now enforces score ordering; `_lab_severity`
  '>N floor' below → "low" (was mislabeled "high"). See **§ Phase 2 — Deferred** below for what's left.
- **Phase 3 (LLM judge + grey-zone pipeline):** `judge.py` — real `dspy.ChainOfThought(GreyZoneJudge)`
  (NOT ReAct) + `run_judge` + `extract_condition` (unstructured messy-ABHA → condition, R-010 free-text
  path — **wired into `rules.py` R-010**: `run_bre(inp, extractor=...)` feeds `unstructured_notes` through
  the LLM → `_label_to_condition` → same crosswalk). Prod discipline in `configure_lm`: DSPy call-history
  OFF + response caching OFF in prod, both ON in eval (`UW_EVAL_MODE=1`); 60s LM timeout so an unreachable
  gateway fails fast. `usage_since`/`model_name` stamp model + prompt version + **cost** (reliable) + tokens
  (best-effort) on `run_metadata`. `pipeline.py` runs the full flow: rules→scoring→**grey-zone? (re-run BRE
  with LLM extractor if free-text notes) → judge→decision table→ONE gather cycle→re-judge**→grounding
  gate→Core-6 mapper. Non-grey-zone cases never call the LLM. **Gather is real code with a mocked RESPONSE**
  (§7.1): `_fixture_gather` reads the canned vendor reply from the proposal's `follow_up_observations`
  (`_ACTION_TO_SOURCE` maps action→source); a real deployment swaps the gateway call behind the same
  `EvidenceGatherer` signature. **Grounding-gate fix** (`decision.py grounding_ok`): every `cited_evidence`
  path resolves against the real bundle on **ALL** rulings **including the escalate path** — fabricated
  citation → REFER `grounding_check_failed`; a `benign_explained` citing nothing is not trusted.
  **Calibrated confidence gate** (`confidence` + `CONFIDENCE_MIN`, deterministic on ruling decisiveness+
  grounding, NOT model self-report; recalibrate vs eval set): low → REFER `low_confidence`. Gate order in
  the mapper: grounding → row-10 explicit escalate → confidence → rows 8/9/7. `test_grounding.py`: offline
  fake-judge tests (both LLM entry points patched — genuinely no network) + **3 live tests** asserting the
  real done-when, gated by `UW_RUN_LIVE=1` + a key.
Fixtures land: suresh→ISSUE, fraud_deepfake→DECLINE (R-003), rohit→GREY-ZONE→REFER, **vikram→GREY-ZONE→
judge→REFER** (grounded, live-verified), **anjali→GREY-ZONE→judge (needs income)→gather bank_statement
→re-judge→ISSUE** (live-verified, 2 cycles; STEP_UP is the row-8 first-cycle next-step, ISSUE the resolved
outcome). Locked decisions:
- **Boundary — facts in, judgments out** (IMPLEMENTATION_PLAN.md §1.8): upstream delivers analyzed
  *facts* (no verdicts); we build the entire judgment layer. We do NOT build the analyzers
  (bank-statement engine, CV/BMI model, OCR).
- **Outcomes:** Core 6 — ISSUE · ISSUE_WITH_LOADING · STEP_UP · POSTPONE · REFER · DECLINE.
- Confirmed medical **non-disclosure → REFER** (not load).
- **Config:** industry-standard defaults in §4A (thresholds tagged `# TODO(underwriting-manual)`).
- **LLM:** `openai/gpt-4o` via company gateway — already in `.env` (`LLM_MODEL`, `LLM_BASE_URL`).
- **I-Adore report** (`docs/IAdore Sample Report.pdf`) = output **layout reference only**.
- **DECLINE only from deterministic hard gates**; LLM never sets pricing/loading, never touches
  AML/PEP/STP-gate/identity-fraud.

## Existing code
`agent.py` + `tests/` is the thin first slice (grey-zone judge only) — now superseded by the
`underwriting/` package (`judge.py` + `decision.py` + `pipeline.py`). Keep for reference; the package
layout in IMPLEMENTATION_PLAN.md §9 is the target structure.

**Phase 4 (report assembly + API + §11 robustness):** `report.py` — `build_report` assembles the
full Appendix-A `ReportOutput` from a `PipelineResult`: `report_meta` (from declared facts), `safety_score`
+ `scoring_breakdown`/`scoring_total`, `signals` (echoed input facts), I-Adore `sections` (per-source
**risk LEVEL derived** from each safety sub-score via `_level`/band cutoffs — the level is our judgment,
facts flow through; every scored group yields a section — one of **Low/Moderate/High**, never a crash on a
partial bundle), `risk_scores`, `bre_result`, `risk_and_fraud_verdict` (narrative from our flags+scores),
`decision`, `cited_evidence_chain` (flattened judge rulings), `run_metadata`, `audit_log`. **KNOWN
LIMITATION (deferred to Phase 5, scoring layer):** an *absent* source is scored clean (sub_score ~100 →
"Low") and its `findings` text can assert a state never observed (e.g. "labs in range" with zero labs) —
`scoring.py` does not yet distinguish absent-source from assessed-clean, so a "Low" section is NOT proof
the source was checked. `report._level` faithfully reflects whatever sub_score the scorer returned.
**Pure function** — no clock, no randomness; **audit timestamps derive from `meta.received_at`** (a bundle
FACT). §11 idempotency is on the **DECISION** (`test_idempotency_same_input_same_decision`); report assembly
itself is byte-pure on a fixed `PipelineResult` (`test_report_assembly_is_pure`), but the report still
carries LLM cost/token stamps in `run_metadata`, so two *real* LLM runs can differ in those fields without
the decision changing. `api.py` — `POST /underwrite` (FastAPI) → `{status, waiting_on?, report}`;
**STEP_UP → `status: "pending"`** with `waiting_on = decision.next_step` (§2 async note; durable Temporal
pause/resume is later). Body validated against `ProposalInput` at the trust boundary (invalid → 422);
partial bundles are the norm, reasoned around. `GET /health` for probes. `run_and_report(inp, gather)` is
the shared core (endpoint + tests one path). New deps: `fastapi`, `uvicorn`, `httpx` (added to
requirements). `test_pipeline.py` (17 tests) — **all 6 fixtures end-to-end (list auto-discovered from
`fixtures/`, not hardcoded)** → full report validating against `ReportOutput` (suresh→ISSUE, fraud→DECLINE,
rohit/vikram→REFER, anjali→2-cycle→ISSUE, **priya→POSTPONE**; grey-zone fixtures use an offline stateful
fake judge with an unknown-flag guard, no network), the real HTTP route via TestClient (envelope + 422),
+ robustness: decision-idempotency, assembly-purity, version-stamp presence (rules always; prompt/model
only when LLM ran), ordered append-only audit log, partial-data + missing-optional-fields no-crash,
pending/complete status.

**Phase 5 (eval harness + real-data readiness — §10 Phase 5/6, §11):** `eval.py` — `replay()` runs the
whole labeled fixture set (the "claim master") through the real `pipeline.run` and scores each case against
its `expected` block (Office-Hours D1 shape). Tracks the **accuracy triad**: **false_benign** (expected
non-clear but ISSUEd — the dangerous miss), **over_escalation** (expected ISSUE but REFER/STEP_UP —
friction), **grounding_hallucination** (a ruling cited evidence that didn't resolve AND wasn't caught by the
gate — should always be 0; the gate turns a fabricated citation into `grounding_check_failed`). **Not
verdict-only:** each case is also scored on `expected_bre_outcome`, `expected_flag_types`, and
`expected_rulings`/`must_cite` — so a rule/prompt change that lands the **right verdict for the wrong reason**
(wrong BRE outcome, wrong flags, or wrong ruling) still fails (`mismatches` names what diverged). One
worked-around blind spot: `expected_rulings` labels the CYCLE-1 triage ruling, but `PipelineResult` keeps
only the last cycle, so the ruling check is skipped on resolved two-cycle cases (`judge_cycles>=2`) — see
**§13.5 D-15**. `EvalReport.clean` is the regression gate (all labels matched + zero leaked hallucinations).
`python -m underwriting.eval` prints the scoreboard (with per-case mismatch reasons), exits non-zero on
regression. Reproducibility: default replay is **network-free** (inject a deterministic judge, as the tests
do); the real cached replay is opt-in via `UW_EVAL_MODE=1` (LLM caching ON, wired in `judge.py`) — **verified:
`UW_EVAL_MODE=1 python -m underwriting.eval` run twice against the real gpt-4o gateway → 6/6 CLEAN, triad all
zero, reproducible across runs.** `test_eval.py` (6 tests): baseline replay CLEAN across all 6 fixtures, a
**seeded bad-prompt** (clear-everything judge) caught as `false_benign`, a **seeded bad-rule** (clean→DECLINE
BRE) caught as a label flip, a **right-verdict-wrong-reason** change caught on the flag mismatch, and each
triad metric lights up on the right kind of wrong answer — **this is the §10 Phase-6 done-when: the suite
fails a bad rule/prompt change before prod.**
**Real vendor adapters (§3 adapter rule, §9):** new `sources/` package — `adapter(key)` registry + `adapt` /
`adapt_bundle` map a raw vendor response → the internal contract shape (the per-source dict under
`signals`). Two representative adapters ship: `identity.py` (PAN — Karza/Signzy-style `result` envelope,
camelCase, text `panStatus` → internal `pan_status`, **unknown status fails safe to `invalid`**) and
`income.py` (AA/BSA — Perfios/Finbox-style `analysis` envelope, paise→rupees, `incomeBasis`→`income_source`,
**drops vendor verdict fields per §1.8**). Vendor choice = re-register under the same key, no downstream
change (§12 open decision #6). The adapter layer is **opt-in at the raw-ingestion seam** — the pipeline and
fixtures (authored directly in the internal shape) never route through it, so **all mocks pass unchanged**
(`test_sources.py`, 6 tests; `pipeline.py` takes no dependency on `sources/`). Adapters verified robust to
empty/None/garbage raw input (no crash) and fail-safe on unknown vendor status. Only PAN + AA ship (§13.5
D-17: the rest are un-adapted until a vendor is chosen — un-adapted sources pass through `adapt` unchanged).
`CONFIDENCE_MIN` calibration stays a `# TODO(underwriting-manual)` deferral against the *labeled* eval set
(needs real underwriter labels, not the 6-fixture seed) — see IMPLEMENTATION_PLAN.md §13.
**Phase-5 deferred ledger — IMPLEMENTATION_PLAN.md §13.5** (What/Why/Impact/How/Trigger, same as §13.1–13.4):
**D-15** cycle-1 ruling not verifiable on resolved two-cycle cases (needs per-cycle rulings on `PipelineResult`,
fold with D-14); **D-16** eval is binary pass/fail — no triad-rate thresholds or run-over-run trend yet
(calibrate with the grown set); **D-17** only PAN+AA adapters shipped (rest gated on vendor selection);
**D-18** ML scorer still the heuristic, no trained model in shadow (needs a labeled training set). All gate on
Phase-6 label growth.
**Deferred to "Later" (post-v1) — IMPLEMENTATION_PLAN.md §13.6:** **L-1** durable async pause/resume (Temporal)
across consent/upload waits — the `EvidenceGatherer` seam in `pipeline.py` is the exact injection point (swap
the fixture gatherer for a signal-awaiting activity); STEP_UP already surfaces as `status: "pending"` +
`waiting_on` synchronously (Phase 4), the durable resume-on-upload is Later. **L-2** WhatsApp Flows channel +
human-review dashboard — both consume `POST /underwrite`'s output, no engine change.

## Next: Phase 6 — grow the labeled set + shadow ML
Phase 5 shipped the harness that Phase 6 grows into. Remaining per IMPLEMENTATION_PLAN.md §10 Phase 6:
1. **Grow the claim-master set** — add real underwriter-labeled grey-zone cases beyond the 6 seed fixtures;
   add every mishandled prod case immediately (files/CLAUDE.md eval-discipline). The harness (`eval.replay`)
   already consumes any new `fixtures/*.json` with an `expected` block — no code change to add a case.
2. **Calibrate the gates against real labels** — `CONFIDENCE_MIN` (§13) + the §4A safety weights/knobs
   (Phase-2 D-5/D-6); replace the `=65` anchor assert with a ranking metric once labels exist.
3. **More real adapters + ML in shadow** — extend `sources/` to the remaining sources as vendors are picked;
   train the fraud/anomaly/graph models and run them in shadow, swapping the heuristic scorer (§5.1, D-6).
4. **Done when:** the grown suite fails a bad change before prod (mechanism proven in Phase 5), and real
   adapters pass the same fixtures.

## Phase 2 — Deferred / Pending (scoring & Safety Score)
Phase 2's own done-when is **met** (Rohit → 65.6 / High from the §4A weight table; every score
attributed; 168 tests green). Nothing here blocks Phases 3–5. Deferrals are tracked in
**IMPLEMENTATION_PLAN.md §13.2** (same ledger as the Phase-1 gaps §13.1), full What/Why/Impact/How/Trigger:
- **D-5** — the Rohit ~65 is a *calibration anchor*, not validated ground truth; re-fit knobs + §4A
  weights against labeled outcomes in Phase 6 and replace the `=65` assert with a ranking metric.
- **D-6** — `risk_scores.shap` is a documented heuristic stand-in (honest via `score_source`/
  `attribution_note`); swap for real SHAP when the shadow models train (§5.1).
- **D-7** — `safety_score` recomputes `risk_scores()` ~3× per call (`ponytail:`-tagged); hoist to one
  call if scoring ever shows up hot. Deterministic → safe, pure waste.
- **D-8** — a few sub-scorer penalty branches (geography hotspot, declared-tobacco, hazardous-class,
  velocity-anomaly) have no fixture exercising them; add targeted cases when widening coverage.
D-5/D-6 gate on the Phase-6 labeled eval set — **do NOT touch the penalty knobs before then**; D-7/D-8
are optional cleanups with no current output change.
