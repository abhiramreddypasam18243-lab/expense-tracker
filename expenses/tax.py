"""Indian income-tax estimate for individuals: old vs new regime (spec section 6.5.1).

Money is int paise. Percentages are applied with Decimal and the results rounded the way the
law does: taxable income and tax to the nearest ₹10. Planning estimate only: no surcharge
(flagged above ₹50 lakh), no capital gains or business income.

Rates and limits are the same for tax years 2025-26 and 2026-27 (Budget 2026 made no changes);
only section numbers changed with the Income-tax Act, 2025 (in force from 1 April 2026).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal

LAKH = 100_000_00  # ₹1,00,000 in paise
TAX_YEARS = ("2025-26", "2026-27")
AGE_GROUPS = {"below60": "Below 60", "60to79": "60 to 79", "80plus": "80 and above"}
SURCHARGE_FROM = 50 * LAKH
CESS_PERCENT = 4

# (upper limit of the slab in paise, or None for "and above", rate %)
NEW_SLABS = [
    (4 * LAKH, 0),
    (8 * LAKH, 5),
    (12 * LAKH, 10),
    (16 * LAKH, 15),
    (20 * LAKH, 20),
    (24 * LAKH, 25),
    (None, 30),
]
OLD_EXEMPT = {"below60": 250_000_00, "60to79": 3 * LAKH, "80plus": 5 * LAKH}
NEW_STANDARD_DEDUCTION = 75_000_00
OLD_STANDARD_DEDUCTION = 50_000_00
NEW_REBATE_LIMIT, NEW_REBATE_MAX = 12 * LAKH, 60_000_00
OLD_REBATE_LIMIT, OLD_REBATE_MAX = 5 * LAKH, 12_500_00

LIMITS = {
    "sec80c": 150_000_00,
    "sec80ccd1b": 50_000_00,
    "home_loan_interest": 2 * LAKH,
    "sec80d": 25_000_00,
    "sec80d_senior": 50_000_00,
    "interest_below60": 10_000_00,  # 80TTA: savings-account interest
    "interest_senior": 50_000_00,  # 80TTB: deposit interest for 60+
}
METROS = {
    "2025-26": {"Delhi", "Mumbai", "Kolkata", "Chennai"},
    # Income-tax Rules, 2026 add four cities to the 50% HRA list.
    "2026-27": {"Delhi", "Mumbai", "Kolkata", "Chennai", "Bengaluru", "Hyderabad", "Pune", "Ahmedabad"},
}
CITIES = sorted(METROS["2026-27"]) + ["Other city"]

# Section names: the Income-tax Act, 2025 renumbered them from tax year 2026-27.
SECTIONS = {
    "2025-26": {
        "sec80c": "80C",
        "sec80ccd1b": "80CCD(1B)",
        "sec80d": "80D",
        "home_loan": "24(b)",
        "sec80e": "80E",
        "sec80g": "80G",
        "interest": "80TTA / 80TTB",
        "rebate": "87A",
        "hra": "HRA, s.10(13A)",
        "new_regime": "115BAC",
        "rent_no_hra": "80GG",
    },
    "2026-27": {
        "sec80c": "s.123 (old 80C)",
        "sec80ccd1b": "s.124 (old 80CCD(1B))",
        "sec80d": "s.126 (old 80D)",
        "home_loan": "s.22 (old 24(b))",
        "sec80e": "s.129 (old 80E)",
        "sec80g": "s.133 (old 80G)",
        "interest": "s.153 (old 80TTA/TTB)",
        "rebate": "s.156 (old 87A)",
        "hra": "HRA, Schedule II",
        "new_regime": "s.202 (old 115BAC)",
        "rent_no_hra": "s.134 (old 80GG)",
    },
}


@dataclass(frozen=True)
class TaxInputs:
    tax_year: str = "2026-27"
    age: str = "below60"
    salary_minor: int = 0  # gross annual salary (Form 16), before standard deduction
    other_income_minor: int = 0  # interest, etc. (taxed at slab rates)
    sec80c_minor: int = 0
    sec80ccd1b_minor: int = 0
    sec80d_self_minor: int = 0
    sec80d_parents_minor: int = 0
    parents_senior: bool = False
    home_loan_interest_minor: int = 0  # self-occupied house
    sec80e_minor: int = 0  # education-loan interest
    sec80g_minor: int = 0  # eligible donation amount (as on the receipt / AIS)
    savings_interest_minor: int = 0  # part of other income, for 80TTA / 80TTB
    hra_received_minor: int = 0
    basic_salary_minor: int = 0  # basic + DA, annual
    rent_paid_minor: int = 0  # annual
    city: str = "Other city"


@dataclass
class TaxResult:
    regime: str  # "old" | "new"
    gross_income_minor: int
    standard_deduction_minor: int
    hra_exemption_minor: int
    deductions: dict[str, int]  # name -> amount allowed (old regime only)
    taxable_minor: int
    slab_tax_minor: int
    rebate_minor: int
    tax_after_rebate_minor: int
    cess_minor: int
    total_tax_minor: int
    surcharge_excluded: bool


@dataclass
class Tip:
    text: str
    field: str
    extra_minor: int
    new_best_tax_minor: int

    def apply(self, inputs: TaxInputs) -> TaxInputs:
        return replace(inputs, **{self.field: getattr(inputs, self.field) + self.extra_minor})


@dataclass
class Comparison:
    old: TaxResult
    new: TaxResult
    best: str
    saving_minor: int
    tips: list[Tip] = field(default_factory=list)

    @property
    def best_result(self) -> TaxResult:
        return self.old if self.best == "old" else self.new


# --- helpers --------------------------------------------------------------------------------------


def round_to_10(minor: int) -> int:
    """Nearest ₹10, halves up: 9,12,654.37 -> 9,12,650; ₹5 rounds up."""
    rupees = (Decimal(minor) / 100 / 10).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 10
    return int(rupees * 100)


def percent(minor: int, rate: int) -> Decimal:
    return Decimal(minor) * rate / 100


def slab_tax(taxable_minor: int, slabs: list[tuple[int | None, int]]) -> int:
    tax = Decimal(0)
    lower = 0
    for upper, rate in slabs:
        top = taxable_minor if upper is None else min(taxable_minor, upper)
        if top > lower:
            tax += percent(top - lower, rate)
        if upper is None or taxable_minor <= upper:
            break
        lower = upper
    return int(tax)


def old_slabs(age: str) -> list[tuple[int | None, int]]:
    exempt = OLD_EXEMPT[age]
    slabs = [(exempt, 0)]
    if exempt < 5 * LAKH:
        slabs.append((5 * LAKH, 5))
    slabs += [(10 * LAKH, 20), (None, 30)]
    return slabs


def hra_exemption(inputs: TaxInputs) -> int:
    """Least of: HRA received; rent - 10% of basic; 50% (metro) / 40% of basic."""
    if inputs.hra_received_minor <= 0 or inputs.rent_paid_minor <= 0:
        return 0
    rate = 50 if inputs.city in METROS[inputs.tax_year] else 40
    rent_minus_10 = inputs.rent_paid_minor - int(percent(inputs.basic_salary_minor, 10))
    return max(
        0, min(inputs.hra_received_minor, rent_minus_10, int(percent(inputs.basic_salary_minor, rate)))
    )


def old_deductions(inputs: TaxInputs) -> dict[str, int]:
    senior = inputs.age != "below60"
    d80d_self_cap = LIMITS["sec80d_senior"] if senior else LIMITS["sec80d"]
    d80d_parent_cap = LIMITS["sec80d_senior"] if inputs.parents_senior else LIMITS["sec80d"]
    interest_cap = LIMITS["interest_senior"] if senior else LIMITS["interest_below60"]
    return {
        "sec80c": min(inputs.sec80c_minor, LIMITS["sec80c"]),
        "sec80ccd1b": min(inputs.sec80ccd1b_minor, LIMITS["sec80ccd1b"]),
        "sec80d": min(inputs.sec80d_self_minor, d80d_self_cap)
        + min(inputs.sec80d_parents_minor, d80d_parent_cap),
        "home_loan": min(inputs.home_loan_interest_minor, LIMITS["home_loan_interest"]),
        "sec80e": inputs.sec80e_minor,
        "sec80g": inputs.sec80g_minor,
        "interest": min(inputs.savings_interest_minor, interest_cap),
    }


# --- computation ----------------------------------------------------------------------------------


def compute(inputs: TaxInputs, regime: str) -> TaxResult:
    if inputs.tax_year not in TAX_YEARS:
        raise ValueError(f"unsupported tax year {inputs.tax_year}")
    gross = inputs.salary_minor + inputs.other_income_minor
    if regime == "new":
        std = min(inputs.salary_minor, NEW_STANDARD_DEDUCTION)
        hra, deductions = 0, {}
        slabs, rebate_limit, rebate_max = NEW_SLABS, NEW_REBATE_LIMIT, NEW_REBATE_MAX
    elif regime == "old":
        std = min(inputs.salary_minor, OLD_STANDARD_DEDUCTION)
        hra = hra_exemption(inputs)
        deductions = old_deductions(inputs)
        slabs, rebate_limit, rebate_max = old_slabs(inputs.age), OLD_REBATE_LIMIT, OLD_REBATE_MAX
    else:
        raise ValueError("regime must be 'old' or 'new'")

    before_deductions = max(0, gross - std - hra)
    allowed = min(sum(deductions.values()), before_deductions)  # deductions can't go below zero income
    taxable = round_to_10(before_deductions - allowed)
    slab = slab_tax(taxable, slabs)

    if taxable <= rebate_limit:
        rebate = min(slab, rebate_max)
    elif regime == "new" and slab > taxable - rebate_limit:
        rebate = slab - (taxable - rebate_limit)  # marginal relief: tax never exceeds income above ₹12 lakh
    else:
        rebate = 0
    after_rebate = slab - rebate
    cess = int(percent(after_rebate, CESS_PERCENT))
    return TaxResult(
        regime=regime,
        gross_income_minor=gross,
        standard_deduction_minor=std,
        hra_exemption_minor=hra,
        deductions=deductions,
        taxable_minor=taxable,
        slab_tax_minor=slab,
        rebate_minor=rebate,
        tax_after_rebate_minor=after_rebate,
        cess_minor=cess,
        total_tax_minor=round_to_10(after_rebate + cess),
        surcharge_excluded=taxable > SURCHARGE_FROM,
    )


_HEADROOM = [  # (input field, limit key, label key)
    ("sec80c_minor", "sec80c", "sec80c"),
    ("sec80ccd1b_minor", "sec80ccd1b", "sec80ccd1b"),
    ("sec80d_self_minor", "sec80d", "sec80d"),
]


def compare(inputs: TaxInputs) -> Comparison:
    old, new = compute(inputs, "old"), compute(inputs, "new")
    best = "old" if old.total_tax_minor < new.total_tax_minor else "new"
    result = Comparison(old, new, best, abs(old.total_tax_minor - new.total_tax_minor))
    best_tax = result.best_result.total_tax_minor
    names = SECTIONS[inputs.tax_year]
    for field_name, limit_key, label_key in _HEADROOM:
        limit = (
            LIMITS[limit_key]
            if not (limit_key == "sec80d" and inputs.age != "below60")
            else LIMITS["sec80d_senior"]
        )
        extra = limit - getattr(inputs, field_name)
        if extra <= 0:
            continue
        trial = replace(inputs, **{field_name: limit})
        trial_best = min(compute(trial, "old").total_tax_minor, compute(trial, "new").total_tax_minor)
        if trial_best < best_tax:
            result.tips.append(
                Tip(
                    text=f"Using the remaining limit under {names[label_key]} would cut your best tax by "
                    f"{(best_tax - trial_best) // 100:,} rupees (via the old regime).",
                    field=field_name,
                    extra_minor=extra,
                    new_best_tax_minor=trial_best,
                )
            )
    return result
