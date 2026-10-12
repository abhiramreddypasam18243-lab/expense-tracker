"""Dashboard: KPIs, category donut, 12-month trend, budgets and top anomalies."""

import streamlit as st

from expenses import insights
from expenses.anomaly import find_anomalies
from expenses.money import format_inr
from ui import charts
from ui.common import esc, eyebrow, get_conn, month_label, nice_date, no_transactions_state, page_header

conn = get_conn()
months = insights.available_months(conn)
month = page_header("Dashboard", "Where your money went, at a glance.", months)

if not month:
    no_transactions_state()
    st.stop()

summary = insights.monthly_summary(conn, month)
previous_month = insights.shift_month(month, -1)
previous = insights.monthly_summary(conn, previous_month)


def delta(now: int, before: int) -> str | None:
    if previous.txn_count == 0:
        return None
    diff = now - before
    sign = "+" if diff > 0 else ""
    return f"{sign}{format_inr(diff)} vs {month_label(previous_month, short=True)}"


# --- KPI row ----------------------------------------------------------------------------------
k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Spend",
    format_inr(summary.spend_minor),
    delta(summary.spend_minor, previous.spend_minor),
    delta_color="inverse",
    border=True,
)
k2.metric(
    "Income",
    format_inr(summary.income_minor),
    delta(summary.income_minor, previous.income_minor),
    border=True,
)
k3.metric("Net", format_inr(summary.net_minor), delta(summary.net_minor, previous.net_minor), border=True)
k4.metric("Transactions", f"{summary.txn_count}", border=True)

# --- charts -------------------------------------------------------------------------------------
left, right = st.columns([5, 6])
with left, st.container(border=True, height="stretch"):
    eyebrow("Spend by category")
    if summary.by_category:
        chart_col, legend_col = st.columns([2, 3], vertical_alignment="center")
        with chart_col:
            st.altair_chart(charts.category_donut(summary.by_category, summary.spend_minor), width="stretch")
        with legend_col:
            rows = "".join(
                f'<div class="legend-row"><span class="dot" style="background:{esc(c.color)}"></span>'
                f'<span class="name">{esc(c.name)}</span><span class="amt">{format_inr(c.spend_minor)}</span>'
                f'<span class="pct">{c.spend_minor * 100 // summary.spend_minor}%</span></div>'
                for c in summary.by_category[:7]
            )
            st.html(rows)
            if len(summary.by_category) > 7:
                st.caption(f"+ {len(summary.by_category) - 7} more categories")
    else:
        st.caption("No spending this month.")

with right, st.container(border=True, height="stretch"):
    eyebrow("Spend over the last 12 months")
    st.altair_chart(charts.spend_trend(insights.spending_trend(conn, month), month), width="stretch")

# --- budgets and anomalies -------------------------------------------------------------------------
left, right = st.columns([5, 6])
with left, st.container(border=True, height="stretch"):
    eyebrow(f"Budgets · {month_label(month)}")
    budgets = insights.budget_status(conn, month)
    if not budgets:
        st.caption("No budgets yet. Set monthly limits per category to track them here.")
        st.page_link("ui/settings.py", label="Set budgets", icon=":material/tune:")
    else:
        status_text = {"ok": "On track", "warning": "Close to limit", "over": "Over budget"}
        st.html(
            "".join(
                f'<div class="budget"><div class="budget-head"><span class="item-title">{esc(b.name)}</span>'
                f'<span class="pill {b.status}">{status_text[b.status]}</span></div>'
                f'<div class="track"><div class="fill {b.status}" style="width:{min(b.percent, 100)}%"></div></div>'
                f'<div class="item-meta">{format_inr(b.spent_minor)} of {format_inr(b.limit_minor)} · {b.percent}%</div>'
                "</div>"
                for b in budgets
            )
        )

with right, st.container(border=True, height="stretch"):
    eyebrow("Unusual spending")
    anomalies = find_anomalies(conn)[:3]
    if not anomalies:
        st.caption("Nothing unusual — every spend is in line with its category.")
    for a in anomalies:
        text_col, badge_col = st.columns([4, 1], vertical_alignment="center")
        with text_col:
            who = f" · {esc(a.merchant)}" if a.merchant else ""
            st.html(
                f'<div class="item-title">{format_inr(-a.amount_minor)}</div>'
                f'<div class="item-meta">{esc(a.category)}{who} · {nice_date(a.txn_date)} · '
                f"typical {format_inr(int(a.median_minor))}</div>"
            )
        badge_col.markdown(f":red-badge[{a.times_typical}× typical]")
    if anomalies:
        st.page_link("ui/insights_page.py", label="See all insights", icon=":material/arrow_forward:")
