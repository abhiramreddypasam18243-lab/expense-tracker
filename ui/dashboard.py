"""Dashboard: month story, KPI cards with sparklines, category donut, trend, budgets, alerts, recurring."""

from datetime import date

import streamlit as st

from expenses import forecast, insights, manual, recurring
from expenses.anomaly import find_anomalies
from expenses.money import format_inr
from ui import charts, theme
from ui.common import card, esc, eyebrow, get_conn, month_label, nice_date, no_transactions_state, page_header
from ui.components import Kpi, kpi_grid, list_row, story

conn = get_conn()
mode = theme.current()
months = insights.available_months(conn)
month = page_header("Dashboard", "Where your money went, at a glance.", months, eyebrow_text="Overview")

if not month:
    no_transactions_state()
    st.stop()

summary = insights.monthly_summary(conn, month)
previous_month = insights.shift_month(month, -1)
previous = insights.monthly_summary(conn, previous_month)
history = [
    insights.monthly_summary(conn, insights.shift_month(month, -i)) for i in range(5, -1, -1)
]  # 6 months


# --- story ----------------------------------------------------------------------------------------
def change(now: int, before: int) -> tuple[str, str]:
    """('▲ 12%', direction) — percent display only."""
    if before == 0:
        return "", "flat"
    pct = (now - before) * 100 // abs(before)
    return (f"▲ {pct}%" if pct > 0 else f"▼ {abs(pct)}%" if pct < 0 else "0%"), (
        "up" if pct > 0 else "down" if pct < 0 else "flat"
    )


spend_delta, spend_dir = change(summary.spend_minor, previous.spend_minor)
sentence = f"You spent <b>{format_inr(summary.spend_minor)}</b> in {month_label(month)}"
if previous.txn_count and spend_dir != "flat":
    word = "more" if spend_dir == "up" else "less"
    sentence += f" — <b>{spend_delta.split()[-1]} {word}</b> than {month_label(previous_month, short=True)}"
sentence += "."
if summary.by_category:
    top = summary.by_category[0]
    sentence += (
        f" <b>{esc(top.name)}</b> took the biggest share "
        f"({top.spend_minor * 100 // max(summary.spend_minor, 1)}%)."
    )
if summary.net_minor >= 0:
    sentence += f" You kept <b>{format_inr(summary.net_minor)}</b>."
st.html(story(f"Your month · {month_label(month)}", sentence))

# Budget early warning (6.4): only for the month in progress.
if month == date.today().strftime("%Y-%m"):
    ahead = forecast.forecast(conn, month)
    if ahead and ahead.warnings:
        with st.container(key="forecast-warning"):
            st.warning(" ".join(c.message for c in ahead.warnings[:2]), icon=":material/trending_up:")
            st.page_link("ui/forecast_page.py", label="See the forecast", icon=":material/arrow_forward:")

# Cash wallet (I13): ATM withdrawals aren't spending, so say how much of that cash isn't logged yet.
cash = manual.cash_gap(conn, month)
if cash.unlogged_minor:
    with st.container(key="cash-gap"):
        st.info(
            f"You withdrew **{format_inr(cash.withdrawn_minor)}** in cash in {month_label(month)}; "
            f"**{format_inr(cash.unlogged_minor)}** of it isn't logged yet, so it isn't in your spending.",
            icon=":material/payments:",
        )
        st.page_link("ui/transactions.py", label="Log cash spending", icon=":material/add:")

# --- KPI cards ------------------------------------------------------------------------------------
vs = f"vs {month_label(previous_month, short=True)}" if previous.txn_count else ""


def tone(direction: str, higher_is_good: bool) -> str:
    if direction == "flat":
        return "flat"
    return "good" if (direction == "up") == higher_is_good else "bad"


inc_delta, inc_dir = change(summary.income_minor, previous.income_minor)
net_delta, net_dir = change(summary.net_minor, previous.net_minor)
cnt_delta, cnt_dir = change(summary.txn_count, previous.txn_count)
st.html(
    kpi_grid(
        [
            Kpi(
                "Spend",
                format_inr(summary.spend_minor),
                "spend",
                "#EF4444",
                [h.spend_minor for h in history],
                spend_delta or None,
                tone(spend_dir, higher_is_good=False),
                vs,
            ),
            Kpi(
                "Income",
                format_inr(summary.income_minor),
                "income",
                "#16A34A",
                [h.income_minor for h in history],
                inc_delta or None,
                tone(inc_dir, higher_is_good=True),
                vs,
            ),
            Kpi(
                "Net",
                format_inr(summary.net_minor),
                "net",
                "#6366F1",
                [h.net_minor for h in history],
                net_delta or None,
                tone(net_dir, higher_is_good=True),
                vs,
            ),
            Kpi(
                "Transactions",
                f"{summary.txn_count}",
                "count",
                "#8B5CF6",
                [h.txn_count for h in history],
                cnt_delta or None,
                "flat",
                vs,
            ),
        ]
    )
)

# --- charts -------------------------------------------------------------------------------------------
left, right = st.columns([5, 6])
with left, card("donut", height="stretch"):
    eyebrow("Spend by category", "pie", "Main categories in colour; the rest grouped as Other.")
    if summary.by_category:
        rows = charts.donut_rows(summary.by_category, mode)
        # Donut on top, labelled legend underneath at full width (names and amounts never cut off).
        st.altair_chart(charts.category_donut(rows, summary.spend_minor, mode), width="stretch")
        legend = "".join(
            f'<div class="legend-row"><span class="dot" style="background:{r.color}"></span>'
            f'<span class="name">{esc(r.name)}</span><span class="amt">{format_inr(r.spend_minor)}</span>'
            f'<span class="pct">{r.spend_minor * 100 // summary.spend_minor}%</span></div>'
            for r in rows
        )
        note = (
            f'<div class="legend-note">Other: {esc(", ".join(rows[-1].parts))}</div>'
            if rows[-1].parts
            else ""
        )
        st.html(f'<div class="legend-grid">{legend}</div>{note}')
    else:
        st.caption("No spending this month.")

with right, card("trend", height="stretch"):
    eyebrow("Last 12 months", "bars", "Total spend per month; the selected month is highlighted.")
    st.altair_chart(charts.spend_trend(insights.spending_trend(conn, month), month, mode), width="stretch")

# --- budgets, unusual spending, recurring -------------------------------------------------------------
c1, c2, c3 = st.columns(3)
with c1, card("budgets", height="stretch"):
    eyebrow("Budgets", "target", month_label(month))
    budgets = insights.budget_status(conn, month)
    if not budgets:
        st.caption("No budgets yet. Set monthly limits per category to track them here.")
        st.page_link("ui/settings.py", label="Set budgets", icon=":material/tune:")
    else:
        label = {"ok": "On track", "warning": "Close to limit", "over": "Over budget"}
        st.html(
            "".join(
                f'<div class="budget"><div class="budget-head"><span class="item-title">{esc(b.name)}</span>'
                f'<span class="pill {b.status}">{label[b.status]}</span></div>'
                f'<div class="track"><div class="fill {b.status}" style="width:{min(b.percent, 100)}%"></div></div>'
                f'<div class="item-meta">{format_inr(b.spent_minor)} of {format_inr(b.limit_minor)} · {b.percent}%</div>'
                "</div>"
                for b in budgets
            )
        )

with c2, card("unusual", height="stretch"):
    eyebrow("Unusual spending", "alert", "Far above what's typical for the category.")
    anomalies = find_anomalies(conn)[:3]
    if not anomalies:
        st.caption("Nothing unusual — every spend is in line with its category.")
    else:
        st.html(
            "".join(
                list_row(
                    format_inr(-a.amount_minor),
                    f"{a.merchant + ' · ' if a.merchant else ''}{a.category} · {nice_date(a.txn_date)}",
                    f'<span class="pill bad">{a.times_typical}× typical</span>',
                    "alert",
                    "#EF4444",
                )
                for a in anomalies
            )
        )
        st.page_link("ui/insights_page.py", label="See all insights", icon=":material/arrow_forward:")

with c3, card("recurring", height="stretch"):
    eyebrow("Recurring payments", "repeat", "Subscriptions and fixed bills.")
    active = [r for r in recurring.find_recurring(conn) if r.active][:4]
    if not active:
        st.caption("None detected yet — they appear after 3 similar monthly payments.")
    else:
        total = sum(int(r.typical_minor) for r in active)
        st.html(
            f'<div class="item-meta" style="margin-bottom:.2rem">About <b>{format_inr(total)}</b> a month</div>'
            + "".join(
                list_row(
                    r.name,
                    f"{r.category} · next {nice_date(r.next_date.isoformat())}",
                    f'<span class="item-title">{format_inr(int(r.typical_minor))}</span>',
                    "repeat",
                    theme.category_color(r.category, mode)
                    if r.category in theme.CATEGORY_PALETTE
                    else "#6366F1",
                )
                for r in active
            )
        )
