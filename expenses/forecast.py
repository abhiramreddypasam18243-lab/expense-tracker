"""Spending forecast and budget early warning (spec section 6.4).

Projects each expense category's month-end total from what has been spent up to an "as of"
day, with four methods, and picks the method that was most accurate on the user's own past
months (backtest, WAPE). Only data dated on or before the as-of date is used for a projection.
All money is integer paise.
"""

from __future__ import annotations

import calendar
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from expenses import db
from expenses.insights import parse_month, shift_month
from expenses.money import format_inr

METHODS = ("pace", "last_month", "smoothing", "typical_rest")
METHOD_LABELS = {
    "pace": "Pace (spent so far, scaled to the month)",
    "last_month": "Same as last month",
    "smoothing": "Smoothed average of past months",
    "typical_rest": "Spent so far + your usual rest-of-month spend",
}
LOOKBACK = 6  # earlier months used by smoothing / typical_rest
ALPHA = 0.5  # exponential smoothing weight of the most recent month
CHECKPOINTS = (7, 14, 21)
MIN_PRIOR_MONTHS = 3  # a month is backtested only with at least this many earlier months


# --- history ------------------------------------------------------------------------------------------


@dataclass
class History:
    """Spend (positive paise) per month and category, as (day, amount) pairs."""

    spend: dict[str, dict[str, list[tuple[int, int]]]] = field(default_factory=dict)
    colors: dict[str, str] = field(default_factory=dict)

    @property
    def months(self) -> list[str]:
        return sorted(self.spend)

    def spent_through(self, month: str, category: str, day: int) -> int:
        return sum(a for d, a in self.spend.get(month, {}).get(category, ()) if d <= day)

    def total(self, month: str, category: str) -> int:
        return sum(a for _, a in self.spend.get(month, {}).get(category, ()))

    def categories(self, months: list[str]) -> set[str]:
        return {c for m in months for c in self.spend.get(m, {})}


def load(conn: sqlite3.Connection) -> History:
    hist = History()
    spend: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for row in db.non_transfer_debits(conn):
        month, day = row["txn_date"][:7], int(row["txn_date"][8:10])
        spend[month][row["category_name"]].append((day, -row["amount_minor"]))
        hist.colors[row["category_name"]] = row["category_color"]
    hist.spend = {m: dict(c) for m, c in spend.items()}
    return hist


def days_in(month: str) -> int:
    year, mon = parse_month(month)
    return calendar.monthrange(year, mon)[1]


def _prior_months(hist: History, month: str) -> list[str]:
    """Up to LOOKBACK earlier months that have data, oldest first."""
    earlier = [shift_month(month, -k) for k in range(LOOKBACK, 0, -1)]
    return [m for m in earlier if m in hist.spend]


# --- projections -------------------------------------------------------------------------------------


def _round_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def project_one(hist: History, month: str, day: int, category: str, method: str) -> int:
    """Projected month-end spend for one category, using only data up to `day` of `month`."""
    days = days_in(month)
    day = min(max(day, 1), days)
    spent = hist.spent_through(month, category, day)
    prior = _prior_months(hist, month)
    if method == "pace":
        value = _round_div(spent * days, day)
    elif method == "last_month":
        last = shift_month(month, -1)
        value = hist.total(last, category) if last in hist.spend else spent
    elif method == "smoothing":
        if not prior:
            value = spent
        else:
            level = float(hist.total(prior[0], category))
            for m in prior[1:]:
                level = ALPHA * hist.total(m, category) + (1 - ALPHA) * level
            value = round(level)
    elif method == "typical_rest":
        if not prior:
            value = _round_div(spent * days, day)
        else:
            rests = [
                hist.total(m, category) - hist.spent_through(m, category, min(day, days_in(m))) for m in prior
            ]
            value = spent + round(statistics.median(rests))
    else:
        raise ValueError(f"unknown method {method!r}")
    return max(value, spent)  # never below what is already spent


def project(hist: History, month: str, day: int, method: str) -> dict[str, int]:
    """{category: projected month-end spend} for every category seen this month or in the lookback."""
    cats = hist.categories([month, *_prior_months(hist, month)])
    return {c: project_one(hist, month, day, c, method) for c in sorted(cats)}


# --- backtest ------------------------------------------------------------------------------------------


@dataclass
class MethodScore:
    method: str
    wape: float  # over all categories, months and checkpoints
    wape_by_day: dict[int, float]
    total_wape: float  # error of the month's total spend


@dataclass
class Backtest:
    months: list[str]
    scores: list[MethodScore]  # best first

    @property
    def best(self) -> str:
        return self.scores[0].method


def backtest(hist: History, before: str | None = None) -> Backtest | None:
    """Score every method on past months; None with fewer than MIN_PRIOR_MONTHS + 1 months.

    `before`: only months strictly before this one are tested (a month in progress has no actual yet).
    """
    data_months = hist.months
    tested = [
        m for i, m in enumerate(data_months) if i >= MIN_PRIOR_MONTHS and (before is None or m < before)
    ]
    if not tested:
        return None
    scores = []
    for method in METHODS:
        err = {d: 0 for d in CHECKPOINTS}
        actual_sum = {d: 0 for d in CHECKPOINTS}
        total_err = total_actual = 0
        for month in tested:
            cats = hist.categories([month, *_prior_months(hist, month)])
            for d in CHECKPOINTS:
                projected_total = actual_total = 0
                for c in cats:
                    projected = project_one(hist, month, d, c, method)
                    actual = hist.total(month, c)
                    err[d] += abs(projected - actual)
                    actual_sum[d] += actual
                    projected_total += projected
                    actual_total += actual
                total_err += abs(projected_total - actual_total)
                total_actual += actual_total
        by_day = {d: err[d] / actual_sum[d] if actual_sum[d] else 0.0 for d in CHECKPOINTS}
        overall = sum(err.values()) / sum(actual_sum.values()) if sum(actual_sum.values()) else 0.0
        scores.append(MethodScore(method, overall, by_day, total_err / total_actual if total_actual else 0.0))
    scores.sort(key=lambda s: (s.wape, METHODS.index(s.method)))
    return Backtest(tested, scores)


def choose_method(hist: History, month: str) -> tuple[str, str, Backtest | None]:
    """(method, reason, backtest): the lowest-WAPE method on the user's own past months."""
    bt = backtest(hist, before=month)
    if bt is not None:
        n = len(bt.months)
        return bt.best, f"most accurate on your last {n} month{'s' if n != 1 else ''} (backtest)", bt
    if _prior_months(hist, month):
        return "typical_rest", "not enough history to backtest yet (needs 4 months)", None
    return "pace", "no earlier months yet", None


# --- forecast for the app -----------------------------------------------------------------------------


@dataclass
class CategoryForecast:
    name: str
    color: str
    spent: int
    projected: int
    actual: int | None = None  # replay only: what really happened
    budget: int | None = None
    status: str | None = None  # 'over' | 'at_risk' | 'on_track' (budgeted categories)
    safe_per_day: int | None = None
    message: str | None = None


@dataclass
class Forecast:
    month: str
    as_of: date
    day: int
    days: int
    replay: bool
    method: str
    reason: str
    backtest: Backtest | None
    categories: list[CategoryForecast]
    spent_total: int
    projected_total: int
    last_month_total: int
    actual_total: int | None
    cumulative: list[tuple[int, int]]  # (day, cumulative spend) up to as_of (whole month in replay)

    @property
    def warnings(self) -> list[CategoryForecast]:
        return [c for c in self.categories if c.status in ("over", "at_risk")]


def budget_check(
    name: str, spent: int, projected: int, limit: int, days_left: int
) -> tuple[str, int | None, str]:
    """(status, safe to spend per day, sentence)."""
    safe = max(0, limit - spent) // days_left if days_left > 0 else None
    if spent > limit:
        return (
            "over",
            safe,
            f"{name} is already {format_inr(spent - limit)} over its {format_inr(limit)} budget.",
        )
    if projected > limit:
        over = _round_div((projected - limit) * 100, limit)
        return (
            "at_risk",
            safe,
            f"At this pace {name} will reach {format_inr(projected)} — "
            f"{over}% over its {format_inr(limit)} budget.",
        )
    return "on_track", safe, f"{name} is on track: about {format_inr(projected)} of {format_inr(limit)}."


def forecast(
    conn: sqlite3.Connection, month: str | None = None, as_of: date | None = None, today: date | None = None
) -> Forecast | None:
    """The month's forecast as of a day; None when there is no data at all.

    Defaults: the latest month with data; as of today in the current month, else the 15th (replay).
    """
    today = today or date.today()
    hist = load(conn)
    if not hist.spend and month is None:
        return None
    current = today.strftime("%Y-%m")
    month = month or (current if current in hist.spend else (hist.months[-1] if hist.months else current))
    days = days_in(month)
    if as_of is None:
        as_of = today if month == current else date(*parse_month(month), min(15, days))
    if as_of.strftime("%Y-%m") != month:
        raise ValueError("the as-of date must be in the forecast month")
    replay = month < current
    day = as_of.day

    method, reason, bt = choose_method(hist, month)
    projected = project(hist, month, day, method)
    budgets = {b["name"]: b["monthly_limit_minor"] for b in db.list_budgets(conn)}
    rows = []
    for name in sorted(set(projected) | set(budgets), key=lambda n: (-projected.get(n, 0), n)):
        spent = hist.spent_through(month, name, day)
        cf = CategoryForecast(
            name=name,
            color=hist.colors.get(name, "#9CA3AF"),
            spent=spent,
            projected=projected.get(name, spent),
            actual=hist.total(month, name) if replay else None,
        )
        if name in budgets:
            cf.budget = budgets[name]
            cf.status, cf.safe_per_day, cf.message = budget_check(
                name, spent, cf.projected, budgets[name], days - day
            )
        if cf.projected or cf.spent or cf.budget:
            rows.append(cf)

    daily = defaultdict(int)
    last_day = days if replay else day
    for cats in hist.spend.get(month, {}).values():
        for d, amount in cats:
            if d <= last_day:
                daily[d] += amount
    running, cumulative = 0, []
    for d in range(1, last_day + 1):
        running += daily[d]
        cumulative.append((d, running))

    last = shift_month(month, -1)
    return Forecast(
        month=month,
        as_of=as_of,
        day=day,
        days=days,
        replay=replay,
        method=method,
        reason=reason,
        backtest=bt,
        categories=rows,
        spent_total=sum(c.spent for c in rows),
        projected_total=sum(c.projected for c in rows),
        last_month_total=sum(hist.total(last, c) for c in hist.categories([last])),
        actual_total=sum(hist.total(month, c) for c in hist.categories([month])) if replay else None,
        cumulative=cumulative,
    )
