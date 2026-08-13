"""pricing.py — INDICATIVE premium calculator. JOURNEY-ONLY, not the agent.

The underwriting engine NEVER prices (CLAUDE.md forbids touching pricing knobs). This
is a small illustrative calculator that drives the Step-2 "indicative premium" display
and stores the chosen riders on the bundle. Every number here is a demo placeholder.

# indicative — journey only, NOT actuarial. Tunable in one place (this file).
"""

from __future__ import annotations

# Base annual premium per ₹1L of sum insured, by age band. Placeholder curve.
# indicative — journey only, not actuarial.
_BASE_PER_LAKH = [
    (0, 30, 220),
    (31, 40, 300),
    (41, 45, 420),
    (46, 50, 620),
    (51, 55, 900),
    (56, 200, 1400),
]

# Zone loading by pincode first digit (metro vs rest) — crude placeholder.
_METRO_FIRST_DIGITS = {"1", "4", "5", "6", "7"}  # Delhi/Mumbai/Chennai/Kolkata/Bangalore-ish
_ZONE_METRO = 1.15
_ZONE_REST = 1.0

_SMOKER_LOAD = 1.25          # +25% if tobacco declared
_FLOATER_LOAD = 1.6          # family floater vs individual (covers >1 life)

# Riders: id -> (label, kind, value). kind "pct" = % of base; "flat" = flat ₹/year.
# indicative — journey only, not actuarial.
RIDERS = {
    "room_rent_waiver":   ("Room Rent Waiver", "pct", 0.10),
    "hospital_cash":      ("Hospital Cash (₹2k/day)", "flat", 1800),
    "consumables":        ("Consumables / Non-Medical", "pct", 0.08),
    "opd":                ("OPD Cover", "pct", 0.18),
    "critical_illness":   ("Critical Illness", "pct", 0.22),
    "personal_accident":  ("Personal Accident", "flat", 1200),
    "maternity":          ("Maternity & Newborn", "pct", 0.30),
    "restoration":        ("Restoration / Recharge of SI", "pct", 0.06),
    "ncb_booster":        ("No-Claim Bonus Booster", "pct", 0.05),
    "wellness":           ("Wellness / Preventive", "flat", 600),
}


def _base_per_lakh(age: int) -> int:
    for lo, hi, rate in _BASE_PER_LAKH:
        if lo <= age <= hi:
            return rate
    return _BASE_PER_LAKH[-1][2]


def compute_premium(*, age: int, sum_assured: int, product_type: str = "individual_health",
                    tobacco: bool = False, pincode: str | None = None,
                    riders: list[str] | None = None) -> dict:
    """Return an indicative premium breakdown. Pure function — no I/O."""
    riders = riders or []
    lakhs = max(sum_assured, 0) / 100_000
    base = _base_per_lakh(max(age, 0)) * lakhs

    zone = _ZONE_METRO if (pincode and pincode[:1] in _METRO_FIRST_DIGITS) else _ZONE_REST
    base *= zone
    if tobacco:
        base *= _SMOKER_LOAD
    if "floater" in (product_type or "").lower():
        base *= _FLOATER_LOAD

    base_r = round(base)
    rider_lines = []
    rider_total = 0
    for rid in riders:
        spec = RIDERS.get(rid)
        if not spec:
            continue
        label, kind, value = spec
        amt = round(base * value) if kind == "pct" else int(value)
        rider_total += amt
        rider_lines.append({"id": rid, "label": label, "amount": amt})

    # Sum the DISPLAYED (rounded) parts so the breakdown adds up exactly on screen.
    return {
        "base": base_r,
        "riders": rider_lines,
        "rider_total": rider_total,
        "total_annual": base_r + rider_total,
        "note": "Indicative — subject to underwriting. Journey estimate, not a quote.",
    }


def _demo() -> None:
    p = compute_premium(age=48, sum_assured=1_000_000, tobacco=True, pincode="560093",
                        riders=["room_rent_waiver", "hospital_cash"])
    assert p["base"] > 0
    assert p["total_annual"] == p["base"] + p["rider_total"]
    assert len(p["riders"]) == 2
    # older + smoker + metro must cost more than young non-smoker rest
    q = compute_premium(age=25, sum_assured=1_000_000, tobacco=False, pincode="999999")
    assert p["base"] > q["base"], (p["base"], q["base"])
    print(f"pricing OK - 48yo smoker metro 10L +2 riders = Rs.{p['total_annual']}/yr; 25yo clean = Rs.{q['base']}/yr base")


if __name__ == "__main__":
    _demo()
