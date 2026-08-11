"""bank_statement.py — iAdore (Perfios) bank-statement adapter.

Two internal shapes come out of ONE iAdore JSON report (docs/vendor_apis.md §4):

  1. `account_aggregator` — the source key the BRE already reads for R-007 (income
     multiple) and R-008 (thin-file / fallback). A PDF bank statement REPLACES the
     Account Aggregator pull as the income-corroboration source (JOURNEY_PLAN.md §3),
     so the iAdore report maps into the SAME internal contract `income.py` produces.

  2. `follow_up_observations.bank_statement` — the shape the STEP_UP income re-judge
     reads: `{verified_annual_income, salary_credit_monthly, avg_monthly_balance,
     corroborates_declared_income}` (pipeline `_ACTION_TO_SOURCE`
     `request_additional_document(bank_statement)` → `bank_statement`).

The raw report comes from `bank_statement.analyze(pdf)` at the repo root (the shipped
iAdore client). This module is the ADAPTER: raw report → internal shape. Mock the
RESPONSE (a canned report dict in tests), never the step — the client's 3-call flow
(submit → poll → report) is real code, identical dev/staging/prod (§3, files/CLAUDE.md).

Boundary (§1.8): iAdore emits categorized FACTS (imputed income, salary credits, avg
balance) AND vendor verdicts (fraud/anomaly labels, `riskTriggers`, its own
`incomeVerified`/`match` booleans). The verdicts are DROPPED — WE decide corroboration
(the `corroborates_declared_income` we compute is our judgment from facts, keyed to the
declared income the re-judge is testing), not the vendor's.

iAdore report field names vary by report version; this adapter accepts the common
spellings (camelCase `analysis`/`summary` envelope, paise or rupee amounts) and is
tolerant of a partial/garbage report (§11) — a missing field degrades to absent, never
a crash.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import adapter

log = logging.getLogger("underwriting.sources.bank_statement")

# iAdore reports the basis it imputed income from (salary vs mixed vs statement-only);
# map to the internal `income_source` fact R-008 keys on (a statement-only basis lowers
# the no-income-proof auto-issue ceiling). Same vocabulary as income.py's AA basis.
_INCOME_BASIS = {
    "SALARY": "salary",
    "SALARY_CREDITS": "salary",
    "GST_ITR": "gst_itr",
    "BANK_STATEMENT_ONLY": "AA_fallback_only",
    "STATEMENT_ONLY": "AA_fallback_only",
    "AA_ONLY": "AA_fallback_only",
}


def _num(v) -> Optional[float]:
    """A numeric field → float, tolerant of a stringified number ("179000.50") and of a
    garbage value (returns None, never raises). Bools are NOT numbers (§11)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _rupees(amount, *, paise: bool) -> Optional[int]:
    """Money → rupees. iAdore reports may be in paise or already rupees depending on the
    field; the caller says which. None-safe."""
    n = _num(amount)
    if n is None:
        return None
    return int(round(n / 100)) if paise else int(round(n))


def _first(d: dict, *keys):
    """First present (non-None) value among `keys` in `d`. Lets one adapter absorb the
    several spellings iAdore uses across report versions."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


_ENVELOPE_KEYS = ("analysis", "summary", "report", "data")


def _analysis(raw: dict) -> Optional[dict]:
    """Unwrap the report envelope. iAdore nests the numbers under `analysis` /
    `summary` / `report` depending on version; fall back to the top level.

    Returns None (→ `unavailable`) for a malformed report: an envelope key present but
    not a usable dict (e.g. `{"analysis": "nope"}`) is not a valid report body."""
    for key in _ENVELOPE_KEYS:
        if key in raw:
            inner = raw.get(key)
            return inner if isinstance(inner, dict) and inner else None
    return raw  # no envelope → the numbers are at the top level


def _verified_annual_income(a: dict) -> Optional[int]:
    """Verified annual income in rupees. iAdore may report it directly (annual) or as a
    monthly figure to annualize; prefer an explicit annual field."""
    paise_annual = _first(a, "imputedAnnualIncomePaise", "verifiedAnnualIncomePaise")
    if paise_annual is not None:
        return _rupees(paise_annual, paise=True)
    rupee_annual = _first(
        a, "imputedAnnualIncome", "verifiedAnnualIncome", "annualIncome",
        "estimatedAnnualIncome", "imputed_annual_income",
    )
    if rupee_annual is not None:
        return _rupees(rupee_annual, paise=False)
    # Only a monthly figure? Annualize it (×12).
    monthly = _salary_credit_monthly(a)
    return int(round(monthly * 12)) if monthly is not None else None


def _salary_credit_monthly(a: dict) -> Optional[float]:
    """Average monthly salary credit in rupees."""
    paise = _first(a, "salaryCreditMonthlyPaise", "avgMonthlySalaryPaise")
    if paise is not None:
        return _rupees(paise, paise=True)
    rupee = _first(
        a, "salaryCreditMonthly", "avgMonthlySalary", "averageMonthlySalary",
        "monthlySalaryCredit", "salary_credit_monthly",
    )
    return _num(rupee)


def _avg_monthly_balance(a: dict) -> Optional[float]:
    paise = _first(a, "avgMonthlyBalancePaise", "averageMonthlyBalancePaise")
    if paise is not None:
        return _rupees(paise, paise=True)
    rupee = _first(
        a, "avgMonthlyBalance", "averageMonthlyBalance", "avgBalance",
        "avg_monthly_balance",
    )
    return _num(rupee)


def _income_source(a: dict) -> Optional[str]:
    basis = str(_first(a, "incomeBasis", "incomeSource", "income_source") or "").strip().upper()
    return _INCOME_BASIS.get(basis) or (a.get("income_source") if not basis else None)


def _warn_if_unmatched_schema(a: dict, verified, balance, salary) -> None:
    """E3 coverage-warning (no-silent-caps discipline, JOURNEY_PLAN.md §6/§8): a report
    body that PARSED (a real, non-trivial analysis dict) but from which NO income /
    balance / salary field matched any known spelling is the silent-miss signature — the
    schema is probably a version this adapter doesn't know. Surface it loudly rather than
    returning clean-looking all-None income. Until a real iAdore report is captured and
    the fixture pinned (deferred — JOURNEY_PLAN.md §later E2), this is the tripwire."""
    if verified is None and balance is None and salary is None and len(a) > 1:
        log.warning(
            "iAdore report parsed (%d fields: %s) but matched NO income/balance/salary "
            "field — unknown report schema? Income facts are absent, not clean. "
            "Capture a real report and pin the fixture (JOURNEY_PLAN §later E2).",
            len(a), sorted(a)[:12],
        )


def to_account_aggregator(raw: dict) -> dict:
    """iAdore report → internal `account_aggregator` shape (schemas.AccountAggregator).
    FACTS only — vendor verdict fields (riskTriggers, fraud/anomaly labels) are dropped."""
    if not isinstance(raw, dict) or not raw:
        return {"status": "unavailable"}
    a = _analysis(raw)
    if not isinstance(a, dict) or not a:
        return {"status": "unavailable"}
    verified = _verified_annual_income(a)
    balance = _avg_monthly_balance(a)
    salary = _salary_credit_monthly(a)
    _warn_if_unmatched_schema(a, verified, balance, salary)
    return {
        "status": "available",
        "name": _first(a, "accountHolderName", "accountHolder", "name"),
        "address": a.get("address"),
        "period": _first(a, "statementPeriod", "period"),
        "imputed_annual_income": verified,
        "avg_monthly_balance": balance,
        "expense_to_income": _num(_first(a, "expenseToIncomeRatio", "expense_to_income")),
        "income_source": _income_source(a),
        # Categorized transactions flow through as facts; verdicts stay out (§1.8).
        "credits": a.get("credits", []) if isinstance(a.get("credits"), list) else [],
        "debits": a.get("debits", []) if isinstance(a.get("debits"), list) else [],
    }


def to_follow_up_observation(raw: dict, declared_annual_income: Optional[int] = None) -> dict:
    """iAdore report → the `follow_up_observations.bank_statement` shape the STEP_UP
    income re-judge reads: {verified_annual_income, salary_credit_monthly,
    avg_monthly_balance, corroborates_declared_income}.

    `corroborates_declared_income` is OUR judgment (§1.8), not the vendor's `match`
    field: the verified income must reach a fraction of what the applicant declared.
    When the declared income isn't passed in (unknown at gather time), we leave it None
    rather than assert either way — the re-judge then reasons from the raw numbers.
    """
    if not isinstance(raw, dict) or not raw:
        return {"status": "unavailable"}
    a = _analysis(raw)
    if not isinstance(a, dict) or not a:
        return {"status": "unavailable"}
    verified = _verified_annual_income(a)
    salary = _salary_credit_monthly(a)
    balance = _avg_monthly_balance(a)
    _warn_if_unmatched_schema(a, verified, balance, salary)
    corroborates = None
    if verified is not None and declared_annual_income:
        # Corroborated if the statement backs at least CORROBORATION_FRACTION of the
        # declared income (a thin margin below covers seasonality / partial statements).
        corroborates = verified >= CORROBORATION_FRACTION * declared_annual_income
    return {
        "status": "available",
        "verified_annual_income": verified,
        "salary_credit_monthly": salary,
        "avg_monthly_balance": balance,
        "corroborates_declared_income": corroborates,
    }


# TODO(underwriting-manual): placeholder value, needs real threshold. The fraction of
# declared income a bank statement must verify to count as corroborating it.
CORROBORATION_FRACTION = 0.80


@adapter("account_aggregator_bank_statement")
def from_vendor(raw: dict) -> dict:
    """Registry entry point: iAdore report → the internal income-corroboration shape.

    Registered under a distinct key (`account_aggregator_bank_statement`) so it doesn't
    clobber the AA adapter (`income.py`, key `account_aggregator`) — a deployment picks
    ONE income source per bundle. `adapt_bundle` routes whichever key the raw ingestion
    layer uses; both land in the same internal `account_aggregator` contract shape.
    """
    return to_account_aggregator(raw)


# ===========================================================================
# E1 — the real iAdore EvidenceGatherer (the STEP_UP gather seam)
# ===========================================================================
# The pipeline's STEP_UP cycle calls an `EvidenceGatherer(proposal_id, actions, inp)`
# that returns {source: observation}. `_fixture_gather` (pipeline.py) returns a
# PRE-CANNED response for dev. This is the REAL one: for the bank-statement action it
# runs the shipped iAdore client (submit → poll → report) on the uploaded PDF, then
# adapts the report to the follow-up shape. Everything else falls back to the fixture
# gatherer, so a deployment can flip to real iAdore without changing the other sources.
#
# `analyze` is imported LAZILY inside the call so importing this module (and the whole
# `sources` package) never requires `requests` or the IADORE_* env — only invoking the
# real gather does. Mock the RESPONSE, never the step: tests inject a fake `analyze`.

# How the uploaded PDF is located in the bundle: a document of this type, carrying a path.
BANK_STATEMENT_DOC_TYPE = "bank_statement"
_ACTION = "request_additional_document(bank_statement)"


def _bank_statement_pdf_path(inp) -> Optional[str]:
    """The uploaded statement PDF path from `inp.documents` (a doc typed
    `bank_statement`, with a `path`/`file_path`/`url` field). None if not uploaded."""
    for doc in getattr(inp, "documents", None) or []:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("type") or "").strip().lower() == BANK_STATEMENT_DOC_TYPE:
            return doc.get("path") or doc.get("file_path") or doc.get("url")
    return None


def make_iadore_gatherer(analyze=None, fallback=None):
    """Build the real STEP_UP `EvidenceGatherer`. Inject `analyze` (defaults to the
    shipped repo-root `bank_statement.analyze`, imported lazily) and a `fallback`
    gatherer for non-bank-statement actions (defaults to the pipeline's fixture gather).

    Fail-safe (§11): if the applicant uploaded no PDF, or iAdore errors/times out, the
    bank_statement observation comes back `unavailable` (the re-judge reasons around it,
    fails safe to REFER) — never a crash, never a silently-clean income.
    """
    def gather(proposal_id: str, actions: list[str], inp) -> dict:
        # Lazy imports so this module stays import-clean without the vendor deps/env.
        if fallback is None:
            from ..pipeline import _fixture_gather as _fb
        else:
            _fb = fallback
        out: dict = {}
        for a in actions:
            if a != _ACTION:
                out.update(_fb(proposal_id, [a], inp))
                continue
            pdf = _bank_statement_pdf_path(inp)
            if not pdf:
                # No upload yet → nothing to analyze; surface as unavailable, not clean.
                out[BANK_STATEMENT_DOC_TYPE] = {
                    "status": "unavailable", "requested_action": a,
                    "reason": "no bank_statement document uploaded",
                }
                continue
            _analyze = analyze
            if _analyze is None:
                from bank_statement import analyze as _analyze  # repo-root client
            declared = None
            fin = getattr(inp.application, "financial", None)
            if fin is not None:
                declared = getattr(fin, "declared_annual_income", None)
            try:
                report = _analyze(pdf)
                out[BANK_STATEMENT_DOC_TYPE] = to_follow_up_observation(
                    report, declared_annual_income=declared)
            except Exception as exc:  # noqa: BLE001 — vendor/gateway failure → fail safe
                log.warning("iAdore analyze failed for %s (%s: %s) → bank_statement "
                            "unavailable, re-judge fails safe.", proposal_id,
                            type(exc).__name__, exc)
                out[BANK_STATEMENT_DOC_TYPE] = {
                    "status": "unavailable", "requested_action": a,
                    "reason": f"iadore_error:{type(exc).__name__}",
                }
        return out

    return gather
