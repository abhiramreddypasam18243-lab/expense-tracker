"""Insights: unusual spends with plain-language explanations, top merchants, recurring payments."""

import streamlit as st

from expenses import insights, recurring
from expenses.anomaly import MIN_COUNT, MIN_MULTIPLE, THRESHOLD, find_anomalies
from expenses.money import format_inr
from ui import charts, theme
from ui.common import card, eyebrow, get_conn, month_label, nice_date, no_transactions_state, page_header
from ui.components import list_row

conn = get_conn()
mode = theme.current()
months = insights.available_months(conn)
month = page_header(
    "Insights", "Spending that stands out, and where most of it goes.", months, eyebrow_text="Analysis"
)

if not month:
    no_transactions_state()
    st.stop()

left, right = st.columns([7, 5])

with left, card("unusual", height="stretch"):
    eyebrow("Unusual spending", "alert", "All time — far above what's typical for the category.")
    anomalies = find_anomalies(conn)
    if not anomalies:
        st.caption("Nothing unusual found. Every spend is in line with its category.")
    else:
        st.html(
            "".join(
                list_row(
                    f"{format_inr(-a.amount_minor)}{' at ' + a.merchant if a.merchant else ''}",
                    f"vs typical {format_inr(int(a.median_minor))} in {a.category} · {nice_date(a.txn_date)}",
                    f'<span class="pill bad">{a.times_typical}× typical</span>',
                    "alert",
                    "#EF4444",
                )
                for a in anomalies
            )
        )
    with st.expander("How is this calculated?", icon=":material/functions:"):
        st.markdown(
            f"""
For each category with at least **{MIN_COUNT}** spends, every spend gets a **modified z-score**:

`M = 0.6745 × (amount − median) / MAD`, where MAD is the median distance from the median.

Spends with **M > {THRESHOLD}** that are also at least **{MIN_MULTIPLE}× the typical amount** are shown.
Medians ignore a few extreme values, so one huge purchase can't hide itself the way it can with an average.
Transfers and income are never flagged.
"""
        )

with right, card("merchants", height="stretch"):
    summary = insights.monthly_summary(conn, month)
    eyebrow("Top merchants", "store", month_label(month))
    if summary.top_merchants:
        st.altair_chart(charts.merchant_bars(summary.top_merchants, mode), width="stretch")
        share = sum(total for _, total in summary.top_merchants) * 100 // max(summary.spend_minor, 1)
        st.caption(f"These {len(summary.top_merchants)} merchants are {share}% of this month's spending.")
    else:
        st.caption("No merchant spending recorded this month.")

# --- recurring payments (spec 6.4) -------------------------------------------------------------------
with card("recurring"):
    eyebrow("Recurring payments", "repeat", "Detected from 3+ similar payments, 20–40 days apart.")
    found = recurring.find_recurring(conn)
    active = [r for r in found if r.active]
    if not found:
        st.caption("Recurring payments: none found yet. They appear after 3 similar monthly payments.")
    else:
        per_month = sum(int(r.typical_minor) for r in active)
        st.caption(
            f"Recurring payments: **{format_inr(per_month)}** per month across {len(active)} active "
            "subscription(s) and bill(s)."
        )
        st.html(
            "".join(
                list_row(
                    r.name,
                    f"{r.category} · {r.count} payments · every ~{r.interval_days} days · "
                    f"{'next' if r.active else 'last expected'} {nice_date(r.next_date.isoformat())}",
                    f'<div class="item-title">{format_inr(int(r.typical_minor))}</div>'
                    + (
                        '<span class="pill good">Active</span>'
                        if r.active
                        else '<span class="pill muted">Stopped?</span>'
                    ),
                    "repeat",
                    theme.category_color(r.category, mode)
                    if r.category in theme.CATEGORY_PALETTE
                    else "#6366F1",
                )
                for r in found
            )
        )
