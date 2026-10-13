"""Your CA: old vs new regime, tax-relevant transactions, filing dates and checklist (spec 6.5)."""

from datetime import date

import pandas as pd
import streamlit as st

from expenses import ai, auth, ca, tax
from expenses.money import MoneyParseError, format_inr, parse_amount, to_plain
from ui.common import card, esc, get_conn, nice_date, page_header

conn = get_conn()
page_header(
    "Your CA",
    "Tax planning from your own statements: which regime to pick, what to claim, and when to file.",
    eyebrow_text="Tax planning",
)
st.caption(
    "Estimates for planning only — not professional advice. Covers salaried individuals in India for tax years "
    "2025-26 and 2026-27 (the Income-tax Act, 2025 applies from 1 April 2026: same rates, new section numbers). "
    "Surcharge, capital gains and business income are not included."
)

today = date.today()
current = (
    f"{today.year}-{(today.year + 1) % 100:02d}"
    if today.month >= 4
    else f"{today.year - 1}-{today.year % 100:02d}"
)
years = list(tax.TAX_YEARS)
c1, c2, _ = st.columns([1.5, 1, 1.5])
year = c1.selectbox(
    "Tax year",
    years,
    index=years.index(current) if current in years else len(years) - 1,
    format_func=lambda y: f"{y} (1 Apr {y[:4]} – 31 Mar {int(y[:4]) + 1})",
)
age = c2.selectbox("Your age", list(tax.AGE_GROUPS), format_func=tax.AGE_GROUPS.get)
names = tax.SECTIONS[year]

items = ca.detect_tax_items(conn, year)
pre = ca.prefill(items)
errors: list[str] = []


def money_input(label: str, field: str, default: int = 0, help: str | None = None, container=st) -> int:
    text = container.text_input(
        label,
        value=to_plain(default) if default else "",
        key=f"ca-{field}-{year}",
        placeholder="0",
        help=help,
    )
    if not text.strip():
        return 0
    try:
        value = parse_amount(text)
    except MoneyParseError:
        errors.append(f"{label}: enter an amount like 1,50,000 or 150000.50")
        return 0
    if value < 0:
        errors.append(f"{label}: can't be negative")
        return 0
    return value


# --- inputs ---------------------------------------------------------------------------------------
tab_tax, tab_items, tab_dates, tab_ai = st.tabs(
    [
        ":material/calculate: Regime comparison",
        ":material/receipt: Tax-relevant transactions",
        ":material/event: Filing dates & checklist",
        ":material/forum: Ask your CA",
    ]
)

with tab_tax:
    with card("ca-inputs"):
        st.markdown("**Income** for the whole tax year")
        i1, i2 = st.columns(2)
        salary = money_input(
            "Gross salary (₹, from Form 16 / payslips)", "salary", pre["salary_takehome_minor"], container=i1
        )
        other = money_input(
            "Other income (₹, e.g. interest)", "other", pre["savings_interest_minor"], container=i2
        )
        if pre["salary_takehome_minor"]:
            st.caption(
                f"Pre-filled with the **{format_inr(pre['salary_takehome_minor'])}** of salary credited to your "
                "bank so far this tax year. That is take-home pay; replace it with your gross annual salary."
            )

        st.markdown("**Deductions** (old regime only) — pre-filled from your statements where found")
        d1, d2, d3 = st.columns(3)
        s80c = money_input(f"{names['sec80c']} (₹, max 1.5 lakh)", "80c", pre["sec80c_minor"], container=d1)
        s80ccd = money_input(
            f"{names['sec80ccd1b']} NPS (₹, max 50,000)", "80ccd", pre["sec80ccd1b_minor"], container=d2
        )
        s80d = money_input(
            f"{names['sec80d']} self & family (₹)", "80d", pre["sec80d_self_minor"], container=d3
        )
        d4, d5, d6 = st.columns(3)
        s80d_parents = money_input(f"{names['sec80d']} parents (₹)", "80d-parents", container=d4)
        home = money_input(f"Home-loan interest, {names['home_loan']} (₹)", "home", container=d5)
        s80e = money_input(f"Education-loan interest, {names['sec80e']} (₹)", "80e", container=d6)
        d7, d8, d9 = st.columns(3)
        s80g = money_input(
            f"Eligible donations, {names['sec80g']} (₹)", "80g", pre["sec80g_minor"], container=d7
        )
        interest = money_input(
            f"Savings interest, {names['interest']} (₹)", "int", pre["savings_interest_minor"], container=d8
        )
        parents_senior = d9.checkbox("Parents are 60 or older", key=f"ca-parents-senior-{year}")

        with st.expander(f"House rent allowance ({names['hra']})", icon=":material/home:"):
            h1, h2 = st.columns(2)
            hra = money_input("HRA received in salary (₹ a year)", "hra", container=h1)
            basic = money_input("Basic salary + DA (₹ a year)", "basic", container=h2)
            h3, h4 = st.columns(2)
            rent = money_input("Rent paid (₹ a year)", "rent", pre["rent_paid_minor"], container=h3)
            city = h4.selectbox("City", tax.CITIES, index=len(tax.CITIES) - 1, key=f"ca-city-{year}")

    for message in errors:
        st.error(message, icon=":material/error:")

    inputs = tax.TaxInputs(
        tax_year=year,
        age=age,
        salary_minor=salary,
        other_income_minor=other,
        sec80c_minor=s80c,
        sec80ccd1b_minor=s80ccd,
        sec80d_self_minor=s80d,
        sec80d_parents_minor=s80d_parents,
        parents_senior=parents_senior,
        home_loan_interest_minor=home,
        sec80e_minor=s80e,
        sec80g_minor=s80g,
        savings_interest_minor=interest,
        hra_received_minor=hra,
        basic_salary_minor=basic,
        rent_paid_minor=rent,
        city=city,
    )
    result = tax.compare(inputs)

    left, right = st.columns(2)
    for col, res, title, note in (
        (left, result.new, "New regime", f"Default · {names['new_regime']} · few deductions, lower rates"),
        (right, result.old, "Old regime", "Opt in when filing · deductions & HRA allowed"),
    ):
        with col, card(f"ca-{res.regime}", highlight=result.best == res.regime and bool(result.saving_minor)):
            badge = " :green-badge[Recommended]" if result.best == res.regime and result.saving_minor else ""
            st.markdown(f"#### {title}{badge}")
            st.caption(note)
            st.markdown(
                f"<div style='font-size:2rem;font-weight:700'>{format_inr(res.total_tax_minor)}</div>",
                unsafe_allow_html=True,
            )
            st.caption(f"Taxable income {format_inr(res.taxable_minor)}")

    if result.saving_minor:
        better = "New regime" if result.best == "new" else "Old regime"
        st.success(
            f"The **{better}** saves you **{format_inr(result.saving_minor)}** this tax year.",
            icon=":material/savings:",
        )
    else:
        st.info("Both regimes give the same tax for these figures.", icon=":material/balance:")
    if result.best == "old":
        st.caption(
            "The new regime is the default: choose the old regime when you file your return "
            "(and tell your employer at the start of the year so TDS matches)."
        )
    if result.best_result.surcharge_excluded:
        st.warning(
            "Taxable income is above ₹50 lakh: a surcharge applies and is **not** included here.",
            icon=":material/warning:",
        )
    for tip in result.tips:
        st.info(tip.text, icon=":material/lightbulb:")

    with st.expander("How it's calculated", icon=":material/functions:"):

        def column(r: tax.TaxResult) -> list[str]:
            return [
                format_inr(v)
                for v in (
                    r.gross_income_minor,
                    r.standard_deduction_minor,
                    r.hra_exemption_minor,
                    sum(r.deductions.values()),
                    r.taxable_minor,
                    r.slab_tax_minor,
                    r.rebate_minor,
                    r.cess_minor,
                    r.total_tax_minor,
                )
            ]

        st.dataframe(
            pd.DataFrame(
                {
                    "Step": [
                        "Gross income",
                        "Standard deduction",
                        "HRA exemption",
                        "Deductions (capped)",
                        "Taxable income (rounded to ₹10)",
                        "Tax on slabs",
                        f"Rebate {names['rebate']}",
                        "Health & education cess 4%",
                        "Total tax",
                    ],
                    "New regime": column(result.new),
                    "Old regime": column(result.old),
                }
            ),
            hide_index=True,
            width="stretch",
        )

# --- tax-relevant transactions --------------------------------------------------------------------
with tab_items:
    st.caption(
        f"Found in your statements between 1 Apr {year[:4]} and 31 Mar {int(year[:4]) + 1}. "
        "These are hints from bank descriptions — keep the proof documents listed in the checklist."
    )
    if not items:
        st.info(
            "Nothing tax-relevant found in this tax year yet. Import statements covering this period.",
            icon=":material/search_off:",
        )
    for item in items:
        with st.expander(
            f"**{item.label}** · {item.section} · {format_inr(item.total_minor)} "
            f"({len(item.rows)} transaction{'s' if len(item.rows) != 1 else ''})"
        ):
            if item.note:
                st.caption(item.note)
            st.dataframe(
                pd.DataFrame(
                    {
                        "Date": [nice_date(r["txn_date"]) for r in item.rows],
                        "Description": [r["description"] for r in item.rows],
                        "Account": [r["account"] for r in item.rows],
                        "Amount": [format_inr(abs(r["amount_minor"])) for r in item.rows],
                    }
                ),
                hide_index=True,
                width="stretch",
            )

# --- filing dates and checklist -------------------------------------------------------------------
with tab_dates:
    dates_col, check_col = st.columns([3, 2])
    with dates_col, card("ca-dates"):
        st.markdown(f"**Key dates for tax year {year}**")
        badge = {
            "past": ":gray-badge[Past]",
            "next": ":blue-badge[Next]",
            "upcoming": ":violet-badge[Upcoming]",
        }
        for d in ca.filing_calendar(year, today):
            when, what = st.columns([1.3, 4], vertical_alignment="center")
            when.markdown(f"**{nice_date(d.day.isoformat())}**")
            flag = " :red-badge[Don't miss]" if d.highlight else ""
            what.markdown(f"{esc(d.title)} {badge[d.status]}{flag}  \n:small[{esc(d.detail)}]")
        st.caption("Due dates can be extended by the government — check incometax.gov.in before filing.")
    with check_col, card("ca-docs"):
        st.markdown("**Documents to collect**")
        for i, doc in enumerate(ca.checklist(items, year)):
            st.checkbox(doc, key=f"ca-doc-{year}-{i}")

# --- ask your CA: local AI chat (spec 6.6) --------------------------------------------------------
with tab_ai:
    st.markdown(
        "**Ask your CA** — a free AI model running on this PC answers your questions, "
        "using the figures on this page. Nothing leaves your computer."
    )
    is_web = auth.load_config(tls_enabled=bool(st.get_option("server.sslCertFile"))).demo
    cfg = ai.config()
    if is_web:
        st.info(
            "The AI chat runs on your own PC, so it's only in the desktop app — not on the web version. "
            "Everything else on this page works here.",
            icon=":material/computer:",
        )
    elif cfg.url is None:
        st.info(
            "To switch it on, run the app with Docker (`docker compose up -d`): it starts the local AI service "
            "and sets `EXPENSES_AI_URL=http://ollama:11434`. Running without Docker? Install Ollama and set "
            "`EXPENSES_AI_URL=http://localhost:11434`.",
            icon=":material/settings:",
        )
    else:
        status = ai.status(cfg.url, cfg.model)
        if not status.reachable:
            st.warning(
                "The local AI service isn't running. Start it with `docker compose up -d` and reload this page.",
                icon=":material/power_off:",
            )
        elif not status.has_model:
            st.markdown(
                f"One-time setup: download the free model **{cfg.model}** (about 2.5 GB). It is stored on this PC "
                "and works offline afterwards."
            )
            if st.button("Download model", key="ai_pull", type="primary", icon=":material/download:"):
                bar = st.progress(0, text="Starting download…")
                try:
                    for step in ai.pull(cfg.url, cfg.model):
                        if step.fraction is not None:
                            bar.progress(
                                int(step.fraction * 100), text=f"Downloading… {int(step.fraction * 100)}%"
                            )
                    st.rerun()
                except ai.AIError as exc:
                    st.error(f"Download failed: {exc}", icon=":material/error:")
        else:
            st.caption(f"Model: {cfg.model} · answers can take a little while on a laptop CPU.")
            history = st.session_state.setdefault("ca_chat", [])
            for message in history:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
            question = st.chat_input("Ask about your taxes, e.g. “Which regime should I pick, and why?”")
            if question:
                context = ai.build_context(
                    inputs, result, items, ca.filing_calendar(year, today), ca.checklist(items, year)
                )
                with st.chat_message("user"):
                    st.markdown(question)
                with st.chat_message("assistant"):
                    try:
                        answer = st.write_stream(ai.ask(cfg.url, cfg.model, history, question, context))
                    except ai.AIError as exc:
                        answer = None
                        st.error(f"The AI couldn't answer: {exc}", icon=":material/error:")
                if answer:
                    history += [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ]
            if history and st.button("Clear chat", key="ai_clear", icon=":material/delete_sweep:"):
                st.session_state["ca_chat"] = []
                st.rerun()
