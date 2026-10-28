"""Step 5 — insights, budgets, anomalies (AC-27 to AC-32)."""

from decimal import Decimal

import pytest

from expenses import categorize as cat
from expenses import db, insights
from expenses.anomaly import find_anomalies, median, robust_scores
from expenses.importer import import_csv


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    cat.seed_defaults(connection)
    yield connection
    connection.close()


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def run(conn, lines):
    text = "Date,Description,Amount\n" + "".join(line + "\n" for line in lines)
    return import_csv(conn, text, account="Test Bank", source_name="t.csv", fetch_rate=no_network)


MARCH = [
    "2026-03-01,NEFT SALARY ACME SYNTHETIC LTD,50000.00",
    "2026-03-02,REFUND AMAZON RETAIL SYN4,500.00",
    "2026-03-03,IMPS SELF TRANSFER SYN3,-10000.00",
    "2026-03-04,UPI/SYN1/SWIGGY/OKAXIS/lunch,-300.00",
    "2026-03-05,UPI/SYN5/SWIGGY/OKAXIS/dinner,-200.00",
    "2026-03-06,POS 7310 AMAZON RETAIL,-2000.00",
    "2026-03-07,ATM WDL SYN6,-1000.00",
    "2026-03-08,UPI-UBER INDIA-SYN2-RIDE,-150.00",
    "2026-02-27,UPI/SYN7/SWIGGY/OKAXIS/feb,-999.00",
]


# --- AC-27 / AC-28 monthly summary and trend --------------------------------------


def test_ac_27_monthly_summary(conn):
    run(conn, MARCH)
    s = insights.monthly_summary(conn, "2026-03")
    assert s.income_minor == 5_050_000
    assert s.spend_minor == 265_000  # the ATM withdrawal moves money to the cash wallet (Step 26)
    assert s.net_minor == 4_785_000
    assert s.txn_count == 8
    assert [(c.name, c.spend_minor) for c in s.by_category] == [
        ("Shopping", 200_000),
        ("Food & Dining", 50_000),
        ("Transport", 15_000),
    ]
    assert all(c.color.startswith("#") for c in s.by_category)
    assert s.top_merchants == [("Amazon Retail", 200_000), ("Swiggy", 50_000), ("Uber India", 15_000)]


def test_ac_27_empty_month(conn):
    s = insights.monthly_summary(conn, "2026-03")
    assert (s.income_minor, s.spend_minor, s.net_minor, s.txn_count) == (0, 0, 0, 0)
    assert s.by_category == [] and s.top_merchants == []


def test_ac_28_trend_has_twelve_months(conn):
    run(conn, MARCH)
    trend = insights.spending_trend(conn, "2026-03")
    assert len(trend) == 12
    assert trend[0] == ("2025-04", 0)
    assert trend[-2:] == [("2026-02", 99_900), ("2026-03", 265_000)]
    assert insights.spending_trend(conn, "2026-01", months=3) == [
        ("2025-11", 0),
        ("2025-12", 0),
        ("2026-01", 0),
    ]


def test_ac_28_bad_month_rejected(conn):
    with pytest.raises(ValueError):
        insights.monthly_summary(conn, "2026-13")


# --- AC-29 budgets ----------------------------------------------------------------


@pytest.mark.parametrize(
    "spent, status",
    [
        (0, "ok"),
        (79_000, "ok"),
        (79_999, "ok"),
        (80_000, "warning"),
        (100_000, "warning"),
        (100_001, "over"),
        (101_000, "over"),
    ],
)
def test_ac_29_budget_boundaries(spent, status):
    assert insights.budget_state(spent, 100_000) == status


def test_ac_29_budget_status_for_month(conn):
    run(conn, MARCH)
    insights.set_budget(conn, cat.category_id(conn, "Food & Dining"), 60_000)
    insights.set_budget(conn, cat.category_id(conn, "Shopping"), 150_000)
    statuses = {b.name: b for b in insights.budget_status(conn, "2026-03")}
    assert (statuses["Food & Dining"].spent_minor, statuses["Food & Dining"].status) == (50_000, "warning")
    assert (statuses["Shopping"].spent_minor, statuses["Shopping"].status) == (200_000, "over")
    insights.set_budget(conn, cat.category_id(conn, "Shopping"), 300_000)  # update in place
    assert {b.name: b.status for b in insights.budget_status(conn, "2026-03")}["Shopping"] == "ok"
    with pytest.raises(ValueError):
        insights.set_budget(conn, cat.category_id(conn, "Salary"), 1000)  # income category
    with pytest.raises(ValueError):
        insights.set_budget(conn, cat.category_id(conn, "Rent"), 0)


# --- AC-30 to AC-32 anomalies -------------------------------------------------------


def rupees(values):
    return [v * 100 for v in values]


def test_ac_30_modified_z_score_hand_example():
    values = rupees([100, 110, 120, 130, 140, 150, 160, 5000])
    assert median(values) == Decimal(13500)
    scores = robust_scores(values)
    # median 135, |dev| = 35,25,15,5,5,15,25,4865 -> MAD = (15+25)/2 = 20
    # M(5000) = 0.6745 * 4865 / 20 = 164.07213 ; M(100) = 0.6745 * -35 / 20 = -1.180375
    assert round(scores[-1], 3) == 164.072
    assert round(scores[0], 3) == -1.180
    assert all(isinstance(s, float) for s in scores)
    assert median([1, 2, 3]) == 2 and median([1, 2]) == Decimal("1.5")


def test_ac_30_find_anomalies_flags_outlier(conn):
    amounts = [100, 110, 120, 130, 140, 150, 160, 5000]
    run(conn, [f"2026-03-{i + 1:02d},UPI/SYN{i}/SWIGGY/OKAXIS/o{i},-{a}.00" for i, a in enumerate(amounts)])
    found = find_anomalies(conn)
    assert len(found) == 1
    a = found[0]
    assert (a.category, a.amount_minor, a.median_minor) == ("Food & Dining", -500_000, Decimal(13500))
    assert round(a.score, 3) == 164.072


def test_ac_31_min_sample_credits_and_transfers(conn):
    seven = [100, 110, 120, 130, 140, 150, 9000]
    run(conn, [f"2026-03-{i + 1:02d},UPI/SYN{i}/SWIGGY/OKAXIS/o{i},-{a}.00" for i, a in enumerate(seven)])
    assert find_anomalies(conn) == []  # only 7 debits in Food & Dining

    transfers = [100, 100, 110, 120, 130, 140, 150, 99000]
    run(conn, [f"2026-04-{i + 1:02d},IMPS SELF TRANSFER T{i},-{a}.00" for i, a in enumerate(transfers)])
    salaries = [100, 100, 110, 120, 130, 140, 150, 99000]
    run(conn, [f"2026-05-{i + 1:02d},NEFT SALARY S{i},{a}.00" for i, a in enumerate(salaries)])
    assert find_anomalies(conn) == []


def test_ac_32_mad_zero_uses_mean_ad():
    values = rupees([649] * 9 + [6490])
    scores = robust_scores(values)
    # median 649, MAD 0 -> MeanAD = 5841/10 = 584.1 -> M = 5841 / (1.253314 * 584.1) = 7.9789
    assert round(scores[-1], 3) == 7.979
    assert scores[:9] == [0.0] * 9
    assert robust_scores(rupees([649] * 10)) == [0.0] * 10


def test_ac_32_mad_zero_in_database(conn):
    run(conn, [f"2026-03-{i + 1:02d},UPI-NETFLIX-SYN{i}-SUB,-649.00" for i in range(9)])
    run(conn, ["2026-03-20,UPI-NETFLIX-SYN99-SUB,-6490.00"])
    found = find_anomalies(conn)
    assert [(a.category, a.amount_minor) for a in found] == [("Entertainment", -649_000)]
