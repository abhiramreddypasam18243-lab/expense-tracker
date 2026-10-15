"""Command-line interface (spec section 7.1). A thin layer: parse args, call core, print.

Exit codes: 0 success, 1 user error (bad file / bad arguments), 2 internal error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from expenses import categorize, combine, db, exporter, insights, recurring, vault
from expenses.anomaly import find_anomalies
from expenses.importer import HeaderNotFoundError, import_file, undo_import
from expenses.money import format_inr
from expenses.pdf_import import PdfImportError, pdf_to_csv


class UserError(Exception):
    """Problem the user can fix; printed without a traceback, exit code 1."""


def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


# --- commands ---------------------------------------------------------------------------------


def cmd_import(conn, args) -> None:
    try:
        report = import_file(conn, args.csv, account=args.account)
    except FileNotFoundError:
        raise UserError(f"file not found: {args.csv}") from None
    except (HeaderNotFoundError, UnicodeDecodeError) as exc:
        raise UserError(str(exc)) from None
    print(
        f"Imported batch {report.batch_id}: {report.rows_imported} new, {report.rows_duplicate} duplicate, "
        f"{report.rows_rejected} rejected (of {report.rows_total} rows)"
    )
    for line, reason in report.errors:
        print(f"  line {line}: {reason}")
    if report.rows_imported:
        print(f"Undo with: python -m expenses undo-import {report.batch_id}")


def cmd_forget_password(conn, args) -> None:
    if not vault.forget(conn, args.account):
        raise UserError(f"no saved PDF password for account {args.account!r}")
    print(f"Forgot the saved PDF password for {args.account}.")


def cmd_import_many(conn, args) -> None:
    inputs = []
    for item in args.files:
        path, sep, account = item.partition("=")
        if not sep or not path or not account.strip():
            raise UserError(f'expected FILE=ACCOUNT, e.g. hdfc.csv="HDFC Savings" (got {item!r})')
        try:
            with open(path, "rb") as fh:
                inputs.append(combine.StatementInput(fh.read(), account, path, password=args.pdf_password))
        except FileNotFoundError:
            raise UserError(f"file not found: {path}") from None

    plan = combine.preview(conn, inputs)
    print(f"{'File':<28}  {'Account':<20}  {'Rows':>5}  {'New':>5}  {'Dup':>5}  {'Rej':>5}  Status")
    for f in plan.files:
        name, account = _shorten(f.source_name, 28), _shorten(f.account, 20)
        counts = f"{f.rows_total:>5}  {f.new:>5}  {f.duplicate:>5}  {f.rejected:>5}"
        print(f"{name:<28}  {account:<20}  {counts}  {f.problem or 'ready'}")
        for line, reason in f.errors:
            print(f"    line {line}: {reason}")
    if plan.pairs:
        print("\nTransfer pairs between your accounts (saved as Transfers unless --no-transfers):")
        for p in plan.pairs:
            out, into = _shorten(p.debit.account, 18), _shorten(p.credit.account, 18)
            amount = format_inr(p.credit.amount_minor)
            print(f"  {p.debit.txn_date}  {out:<18} -> {p.credit.txn_date}  {into:<18}  {amount:>14}")
    else:
        print("\nTransfer pairs: none found")
    if not plan.ok:
        raise UserError("some files have a problem; nothing was imported")
    if not args.yes:
        print(f"\nDry run: nothing imported. Add --yes to import {plan.new_rows} new transaction(s).")
        return

    report = combine.commit(conn, plan, confirmed=set() if args.no_transfers else None)
    print(
        f"\nImported batch {report.batch_id}: {report.rows_imported} new transaction(s) from "
        f"{len(report.files)} file(s). Marked {report.transfers_marked} transfer pair(s) as Transfers."
    )
    print(f"Undo with: python -m expenses undo-import {report.batch_id}")


def cmd_pdf_to_csv(conn, args) -> None:
    try:
        with open(args.pdf, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        raise UserError(f"file not found: {args.pdf}") from None
    try:
        conversion = pdf_to_csv(data, args.password)
    except PdfImportError as exc:
        raise UserError(str(exc)) from None  # the message never contains the password
    try:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(conversion.csv_text)
    except OSError as exc:
        raise UserError(f"cannot write {args.out}: {exc.strerror}") from None
    print(f"Converted {conversion.pages} page(s) into {conversion.rows} row(s): {args.out}")


def cmd_list(conn, args) -> None:
    start, end = insights.month_range(args.month) if args.month else (None, None)
    rows = db.list_transactions(conn, start=start, end=end, category=args.category, limit=args.limit)
    if not rows:
        print("No transactions.")
        return
    print(f"{'Date':<10}  {'Description':<42}  {'Category':<18}  {'Amount':>15}")
    for r in rows:
        print(
            f"{r['txn_date']:<10}  {_shorten(r['description'], 42):<42}  "
            f"{_shorten(r['category_name'], 18):<18}  {format_inr(r['amount_minor']):>15}"
        )


def cmd_summary(conn, args) -> None:
    s = insights.monthly_summary(conn, args.month)
    print(f"Summary for {s.month}  ({s.txn_count} transactions)")
    print(f"  Income  {format_inr(s.income_minor):>16}")
    print(f"  Spend   {format_inr(s.spend_minor):>16}")
    print(f"  Net     {format_inr(s.net_minor):>16}")
    if s.by_category:
        print("\nSpend by category")
        for c in s.by_category:
            share = c.spend_minor * 100 // s.spend_minor
            print(f"  {c.name:<20} {format_inr(c.spend_minor):>14}  {share:>3}%")
    if s.top_merchants:
        print("\nTop merchants")
        for name, total in s.top_merchants:
            print(f"  {name:<20} {format_inr(total):>14}")
    budgets = insights.budget_status(conn, args.month)
    if budgets:
        print("\nBudgets")
        for b in budgets:
            spent, limit = format_inr(b.spent_minor), format_inr(b.limit_minor)
            print(f"  {b.name:<20} {spent:>14} of {limit:<14} {b.status}")


def cmd_anomalies(conn, args) -> None:
    found = find_anomalies(conn)
    if not found:
        print("No unusual spending found.")
        return
    print(f"{'Date':<10}  {'Category':<18}  {'Amount':>14}  {'Typical':>12}  {'Score':>6}")
    for a in found:
        print(
            f"{a.txn_date:<10}  {_shorten(a.category, 18):<18}  {format_inr(-a.amount_minor):>14}  "
            f"{format_inr(int(a.median_minor)):>12}  {a.score:>6.1f}"
        )


def cmd_recurring(conn, args) -> None:
    found = recurring.find_recurring(conn)
    if not found:
        print("No recurring monthly payments found.")
        return
    print(f"{'Name':<24}  {'Category':<18}  {'Typical':>12}  {'Paid':>4}  {'Next expected':<13}  Status")
    for r in found:
        status = "active" if r.active else "stopped?"
        typical = format_inr(int(r.typical_minor))
        print(
            f"{_shorten(r.name, 24):<24}  {_shorten(r.category, 18):<18}  {typical:>12}  "
            f"{r.count:>4}  {r.next_date.isoformat():<13}  {status}"
        )


def cmd_recategorize(conn, args) -> None:
    changed = categorize.recategorize(conn)
    print(f"Recategorised {changed} transaction(s). Manual choices were kept.")


def cmd_export(conn, args) -> None:
    text = exporter.export_csv(conn, month=args.month)
    try:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    except OSError as exc:
        raise UserError(f"cannot write {args.out}: {exc.strerror}") from None
    print(f"Exported {text.count(chr(10)) - 1} transaction(s) to {args.out}")


def cmd_undo(conn, args) -> None:
    if not db.batch_exists(conn, args.batch_id):
        raise UserError(f"no import batch {args.batch_id}")
    removed = undo_import(conn, args.batch_id)
    print(f"Removed batch {args.batch_id} ({removed} transaction(s)).")


# --- wiring ------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m expenses", description="Private, offline expense tracker."
    )
    parser.add_argument("--db", help="database path (default: $EXPENSES_DB or data/expenses.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="import a CSV bank statement")
    p.add_argument("csv")
    p.add_argument("--account", required=True, help="account name, e.g. 'HDFC Savings'")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("import-many", help="import several statements (accounts) at once, matching transfers")
    p.add_argument("files", nargs="+", metavar="FILE=ACCOUNT")
    p.add_argument("--yes", action="store_true", help="really import (without it: preview only)")
    p.add_argument("--no-transfers", action="store_true", help="don't mark transfer pairs")
    p.add_argument(
        "--pdf-password",
        help="password for locked PDFs (remembered per account; saved ones are used if omitted)",
    )
    p.set_defaults(func=cmd_import_many)

    p = sub.add_parser("forget-password", help="forget the saved PDF password of an account")
    p.add_argument("account")
    p.set_defaults(func=cmd_forget_password)

    p = sub.add_parser("pdf-to-csv", help="convert a PDF bank statement to CSV")
    p.add_argument("pdf")
    p.add_argument("out")
    p.add_argument("--password", help="for password-protected PDFs (never stored)")
    p.set_defaults(func=cmd_pdf_to_csv)

    p = sub.add_parser("list", help="list transactions")
    p.add_argument("--month", help="YYYY-MM")
    p.add_argument("--category")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("summary", help="monthly summary")
    p.add_argument("--month", required=True, help="YYYY-MM")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("anomalies", help="unusually large spends")
    p.set_defaults(func=cmd_anomalies)

    p = sub.add_parser("recurring", help="recurring monthly payments")
    p.set_defaults(func=cmd_recurring)

    p = sub.add_parser("recategorize", help="re-run rules (manual choices are kept)")
    p.set_defaults(func=cmd_recategorize)

    p = sub.add_parser("export", help="export transactions to CSV")
    p.add_argument("out")
    p.add_argument("--month", help="YYYY-MM")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("undo-import", help="delete everything one import added")
    p.add_argument("batch_id", type=int)
    p.set_defaults(func=cmd_undo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # ₹ on Windows consoles
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse: --help -> 0, bad arguments -> 2; we map bad arguments to 1
        return 0 if exc.code == 0 else 1

    conn = db.connect(args.db)
    try:
        categorize.seed_defaults(conn)
        args.func(conn, args)
        return 0
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # e.g. a malformed --month
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — last resort; no data in the message
        print(f"internal error: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        conn.close()
