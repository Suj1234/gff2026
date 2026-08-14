"""pricing.py — INDICATIVE premium calculator. JOURNEY-ONLY, not the agent.

TERM LIFE, single life. The underwriting engine NEVER prices (CLAUDE.md forbids touching
pricing knobs). This is a small illustrative calculator that drives the Step-2 "indicative
premium" display and stores the chosen riders on the bundle. Every number here is a demo
placeholder.

# indicative — journey only, NOT actuarial. Tunable in one place (this file).
"""

from __future__ import annotations

# Base annual premium per ₹1L of TERM sum assured, by age band. Placeholder curve.
# Term-life rates per lakh are far lower than health (pure mortality cover, large SA).
# indicative — journey only, not actuarial.
_BASE_PER_LAKH = [
    (0, 30, 12),
    (31, 40, 18),
    (41, 45, 32),
    (46, 50, 55),
    (51, 55, 90),
    (56, 200, 150),
]

# Zone loading by pincode first digit (metro vs rest) — crude placeholder.
_METRO_FIRST_DIGITS = {"1", "4", "5", "6", "7"}  # Delhi/Mumbai/Chennai/Kolkata/Bangalore-ish
_ZONE_METRO = 1.15
_ZONE_REST = 1.0

_SMOKER_LOAD = 1.5           # +50% if tobacco declared (mortality-driven for term life)

# Term PLAN variants (tiers). id -> (label, premium multiplier). indicative — journey only.
PLANS = {
    "term_protect": ("Acme Term Protect", 1.00),   # essential cover
    "term_plus":    ("Acme Term Plus", 1.12),       # popular — richer features
    "term_elite":   ("Acme Term Elite", 1.25),      # premium — fullest benefits
}
_DEFAULT_PLAN = "term_protect"

# Riders. Each: (label, mode, rate).
#   mode "sa"     -> priced on the rider's OWN sum assured (rate = ₹/yr per ₹1L of rider SA)
#   mode "income" -> priced on a monthly income benefit (rate = ₹/yr per ₹1k/month)
#   mode "flat"   -> no amount to size; flat ₹/yr (checkbox-only rider)
# TERM-LIFE riders (IRDAI-approved add-ons). indicative — journey only, not actuarial.
RIDERS = {
    "critical_illness":      ("Critical Illness", "sa", 45),        # per ₹1L of CI cover
    "accidental_death":      ("Accidental Death Benefit", "sa", 8), # per ₹1L of ADB cover
    "accidental_disability": ("Accidental Total & Permanent Disability", "sa", 10),
    "income_benefit":        ("Income Benefit", "income", 240),     # per ₹1k/month of income
    "waiver_of_premium":     ("Waiver of Premium", "flat", 450),    # checkbox-only, flat
}


def _base_per_lakh(age: int) -> int:
    for lo, hi, rate in _BASE_PER_LAKH:
        if lo <= age <= hi:
            return rate
    return _BASE_PER_LAKH[-1][2]


def _rider_sum(riders) -> list[dict]:
    """Normalize the riders input to a list of {id, amount} dicts.
    Accepts either legacy ["id", ...] (amount defaults) or [{"id","amount"}, ...]."""
    out = []
    for r in riders or []:
        if isinstance(r, str):
            out.append({"id": r, "amount": 0})
        elif isinstance(r, dict) and r.get("id"):
            out.append({"id": r["id"], "amount": int(r.get("amount") or 0)})
    return out


def compute_premium(*, age: int, sum_assured: int, product_type: str = "term_life",
                    plan: str | None = None, tenure_years: int = 20, tobacco: bool = False,
                    pincode: str | None = None, riders=None) -> dict:
    """Return an indicative premium breakdown. Pure function — no I/O.

    `riders` is a list of {"id","amount"} (amount = rider SA in ₹ for 'sa' riders,
    or ₹/month for 'income' riders; ignored for 'flat'). Legacy ["id"] also accepted.
    """
    lakhs = max(sum_assured, 0) / 100_000
    base = _base_per_lakh(max(age, 0)) * lakhs

    zone = _ZONE_METRO if (pincode and pincode[:1] in _METRO_FIRST_DIGITS) else _ZONE_REST
    base *= zone
    if tobacco:
        base *= _SMOKER_LOAD
    # plan tier multiplier (term is single-life only — no floater/family multiplier)
    base *= PLANS.get(plan or _DEFAULT_PLAN, PLANS[_DEFAULT_PLAN])[1]
    # longer term = more years of mortality risk covered -> higher annual premium.
    # indicative: +1.5% per year beyond a 20-yr reference, floored at -15%.
    base *= max(0.85, 1 + 0.015 * (max(tenure_years, 5) - 20))
    _ = product_type

    base_r = round(base)
    rider_lines = []
    rider_total = 0
    for r in _rider_sum(riders):
        spec = RIDERS.get(r["id"])
        if not spec:
            continue
        label, mode, rate = spec
        if mode == "sa":
            # rider SA is capped at the base sum assured (IRDAI/market norm)
            cover = min(max(r["amount"], 0), max(sum_assured, 0))
            amt = round(rate * cover / 100_000)
            rider_lines.append({"id": r["id"], "label": label, "amount": amt, "cover": cover})
        elif mode == "income":
            monthly = max(r["amount"], 0)          # ₹/month
            amt = round(rate * monthly / 1000)
            rider_lines.append({"id": r["id"], "label": label, "amount": amt, "monthly": monthly})
        else:  # flat, checkbox-only
            amt = int(rate)
            rider_lines.append({"id": r["id"], "label": label, "amount": amt})
        rider_total += amt

    # Sum the DISPLAYED (rounded) parts so the breakdown adds up exactly on screen.
    return {
        "base": base_r,
        "riders": rider_lines,
        "rider_total": rider_total,
        "total_annual": base_r + rider_total,
        "note": "Indicative — subject to underwriting. Journey estimate, not a quote.",
    }


def _demo() -> None:
    p = compute_premium(age=48, sum_assured=10_000_000, tobacco=True, pincode="560093",
                        riders=[{"id": "critical_illness", "amount": 5_000_000},
                                {"id": "accidental_death", "amount": 10_000_000}])
    assert p["base"] > 0
    assert p["total_annual"] == p["base"] + p["rider_total"]
    assert len(p["riders"]) == 2
    # rider SA is capped at base SA: ask for 5Cr CI on a 1Cr base -> priced on 1Cr
    capped = compute_premium(age=30, sum_assured=10_000_000,
                             riders=[{"id": "critical_illness", "amount": 50_000_000}])
    assert capped["riders"][0]["cover"] == 10_000_000, capped["riders"][0]
    # income-benefit priced on ₹/month, not a lump SA
    inc = compute_premium(age=30, sum_assured=10_000_000,
                          riders=[{"id": "income_benefit", "amount": 50_000}])  # ₹50k/mo
    assert inc["riders"][0]["amount"] > 0 and inc["riders"][0]["monthly"] == 50_000
    # waiver is flat, no amount to size
    wop = compute_premium(age=30, sum_assured=10_000_000, riders=[{"id": "waiver_of_premium"}])
    assert wop["riders"][0]["amount"] == 450
    # legacy string ids still accepted (amount defaults to 0 -> sa riders price to 0)
    legacy = compute_premium(age=30, sum_assured=10_000_000, riders=["critical_illness"])
    assert legacy["riders"][0]["amount"] == 0
    # older + smoker + metro must cost more than young non-smoker rest
    q = compute_premium(age=25, sum_assured=10_000_000, tobacco=False, pincode="999999")
    assert p["base"] > q["base"], (p["base"], q["base"])
    # plan tiers scale premium: Elite > Protect for identical cover
    protect = compute_premium(age=30, sum_assured=10_000_000, plan="term_protect")
    elite = compute_premium(age=30, sum_assured=10_000_000, plan="term_elite")
    assert elite["base"] > protect["base"], (elite["base"], protect["base"])
    # longer term costs more per year
    t10 = compute_premium(age=30, sum_assured=10_000_000, tenure_years=10)
    t30 = compute_premium(age=30, sum_assured=10_000_000, tenure_years=30)
    assert t30["base"] > t10["base"], (t30["base"], t10["base"])
    print(f"pricing OK (term life) - 48yo smoker metro 1Cr +2 riders = Rs.{p['total_annual']}/yr; "
          f"CI cover capped at base; income on Rs/mo; plans Protect<Elite")


if __name__ == "__main__":
    _demo()
