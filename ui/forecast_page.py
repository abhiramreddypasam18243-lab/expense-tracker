"""Forecast: month-end projection per category, budget early warnings, and how accurate it has been."""

from datetime import date

import pandas as pd
import streamlit as st

from expenses import forecast, insights
from expenses.money import format_inr
from ui import charts, theme
from ui.common import card, eyebrow, get_conn, month_label, no_transactions_state, page_header
from ui.components import list_row

conn = get_conn()
mode = theme.current()
today = date.today()
months = insights.available_months(conn)
month = page_header(
    "Forecast",
    "Where this month is heading, and which budgets need attention — before it's too late.",
    months,
    eyebrow_text="Planning",
)

if not month:
    no_transactions_state()
    st.stop()

days = forecast.days_in(month)
current = month == today.strftime("%Y-%m")
if current:
    day = today.day
else:
    day = st.slider(
        "Replay: pretend today is day…",
        min_value=1,
        max_value=days,
        value=min(15, days),
        key=f"forecast-day-{month}",
        help="See what the forecast would have said on that day, next to what really happened.",
    )
    st.info(
        f"**Replay of {month_label(month)}** — the forecast uses only what was known on day {day}, "
        "so you can compare it with what really happened.",
        icon=":material/replay:",
    )
result = forecast.forecast(conn, month, date(*insights.parse_month(month), day))

# --- headline ---------------------------------------------------------------------------------------------
with card("forecast-kpis"):
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(f"Spent by day {result.day}", format_inr(result.spent_total))
    change = result.projected_total - result.last_month_total
    k2.metric(
        "Projected month total",
        format_inr(result.projected_total),
        delta=f"{'+' if change >= 0 else '-'}{format_inr(abs(change))} vs last month"
        if result.last_month_total
        else None,
        delta_color="inverse",  # spending more than last month is the bad direction
    )
    if result.actual_total is not None:
        miss = result.actual_total - result.projected_total
        k3.metric(
            "What really happened",
            format_inr(result.actual_total),
            delta=f"{'+' if miss >= 0 else '-'}{format_inr(abs(miss))} vs forecast",
            delta_color="off",
        )
    else:
        k3.metric("Days left", result.days - result.day)
    k4.metric("Budgets needing attention", len(result.warnings))
    st.caption(f"Method: **{forecast.METHOD_LABELS[result.method]}** — {result.reason}.")

left, right = st.columns([7, 5])
with left, card("forecast-chart", height="stretch"):
    eyebrow("This month so far", "bars", f"{month_label(month)} · cumulative spending, day by day")
    st.altair_chart(
        charts.forecast_line(result.cumulative, result.day, result.days, result.projected_total, mode),
        width="stretch",
    )

with right, card("forecast-budgets", height="stretch"):
    eyebrow("Budget early warning", "target", "Projected against each monthly budget")
    budgeted = [c for c in result.categories if c.budget]
    if not budgeted:
        st.caption("No budgets yet. Set them in **Settings → Budgets** to get early warnings here.")
    else:
        pills = {
            "over": '<span class="pill over">Over</span>',
            "at_risk": '<span class="pill warning">At risk</span>',
            "on_track": '<span class="pill good">On track</span>',
        }
        order = {"over": 0, "at_risk": 1, "on_track": 2}
        st.html(
            "".join(
                list_row(
                    c.message,
                    (
                        f"Spent {format_inr(c.spent)} of {format_inr(c.budget)}"
                        + (
                            f" · safe to spend {format_inr(c.safe_per_day)}/day"
                            if c.safe_per_day is not None
                            else ""
                        )
                    ),
                    pills[c.status],
                    "alert" if c.status != "on_track" else "check",
                    "#EF4444" if c.status == "over" else ("#F59E0B" if c.status == "at_risk" else "#16A34A"),
                )
                for c in sorted(budgeted, key=lambda c: (order[c.status], c.name))
            )
        )

# --- per category -----------------------------------------------------------------------------------------
with card("forecast-table"):
    eyebrow("By category", "store", "Spent so far and where each category is heading")
    status_text = {"over": "Over budget", "at_risk": "At risk", "on_track": "On track", None: ""}
    table = {
        "Category": [c.name for c in result.categories],
        "Spent so far": [format_inr(c.spent) for c in result.categories],
        "Projected": [format_inr(c.projected) for c in result.categories],
    }
    if result.replay:
        table["Actually spent"] = [format_inr(c.actual or 0) for c in result.categories]
    table["Budget"] = [format_inr(c.budget) if c.budget else "—" for c in result.categories]
    table["Status"] = [status_text[c.status] for c in result.categories]
    table["Safe per day"] = [
        format_inr(c.safe_per_day) if c.safe_per_day is not None and c.budget else "—"
        for c in result.categories
    ]
    st.dataframe(
        pd.DataFrame(table),
        hide_index=True,
        width="stretch",
        column_config={
            name: st.column_config.TextColumn(alignment="right")
            for name in ("Spent so far", "Projected", "Actually spent", "Budget", "Safe per day")
        },
    )

# --- accuracy ---------------------------------------------------------------------------------------------
with card("forecast-accuracy"):
    eyebrow("How accurate has it been?", "history", "Backtest on your own past months")
    bt = result.backtest
    if bt is None:
        st.caption(
            "Not enough history yet: the backtest needs at least 4 months of transactions. "
            f"Until then the forecast uses **{forecast.METHOD_LABELS[result.method]}**."
        )
    else:
        st.caption(
            f"For each of your last {len(bt.months)} complete months, every method pretended to be on day "
            f"{', '.join(str(d) for d in forecast.CHECKPOINTS)} and projected the month; then it was compared "
            "with what really happened. Lower error is better. The best one is used."
        )
        st.dataframe(
            pd.DataFrame(
                {
                    "Method": [
                        forecast.METHOD_LABELS[s.method] + ("  ✓ used" if s.method == result.method else "")
                        for s in bt.scores
                    ],
                    "Error": [s.wape for s in bt.scores],
                    **{f"Day {d}": [s.wape_by_day[d] for s in bt.scores] for d in forecast.CHECKPOINTS},
                    "Monthly total error": [s.total_wape for s in bt.scores],
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Error": st.column_config.NumberColumn(
                    format="percent", help="WAPE across categories and days"
                ),
                **{f"Day {d}": st.column_config.NumberColumn(format="percent") for d in forecast.CHECKPOINTS},
                "Monthly total error": st.column_config.NumberColumn(format="percent"),
            },
        )
    with st.expander("How is this calculated?", icon=":material/functions:"):
        st.markdown(
            """
- **Pace** — spent so far × days in month ÷ days passed.
- **Same as last month** — last month's total for the category.
- **Smoothed average** — exponential smoothing (α = 0.5) of the previous 6 months.
- **Spent so far + usual rest-of-month** — adds the median of what you spent *after this day* in the previous 6 months,
  so a rent paid on the 3rd isn't multiplied by 10.

A projection is never below what's already spent. Error is **WAPE** = Σ|projected − actual| ÷ Σ actual.
One-off big purchases can't be predicted by any method — they show up in a replay as a miss.
"""
        )
