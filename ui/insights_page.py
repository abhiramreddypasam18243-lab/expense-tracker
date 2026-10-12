"""Insights: unusual spends with plain-language explanations, and top merchants."""

import streamlit as st

from expenses import insights
from expenses.anomaly import MIN_COUNT, THRESHOLD, find_anomalies
from expenses.money import format_inr
from ui import charts
from ui.common import esc, eyebrow, get_conn, month_label, nice_date, no_transactions_state, page_header

conn = get_conn()
months = insights.available_months(conn)
month = page_header("Insights", "Spending that stands out, and where most of it goes.", months)

if not month:
    no_transactions_state()
    st.stop()

left, right = st.columns([7, 5])

with left, st.container(border=True, height="stretch"):
    eyebrow("Unusual spending · all time")
    anomalies = find_anomalies(conn)
    if not anomalies:
        st.caption("Nothing unusual found. Every spend is in line with its category.")
    for a in anomalies:
        with st.container(border=True):
            text_col, badge_col = st.columns([4, 1.3], vertical_alignment="center")
            who = f" at {esc(a.merchant)}" if a.merchant else ""
            text_col.html(
                f'<div class="item-title">{format_inr(-a.amount_minor)}{who}</div>'
                f'<div class="item-meta">{format_inr(-a.amount_minor)} vs typical '
                f"{format_inr(int(a.median_minor))} in {esc(a.category)} · {nice_date(a.txn_date)}</div>"
            )
            badge_col.markdown(f":red-badge[{a.times_typical}× typical]")
    with st.expander("How is this calculated?", icon=":material/functions:"):
        st.markdown(
            f"""
For each category with at least **{MIN_COUNT}** spends, every spend gets a **modified z-score**:

`M = 0.6745 × (amount − median) / MAD`, where MAD is the median distance from the median.

Spends with **M > {THRESHOLD}** are shown. Medians ignore a few extreme values, so one huge
purchase can't hide itself the way it can with an average. Transfers and income are never flagged.
"""
        )

with right, st.container(border=True, height="stretch"):
    eyebrow(f"Top merchants · {month_label(month)}")
    summary = insights.monthly_summary(conn, month)
    if summary.top_merchants:
        st.altair_chart(charts.merchant_bars(summary.top_merchants), width="stretch")
        share = sum(total for _, total in summary.top_merchants) * 100 // max(summary.spend_minor, 1)
        st.caption(f"These {len(summary.top_merchants)} merchants are {share}% of this month's spending.")
    else:
        st.caption("No merchant spending recorded this month.")
