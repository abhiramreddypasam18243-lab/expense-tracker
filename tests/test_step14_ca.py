"""Step 14 — virtual CA, offline (AC-70 to AC-77)."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import ca, db, tax
from expenses import categorize as cat
from expenses.importer import import_csv
from tools.make_synthetic import generate_csv

ROOT = Path(__file__).resolve().parent.parent
L = 100_000_00  # one lakh rupees in paise


def rs(rupees: int) -> int:
    return rupees * 100


def inputs(**kw) -> tax.TaxInputs:
    return tax.TaxInputs(tax_year=kw.pop("tax_year", "2026-27"), **kw)


# --- AC-70 new regime -----------------------------------------------------------------------------


def test_ac_70_new_regime_examples():
    zero = tax.compute(inputs(salary_minor=rs(12_75_000)), "new")
    assert (zero.taxable_minor, zero.total_tax_minor) == (12 * L, 0)

    near = tax.compute(inputs(salary_minor=rs(13_00_000)), "new")
    assert near.taxable_minor == rs(12_25_000)
    assert near.slab_tax_minor == rs(63_750)
    assert near.tax_after_rebate_minor == rs(25_000)  # marginal relief: tax <= income above ₹12 lakh
    assert near.total_tax_minor == rs(26_000)

    twenty = tax.compute(inputs(salary_minor=rs(20_00_000)), "new")
    assert (twenty.taxable_minor, twenty.slab_tax_minor, twenty.total_tax_minor) == (
        rs(19_25_000),
        rs(1_85_000),
        rs(1_92_400),
    )


# --- AC-71 old regime -----------------------------------------------------------------------------


def test_ac_71_old_regime_examples():
    r = tax.compute(
        inputs(salary_minor=rs(10_00_000), sec80c_minor=rs(1_50_000), sec80d_self_minor=rs(25_000)), "old"
    )
    assert (r.taxable_minor, r.slab_tax_minor, r.total_tax_minor) == (rs(7_75_000), rs(67_500), rs(70_200))

    capped = tax.compute(
        inputs(salary_minor=rs(10_00_000), sec80c_minor=rs(2_00_000), sec80d_self_minor=rs(25_000)), "old"
    )
    assert capped.total_tax_minor == r.total_tax_minor  # 80C capped at ₹1.5 lakh

    senior = tax.compute(inputs(other_income_minor=rs(5_00_000), age="60to79"), "old")
    assert (senior.slab_tax_minor, senior.total_tax_minor) == (rs(10_000), 0)  # 3L exempt, then rebate
    super_senior = tax.compute(inputs(other_income_minor=rs(6_00_000), age="80plus"), "old")
    assert super_senior.slab_tax_minor == rs(20_000)  # 80+: 5L exempt, next slab (5-10L) is 20%
    young = tax.compute(inputs(other_income_minor=rs(6_00_000)), "old")
    assert young.slab_tax_minor == rs(12_500 + 20_000)


# --- AC-72 comparison and tips --------------------------------------------------------------------


def test_ac_72_compare_and_tips():
    heavy = inputs(
        salary_minor=rs(15_00_000),
        sec80c_minor=rs(1_50_000),
        sec80d_self_minor=rs(25_000),
        home_loan_interest_minor=rs(2_00_000),
        sec80ccd1b_minor=rs(50_000),
    )
    c = tax.compare(heavy)
    assert c.best == ("old" if c.old.total_tax_minor < c.new.total_tax_minor else "new")
    assert c.saving_minor == abs(c.old.total_tax_minor - c.new.total_tax_minor)

    light = inputs(salary_minor=rs(15_00_000))
    assert (
        tax.compute(light, "new").total_tax_minor
        == tax.compute(
            inputs(salary_minor=rs(15_00_000), sec80c_minor=rs(1_50_000), sec80d_self_minor=rs(25_000)), "new"
        ).total_tax_minor
    )  # old-only deductions never change the new regime

    almost = inputs(
        salary_minor=rs(15_00_000),
        sec80c_minor=rs(50_000),
        sec80d_self_minor=rs(25_000),
        home_loan_interest_minor=rs(2_00_000),
        sec80ccd1b_minor=rs(50_000),
    )
    comparison = tax.compare(almost)
    for tip in comparison.tips:
        assert tip.new_best_tax_minor < comparison.best_result.total_tax_minor
        redo = tax.compare(tip.apply(almost))
        assert redo.best_result.total_tax_minor == tip.new_best_tax_minor
    assert not tax.compare(light).tips or all(
        t.new_best_tax_minor < tax.compare(light).best_result.total_tax_minor for t in tax.compare(light).tips
    )


# --- AC-73 rounding and surcharge flag ------------------------------------------------------------


def test_ac_73_rounding_types_and_surcharge():
    r = tax.compute(inputs(salary_minor=rs(9_87_654) + 37), "new")
    assert r.taxable_minor % 1000 == 0 and r.total_tax_minor % 1000 == 0
    for value in (
        r.taxable_minor,
        r.slab_tax_minor,
        r.tax_after_rebate_minor,
        r.cess_minor,
        r.total_tax_minor,
    ):
        assert type(value) is int
    assert not r.surcharge_excluded
    assert tax.compute(inputs(salary_minor=rs(60_00_000)), "new").surcharge_excluded


# --- AC-74 HRA ------------------------------------------------------------------------------------


def test_ac_74_hra_exemption():
    base = dict(
        hra_received_minor=rs(3_00_000), basic_salary_minor=rs(6_00_000), rent_paid_minor=rs(3_60_000)
    )
    # least of: 3,00,000 received; 3,60,000 - 60,000 = 3,00,000; 40% / 50% of basic = 2,40,000 / 3,00,000
    assert tax.hra_exemption(inputs(tax_year="2025-26", city="Bengaluru", **base)) == rs(2_40_000)
    assert tax.hra_exemption(inputs(tax_year="2026-27", city="Bengaluru", **base)) == rs(3_00_000)
    assert tax.hra_exemption(inputs(tax_year="2025-26", city="Delhi", **base)) == rs(3_00_000)
    assert (
        tax.hra_exemption(
            inputs(
                tax_year="2026-27",
                city="Delhi",
                hra_received_minor=rs(3_00_000),
                basic_salary_minor=rs(6_00_000),
            )
        )
        == 0
    )


# --- AC-75 tax-relevant transactions --------------------------------------------------------------


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    cat.seed_defaults(connection)
    yield connection
    connection.close()


def test_ac_75_detects_tax_items_in_tax_year(conn):
    rows = [
        "2026-03-20,UPI/R0/LIC OF INDIA/OKAXIS/premium,-9000.00",  # previous tax year: ignored
        "2026-04-10,UPI/R1/LIC OF INDIA/OKAXIS/premium,-12000.00",
        "2026-05-05,NEFT STAR HEALTH INSURANCE R2,-18000.00",
        "2026-06-01,PPF DEPOSIT R3,-50000.00",
        "2026-06-15,UPI-PM CARES-R4-DONATION,-2000.00",
        "2026-07-01,IMPS RENT R5 LANDLORD,-25000.00",
        "2026-07-31,SB INT PD 01-04 TO 30-06,1234.00",
        "2026-08-02,POS 9 CITY HOSPITAL TREATMENT,-7000.00",
        "2026-08-03,POS 7 POLICYBAZAAR SHOES,-999.00",  # "LIC" inside POLICY must not match
    ]
    import_csv(conn, "Date,Description,Amount\n" + "\n".join(rows) + "\n", "HDFC", "t.csv", fetch_rate=None)
    items = {i.key: i for i in ca.detect_tax_items(conn, "2026-27")}
    assert items["life_insurance"].total_minor == rs(12_000) and len(items["life_insurance"].rows) == 1
    assert items["health_insurance"].total_minor == rs(18_000)
    assert items["ppf_elss_tuition"].total_minor == rs(50_000)
    assert items["donations"].total_minor == rs(2_000)
    assert items["rent"].total_minor == rs(25_000)
    assert items["interest"].total_minor == rs(1_234)
    assert all("HOSPITAL" not in r["description_norm"] for i in items.values() for r in i.rows)
    assert all("POLICYBAZAAR" not in r["description_norm"] for i in items.values() for r in i.rows)
    assert "123" in items["life_insurance"].section and "80C" in items["life_insurance"].section
    assert ca.detect_tax_items(conn, "2025-26")[0].section.startswith("80C")

    prefill = ca.prefill(list(items.values()))
    assert prefill["sec80c_minor"] == rs(62_000)
    assert prefill["sec80d_self_minor"] == rs(18_000)
    assert prefill["rent_paid_minor"] == rs(25_000)


# --- AC-76 filing calendar and checklist ----------------------------------------------------------


def test_ac_76_calendar_and_checklist():
    today = date(2026, 10, 13)
    cal = {d.title: d for d in ca.filing_calendar("2026-27", today)}
    assert cal["Advance tax, 1st instalment (15%)"].status == "past"
    assert cal["Advance tax, 2nd instalment (45%)"].status == "past"
    assert cal["Advance tax, 3rd instalment (75%)"].status == "next"
    assert cal["Advance tax, 3rd instalment (75%)"].day == date(2026, 12, 15)
    assert cal["ITR due (ITR-1 / ITR-2)"].day == date(2027, 7, 31)

    old = {d.title: d for d in ca.filing_calendar("2025-26", today)}
    assert old["ITR due (ITR-1 / ITR-2)"].status == "past"
    assert old["Belated return (if you missed the due date)"].day == date(2026, 12, 31)
    assert old["Belated return (if you missed the due date)"].highlight

    base = ca.checklist([], "2026-27")
    assert any("Form 16" in item for item in base) and any("AIS" in item for item in base)
    assert not any("Rent receipts" in item for item in base)
    rent = ca.TaxItem("rent", "Rent paid", "HRA", rs(1_20_000), [])
    assert any("Rent receipts" in item and "PAN" in item for item in ca.checklist([rent], "2026-27"))


# --- AC-77 page -----------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["empty", "seeded", "web"])
def test_ac_77_ca_page_renders(tmp_path, monkeypatch, mode):
    path = tmp_path / "ca.db"
    monkeypatch.setenv("EXPENSES_DB", str(path))
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_TLS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EXPENSES_DEMO", "1" if mode == "web" else "0")
    if mode == "seeded":
        c = db.connect(path)
        import_csv(c, generate_csv(42, "amount"), "Demo", "s.csv", fetch_rate=None)
        c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page("ui/ca.py").run()
    assert not at.exception, [e.value for e in at.exception]
    text = "\n".join(
        [m.value for m in at.markdown] + [c.value for c in at.caption] + [w.value for w in at.warning]
    )
    assert "New regime" in text and "Old regime" in text
    assert "not professional advice" in text
