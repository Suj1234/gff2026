"""income.py — Account Aggregator / BSA vendor adapter (internal key: `account_aggregator`).

Maps a real AA / bank-statement-analysis vendor response (shape modeled on Perfios /
Finbox / Anumati AA-consumer style: money in paise, an `analysis` envelope, an income
`basis` label) to the internal `AccountAggregator` contract (schemas.py) the BRE reads
for R-007 (income multiple) and R-008 (thin-file / AA-fallback).

Boundary (§1.8): the vendor delivers *categorized facts* — imputed income, credits,
debits. It does NOT deliver risk verdicts; `risk_triggers`/`lifestyle_spends.severity`
are OUR judgments and are intentionally dropped here so they cannot sneak in as input.
"""

from __future__ import annotations

from . import adapter

# The BSA engine reports the basis it imputed income from; map to the internal
# `income_source` fact R-008 keys on (AA_fallback_only lowers the auto-issue ceiling).
_INCOME_BASIS = {
    "GST_ITR": "gst_itr",
    "SALARY_CREDITS": "salary",
    "AA_ONLY": "AA_fallback_only",
    "BANK_STATEMENT_ONLY": "AA_fallback_only",
}


def _rupees(paise) -> int | None:
    """Vendors often report money in paise; the internal contract is rupees."""
    return None if paise is None else int(round(paise / 100))


@adapter("account_aggregator")
def from_vendor(raw: dict) -> dict:
    """Vendor raw → internal AccountAggregator shape. FACTS only — no verdicts."""
    a = raw.get("analysis", raw)
    basis = str(a.get("incomeBasis") or "").strip().upper()
    return {
        "status": "available" if a else "unavailable",
        "name": a.get("accountHolderName") or a.get("name"),
        "address": a.get("address"),
        "period": a.get("statementPeriod") or a.get("period"),
        "imputed_annual_income": _rupees(a.get("imputedAnnualIncomePaise"))
        if "imputedAnnualIncomePaise" in a else a.get("imputed_annual_income"),
        "avg_monthly_balance": _rupees(a.get("avgMonthlyBalancePaise"))
        if "avgMonthlyBalancePaise" in a else a.get("avg_monthly_balance"),
        "expense_to_income": a.get("expenseToIncomeRatio") or a.get("expense_to_income"),
        "income_source": _INCOME_BASIS.get(basis) or a.get("income_source"),
        # Categorized transactions flow through as facts; verdict fields are omitted.
        "credits": a.get("credits", []),
        "debits": a.get("debits", []),
    }
