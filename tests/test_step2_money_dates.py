"""Step 2 — money and dates (AC-01 to AC-12)."""

from datetime import date

import pytest

from expenses.dates import DateParseError, parse_date
from expenses.money import MoneyParseError, format_inr, parse_amount, parse_debit_credit


def test_ac_01_indian_grouping():
    assert format_inr(12345678) == "₹1,23,456.78"
    assert format_inr(5) == "₹0.05"
    assert format_inr(100000000) == "₹10,00,000.00"
    assert format_inr(0) == "₹0.00"
    assert format_inr(99999) == "₹999.99"
    assert format_inr(100000) == "₹1,000.00"


def test_ac_02_negative_display():
    assert format_inr(-123450) == "-₹1,234.50"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("500", 50000),
        ("500.5", 50050),
        ("500.50", 50050),
        ("1,23,456.78", 12345678),
        ("123,456.78", 12345678),
    ],
)
def test_ac_03_plain_and_grouped(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", [" ₹ 1,234.50 ", "Rs.1234.5", "INR 1234.50", "rs 1234.50", "₹1234.50"])
def test_ac_04_currency_markers(text):
    assert parse_amount(text) == 123450


@pytest.mark.parametrize(
    "text, expected",
    [("-500", -50000), ("+500", 50000), ("(500.00)", -50000), ("500.00 Dr", -50000), ("500 CR", 50000)],
)
def test_ac_05_signs(text, expected):
    assert parse_amount(text) == expected


def test_ac_06_debit_credit_columns():
    assert parse_debit_credit("1,000.00", "") == -100000
    assert parse_debit_credit("", "250") == 25000
    assert parse_debit_credit("  ", "250") == 25000
    with pytest.raises(MoneyParseError):
        parse_debit_credit("10", "20")
    with pytest.raises(MoneyParseError):
        parse_debit_credit("", " ")


def test_ac_07_too_many_decimals():
    with pytest.raises(MoneyParseError):
        parse_amount("10.555")


@pytest.mark.parametrize(
    "text", ["", "   ", "abc", "1,,000", "--5", "-500 Dr", "(500) Dr", "12.3.4", "₹", "1e5"]
)
def test_ac_08_garbage_rejected_without_echo(text):
    with pytest.raises(MoneyParseError) as exc:
        parse_amount(text)
    if text.strip():
        assert text.strip() not in str(exc.value)


def test_ac_09_no_float():
    with pytest.raises(TypeError):
        parse_amount(10.5)  # type: ignore[arg-type]
    for value in (parse_amount("1,234.50"), parse_amount("(5)"), parse_debit_credit("1", "")):
        assert type(value) is int


@pytest.mark.parametrize(
    "text",
    [
        "12/10/2026",
        "12-10-2026",
        "12.10.2026",
        "2026-10-12",
        "12-Oct-2026",
        "12 Oct 2026",
        "12/10/26",
        "12-oct-2026",
    ],
)
def test_ac_10_date_formats(text):
    assert parse_date(text) == date(2026, 10, 12)


def test_ac_11_day_first():
    assert parse_date("03/04/2026") == date(2026, 4, 3)
    assert parse_date(" 03/04/2026 ") == date(2026, 4, 3)


@pytest.mark.parametrize("text", ["31/02/2026", "2026-13-01", "hello", "", "12/10", "2026/10/12x"])
def test_ac_12_invalid_dates(text):
    with pytest.raises(DateParseError):
        parse_date(text)
