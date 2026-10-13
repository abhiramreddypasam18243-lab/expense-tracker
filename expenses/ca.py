"""Virtual CA helpers: tax-relevant transactions, pre-filled deductions, filing dates, checklist.

Spec sections 6.5.2 and 6.5.3. Detection is a *hint* from bank descriptions; the checklist asks
for the proof documents. Planning help only, not professional advice.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from expenses import db
from expenses.tax import SECTIONS, TAX_YEARS


@dataclass(frozen=True)
class ItemRule:
    key: str
    label: str
    section_key: str | None  # key into tax.SECTIONS, or None
    kind: str  # "debit" | "credit"
    patterns: tuple[str, ...] = ()  # whole-word regexes on the normalised description
    category: str | None = None  # or: every row in this category
    note: str = ""


# Order = display order. Patterns use word boundaries so "LIC" doesn't match "POLICY".
ITEM_RULES = [
    ItemRule(
        "life_insurance",
        "Life insurance premiums",
        "sec80c",
        "debit",
        (r"LIC", r"LIFE INSURANCE", r"HDFC LIFE", r"SBI LIFE", r"ICICI PRU\w*", r"MAX LIFE", r"TATA AIA"),
    ),
    ItemRule(
        "ppf_elss_tuition",
        "PPF / ELSS / tuition fees",
        "sec80c",
        "debit",
        (r"PPF", r"ELSS", r"TUITION", r"SCHOOL FEES?"),
        note="Mutual-fund SIPs only count if the fund is ELSS.",
    ),
    ItemRule("nps", "NPS (own contribution)", "sec80ccd1b", "debit", (r"NPS", r"PRAN")),
    ItemRule(
        "health_insurance",
        "Health insurance premiums",
        "sec80d",
        "debit",
        (r"HEALTH INSURANCE", r"MEDICLAIM", r"STAR HEALTH", r"NIVA BUPA", r"CARE HEALTH", r"ICICI LOMBARD"),
    ),
    ItemRule(
        "home_loan",
        "Home loan EMIs",
        "home_loan",
        "debit",
        (r"HOME LOAN", r"HOUSING LOAN", r"HSG LOAN"),
        note="An EMI is interest + principal; take the split from the bank's interest certificate.",
    ),
    ItemRule(
        "education_loan",
        "Education loan EMIs",
        "sec80e",
        "debit",
        (r"EDUCATION LOAN", r"EDU LOAN"),
        note="Only the interest part is deductible.",
    ),
    ItemRule(
        "donations",
        "Donations",
        "sec80g",
        "debit",
        (r"DONATION", r"PM CARES", r"CRY", r"GIVEINDIA"),
        note="Only donations to approved funds/charities with an 80G receipt count.",
    ),
    ItemRule(
        "rent",
        "Rent paid",
        "hra",
        "debit",
        category="Rent",
        note="Claim as HRA if your salary has HRA; otherwise the rent deduction (80GG) may apply.",
    ),
    ItemRule(
        "interest",
        "Savings / deposit interest received",
        "interest",
        "credit",
        (r"INT\.? ?PD", r"INTEREST CREDIT", r"SB INT", r"INT CREDIT"),
        note="Interest is taxable income; part of it may be deductible.",
    ),
    ItemRule(
        "salary",
        "Salary received (take-home)",
        None,
        "credit",
        category="Salary",
        note="Bank credits are after TDS and payroll deductions; use gross salary from Form 16 for tax.",
    ),
]


@dataclass
class TaxItem:
    key: str
    label: str
    section: str
    total_minor: int  # positive amount
    rows: list = field(default_factory=list)
    note: str = ""


def tax_year_range(tax_year: str) -> tuple[date, date]:
    """'2026-27' -> (2026-04-01, 2027-04-01): start inclusive, end exclusive."""
    if tax_year not in TAX_YEARS:
        raise ValueError(f"unsupported tax year {tax_year}")
    start = int(tax_year[:4])
    return date(start, 4, 1), date(start + 1, 4, 1)


def _matches(rule: ItemRule, row) -> bool:
    amount = row["amount_minor"]
    if (rule.kind == "debit" and amount >= 0) or (rule.kind == "credit" and amount <= 0):
        return False
    if rule.category is not None:
        return row["category_name"] == rule.category
    return any(re.search(rf"\b{p}\b", row["description_norm"]) for p in rule.patterns)


def detect_tax_items(conn: sqlite3.Connection, tax_year: str) -> list[TaxItem]:
    start, end = tax_year_range(tax_year)
    rows = db.list_transactions(conn, start=start, end=end, newest_first=False)
    names = SECTIONS[tax_year]
    items = []
    for rule in ITEM_RULES:
        matched = [r for r in rows if _matches(rule, r)]
        if matched:
            section = names[rule.section_key] if rule.section_key else "—"
            total = sum(abs(r["amount_minor"]) for r in matched)
            items.append(TaxItem(rule.key, rule.label, section, total, matched, rule.note))
    return items


def prefill(items: list[TaxItem]) -> dict[str, int]:
    """Starting values for the deduction fields (the user can change them)."""
    by_key = {i.key: i.total_minor for i in items}
    return {
        "sec80c_minor": by_key.get("life_insurance", 0) + by_key.get("ppf_elss_tuition", 0),
        "sec80ccd1b_minor": by_key.get("nps", 0),
        "sec80d_self_minor": by_key.get("health_insurance", 0),
        "sec80g_minor": by_key.get("donations", 0),
        "rent_paid_minor": by_key.get("rent", 0),
        "savings_interest_minor": by_key.get("interest", 0),
        "salary_takehome_minor": by_key.get("salary", 0),
    }


# --- filing dates and checklist -------------------------------------------------------------------


@dataclass
class Deadline:
    day: date
    title: str
    detail: str
    status: str = "upcoming"  # "past" | "next" | "upcoming"
    highlight: bool = False


ITR_DUE = "ITR due (ITR-1 / ITR-2)"
BELATED = "Belated return (if you missed the due date)"


def filing_calendar(tax_year: str, today: date) -> list[Deadline]:
    start = int(tax_year[:4])
    nxt = start + 1
    deadlines = [
        Deadline(
            date(start, 6, 15),
            "Advance tax, 1st instalment (15%)",
            "Only if tax still due after TDS is ₹10,000 or more.",
        ),
        Deadline(
            date(start, 9, 15), "Advance tax, 2nd instalment (45%)", "Cumulative 45% of the year's tax."
        ),
        Deadline(
            date(start, 12, 15), "Advance tax, 3rd instalment (75%)", "Cumulative 75% of the year's tax."
        ),
        Deadline(
            date(nxt, 3, 15), "Advance tax, 4th instalment (100%)", "Pay the balance before the year ends."
        ),
        Deadline(date(nxt, 7, 31), ITR_DUE, "Salaried / pension / interest income (no business income)."),
        Deadline(date(nxt, 8, 31), "ITR due (ITR-3 / ITR-4, non-audit)", "Business or professional income."),
        Deadline(date(nxt, 12, 31), BELATED, "Late fee applies; some losses can't be carried forward."),
        Deadline(date(nxt + 1, 3, 31), "Revised return deadline", "Fix mistakes in a filed return."),
    ]
    next_found = False
    for d in deadlines:
        if d.day < today:
            d.status = "past"
        elif not next_found:
            d.status, next_found = "next", True
    itr = next(d for d in deadlines if d.title == ITR_DUE)
    belated = next(d for d in deadlines if d.title == BELATED)
    belated.highlight = itr.status == "past" and belated.status != "past"
    return deadlines


BASE_DOCUMENTS = [
    "Form 16 from your employer (salary and TDS)",
    "AIS / TIS from the income-tax portal (all income reported about you)",
    "Form 26AS (tax credited against your PAN)",
    "Bank interest certificates for savings accounts and FDs",
    "PAN linked with Aadhaar; bank account pre-validated on the e-filing portal for refunds",
]
ITEM_DOCUMENTS = {
    "rent": "Rent receipts and rent agreement (landlord's PAN if rent is above ₹1 lakh a year)",
    "life_insurance": "Life-insurance premium receipts",
    "ppf_elss_tuition": "PPF passbook / ELSS statement / school tuition-fee receipts",
    "nps": "NPS contribution statement (Tier I)",
    "health_insurance": "Health-insurance premium receipts (separate ones for parents, if any)",
    "home_loan": "Home-loan interest certificate (interest and principal split)",
    "education_loan": "Education-loan interest certificate",
    "donations": "Donation receipts with the charity's 80G registration number",
}


def checklist(items: list[TaxItem], tax_year: str) -> list[str]:
    keys = {i.key for i in items}
    return BASE_DOCUMENTS + [doc for key, doc in ITEM_DOCUMENTS.items() if key in keys]
