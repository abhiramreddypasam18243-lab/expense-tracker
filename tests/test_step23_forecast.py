"""Step 23 — spending forecast and budget early warning (AC-118 to AC-122)."""

from datetime import date

import pytest

from expenses import categorize as cat
from expenses import cli, db, forecast, insights
from expenses.importer import import_csv
from expenses.money import format_inr
from tools.make_synthetic import generate_csv

TODAY = date(2026, 10, 17)


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def hand_made() -> forecast.History:
    return forecast.History(
        spend={
            "2026-01": {"Food": [(5, 1000), (20, 2000)], "Rent": [(3, 20000)]},
            "2026-02": {"Food": [(10, 3000), (25, 1000)], "Rent": [(3, 20000)]},
            "2026-03": {"Food": [(4, 600)], "Rent": [(3, 20000)], "Shopping": [(2, 9000)]},
        }
    )


def synthetic_db(months=9):
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    import_csv(conn, generate_csv(42, months=months), "Bank", "s.csv", fetch_rate=no_network)
    return conn


# --- AC-118 projections ------------------------------------------------------------------------------


def test_ac_118_methods_on_hand_made_data():
    h = hand_made()
    # March, as of day 10 (31 days): Food spent 600.
    assert forecast.project_one(h, "2026-03", 10, "Food", "pace") == 1860  # 600 * 31 / 10
    assert forecast.project_one(h, "2026-03", 10, "Food", "last_month") == 4000  # February's total
    assert forecast.project_one(h, "2026-03", 10, "Food", "smoothing") == 3500  # 0.5*4000 + 0.5*3000
    # rest of month after day 10: Jan 3000-1000 = 2000, Feb 4000-3000 = 1000 -> median 1500
    assert forecast.project_one(h, "2026-03", 10, "Food", "typical_rest") == 2100
    # Rent paid on the 3rd: pace badly over-projects, typical_rest doesn't
    assert forecast.project_one(h, "2026-03", 10, "Rent", "pace") == 62000
    assert forecast.project_one(h, "2026-03", 10, "Rent", "typical_rest") == 20000
    # February has 28 days
    assert forecast.days_in("2026-02") == 28
    assert forecast.project_one(h, "2026-02", 14, "Food", "pace") == 6000  # 3000 * 28 / 14


def test_ac_118_never_below_spent_and_no_future_data():
    h = hand_made()
    for method in forecast.METHODS:  # Shopping: no history, 9000 already spent
        assert forecast.project_one(h, "2026-03", 10, "Shopping", method) >= 9000
    before = {m: forecast.project(h, "2026-03", 10, m) for m in forecast.METHODS}
    h.spend["2026-03"]["Food"].append((25, 50000))  # after the as-of day
    h.spend["2026-04"] = {"Food": [(1, 99999)]}  # a later month
    after = {m: forecast.project(h, "2026-03", 10, m) for m in forecast.METHODS}
    assert before == after


# --- AC-119 backtest ------------------------------------------------------------------------------------


def test_ac_119_backtest_on_synthetic_statement():
    conn = synthetic_db(9)
    h = forecast.load(conn)
    bt = forecast.backtest(h)
    assert bt.months == h.months[forecast.MIN_PRIOR_MONTHS :]  # only months with >= 3 earlier months
    scores = {s.method: s for s in bt.scores}
    assert set(scores) == set(forecast.METHODS)
    assert all(set(s.wape_by_day) == set(forecast.CHECKPOINTS) for s in bt.scores)
    assert bt.best == min(bt.scores, key=lambda s: s.wape).method
    assert scores["typical_rest"].wape_by_day[7] < scores["pace"].wape_by_day[7]  # rent early in the month
    assert forecast.backtest(h, before="2026-06").months == ["2026-04", "2026-05"]
    conn.close()


# --- AC-120 budget warnings ------------------------------------------------------------------------------


def test_ac_120_budget_check():
    status, safe, msg = forecast.budget_check("Food & Dining", 800000, 900000, 700000, 10)
    assert (status, safe) == ("over", 0) and "already" in msg and format_inr(100000) in msg

    status, safe, msg = forecast.budget_check("Food & Dining", 300000, 820000, 700000, 10)
    assert (status, safe) == ("at_risk", 40000)
    assert msg == (
        f"At this pace Food & Dining will reach {format_inr(820000)} — "
        f"17% over its {format_inr(700000)} budget."
    )

    status, safe, _ = forecast.budget_check("Food & Dining", 300000, 650000, 700000, 0)
    assert (status, safe) == ("on_track", None)  # last day: no days left


def test_ac_120_forecast_with_budgets_and_replay():
    conn = synthetic_db(9)
    food = cat.category_id(conn, "Food & Dining")
    rent = cat.category_id(conn, "Rent")
    insights.set_budget(conn, food, 100)  # ₹1: certainly over
    insights.set_budget(conn, rent, 10_000_000)  # ₹1,00,000: on track
    f = forecast.forecast(conn, "2026-08", date(2026, 8, 15), today=TODAY)
    assert f.replay and f.actual_total is not None and f.day == 15 and f.days == 31
    by_name = {c.name: c for c in f.categories}
    assert by_name["Food & Dining"].status == "over" and by_name["Rent"].status == "on_track"
    assert [c.name for c in f.warnings] == ["Food & Dining"]
    assert f.cumulative[-1] == (31, f.actual_total)  # replay: the whole month is drawn
    assert f.spent_total == sum(c.spent for c in f.categories)
    conn.close()


# --- AC-121 little or no history ---------------------------------------------------------------------------


def test_ac_121_fallbacks():
    conn = synthetic_db(2)
    h = forecast.load(conn)
    assert forecast.backtest(h) is None
    assert forecast.choose_method(h, "2026-02")[0] == "typical_rest"
    assert forecast.choose_method(h, "2026-01")[0] == "pace"
    f = forecast.forecast(conn, "2026-02", date(2026, 2, 10), today=TODAY)
    assert f.method == "typical_rest" and "not enough history" in f.reason and f.backtest is None
    conn.close()

    empty = db.connect(":memory:")
    cat.seed_defaults(empty)
    assert forecast.forecast(empty, today=TODAY) is None
    with pytest.raises(ValueError):
        forecast.forecast(synthetic_db(2), "2026-02", date(2026, 3, 1), today=TODAY)  # as-of outside month
    empty.close()


# --- AC-122 CLI -----------------------------------------------------------------------------------


def test_ac_122_cli_forecast(tmp_path, capsys):
    path = tmp_path / "f.db"
    csv_path = tmp_path / "s.csv"
    csv_path.write_text(generate_csv(42, months=6), encoding="utf-8")
    assert cli.main(["--db", str(path), "import", str(csv_path), "--account", "Bank"]) == 0
    capsys.readouterr()
    assert cli.main(["--db", str(path), "forecast", "--month", "2026-05", "--as-of", "2026-05-10"]) == 0
    out = capsys.readouterr().out
    assert "Forecast for 2026-05, as of 10 May 2026" in out and "replay" in out
    assert "Method:" in out and "Backtest on" in out and "projected" in out
    assert cli.main(["--db", str(path), "forecast", "--as-of", "2026-13-40"]) == 1
    assert cli.main(["--db", str(path), "forecast", "--month", "2026-05", "--as-of", "2026-06-01"]) == 1


# --- AC-123 Dashboard warning only for the month in progress ---------------------------------------------


def test_ac_123_dashboard_warning_only_this_month(tmp_path, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "now.db"
    monkeypatch.setenv("EXPENSES_DB", str(path))
    monkeypatch.setenv("EXPENSES_DEMO", "0")
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_TLS_PROXY", "EXPENSES_AI_URL"):
        monkeypatch.delenv(name, raising=False)

    this_month = date.today().replace(day=1)
    conn = db.connect(path)
    cat.seed_defaults(conn)
    import_csv(
        conn,
        f"Date,Description,Amount\n{this_month.isoformat()},UPI/R1/SWIGGY/OKAXIS/x,-900.00\n",
        "Bank",
        "now.csv",
        fetch_rate=no_network,
    )
    insights.set_budget(conn, cat.category_id(conn, "Food & Dining"), 10_000)  # ₹100: already over
    conn.close()

    at = AppTest.from_file(str(root / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("over its" in w.value for w in at.warning)  # this month: the early warning shows

    conn = db.connect(path)  # move the spend to last year: no month in progress any more
    conn.execute("UPDATE transactions SET txn_date = '2025-01-05'")
    conn.commit()
    conn.close()
    at = AppTest.from_file(str(root / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert not any("over its" in w.value for w in at.warning)
