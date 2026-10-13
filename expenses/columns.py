"""Statements without a heading row: work out the columns from the data (spec rule I11).

infer_columns() looks at what the cells contain — dates, money with two decimals, Dr/Cr flags,
long text — and, when there is a running balance, proves which column is debit and which is
credit by checking the balance arithmetic row by row.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from expenses.dates import DateParseError, parse_date
from expenses.importer import ColumnMap, HeaderNotFoundError, _signed_by_indicator
from expenses.money import MoneyParseError, parse_amount

DATE_SHARE = 0.9  # of filled cells that must be dates
MONEY_SHARE = 0.9  # of filled cells that must be amounts ...
DECIMALS_SHARE = 0.8  # ... and written with two decimals (so cheque numbers aren't money)
BALANCE_SHARE = 0.8  # of rows whose balance change must equal the amount
FULL_SHARE = 0.95  # a balance column is filled on (nearly) every row
SAMPLE_ROWS = 3

ROLE_NAMES = {
    "date": "Date",
    "description": "Description",
    "debit": "Debit",
    "credit": "Credit",
    "amount": "Amount",
    "indicator": "Dr/Cr",
    "balance": "Balance",
}
_FLAGS = {"dr", "cr", "d", "c", "db", "debit", "credit"}
_TWO_DECIMALS = re.compile(r"\.\d{2}(?!\d)")

NO_HEADING = (
    "no heading row with date, description and amount columns was found, and the columns couldn't be "
    "worked out from the data — add a heading row such as Date,Narration,Withdrawal,Deposit,Balance"
)


class ColumnRolesError(HeaderNotFoundError):
    """The user's column choice is incomplete (no date, description or amount)."""


@dataclass
class Inference:
    roles: dict[str, int]  # role -> column index
    start: int  # index of the first data row
    width: int
    balance_checked: bool  # True when the running balance confirmed the signs
    sample: list[list[str]] = field(default_factory=list, repr=False)  # first rows, for the preview only

    def header_row(self) -> list[str]:
        names = [""] * self.width
        for role, col in self.roles.items():
            names[col] = ROLE_NAMES[role]
        return names

    def column_map(self) -> ColumnMap:
        r = self.roles
        return ColumnMap(
            date=r["date"],
            description=r["description"],
            amount=r.get("amount"),
            debit=r.get("debit"),
            credit=r.get("credit"),
            indicator=r.get("indicator"),
            header=self.header_row(),
        )


def _cell(row: list[str], col: int) -> str:
    return row[col].strip() if col < len(row) else ""


def _is_date(text: str) -> bool:
    try:
        parse_date(text)
        return True
    except (DateParseError, ValueError):
        return False


def _money(text: str) -> int | None:
    try:
        return parse_amount(text)
    except (MoneyParseError, ValueError):
        return None


def _share(cells: list[str], test) -> float:
    return sum(1 for c in cells if test(c)) / len(cells) if cells else 0.0


def _signed(row: list[str], roles: dict[str, int]) -> int | None:
    """The row's signed amount under these roles, or None if it can't be read."""
    try:
        if "amount" in roles:
            if "indicator" in roles:
                return _signed_by_indicator(_cell(row, roles["amount"]), _cell(row, roles["indicator"]))
            return parse_amount(_cell(row, roles["amount"]))
        debit, credit = _cell(row, roles["debit"]), _cell(row, roles["credit"])
        return (abs(parse_amount(credit)) if credit else 0) - (abs(parse_amount(debit)) if debit else 0)
    except (MoneyParseError, ValueError, KeyError):
        return None


def _balance_share(rows: list[list[str]], roles: dict[str, int], balance: int) -> float:
    """Share of rows whose balance change equals the amount (oldest-first or newest-first)."""
    if len(rows) < 2:
        return 0.0
    balances = [_money(_cell(r, balance)) for r in rows]
    amounts = [_signed(r, roles) for r in rows]
    forward = backward = 0
    for i in range(1, len(rows)):
        if None in (balances[i], balances[i - 1]):
            continue
        if amounts[i] is not None and balances[i] - balances[i - 1] == amounts[i]:
            forward += 1
        if amounts[i - 1] is not None and balances[i - 1] - balances[i] == amounts[i - 1]:
            backward += 1
    return max(forward, backward) / (len(rows) - 1)


def _data_rows(rows: list[list[str]], date_col: int | None = None) -> tuple[int, list[int]]:
    """(width, indexes of data rows): rows of the most common width that hold a date."""

    def has_date(row: list[str]) -> bool:
        cells = [row[date_col]] if date_col is not None and date_col < len(row) else row
        return any(_is_date(c.strip()) for c in cells if c.strip())

    dated = [(i, len(r)) for i, r in enumerate(rows) if any(c.strip() for c in r) and has_date(r)]
    if not dated:
        return 0, []
    width = Counter(w for _, w in dated).most_common(1)[0][0]
    return width, [i for i, w in dated if w == width]


def _validate(roles: dict[str, int], width: int) -> None:
    missing = [name for name in ("date", "description") if name not in roles]
    if "amount" not in roles and not ("debit" in roles and "credit" in roles):
        missing.append("amount (or both debit and credit)")
    if missing:
        raise ColumnRolesError("choose a column for: " + ", ".join(missing))
    if len(set(roles.values())) != len(roles) or any(not 0 <= c < width for c in roles.values()):
        raise ColumnRolesError("each column can have only one role")


def infer_columns(rows: list[list[str]], roles: dict[str, int] | None = None) -> Inference:
    """Guess the role of every column of a headerless statement (or check the user's `roles`)."""
    if roles is not None:
        width, indexes = _data_rows(rows, roles.get("date"))
        _validate(roles, max(width, max(roles.values(), default=0) + 1))
        data = [rows[i] for i in indexes]
        if not data:
            raise ColumnRolesError("no dates found in the chosen date column")
        checked = "balance" in roles and _balance_share(data, roles, roles["balance"]) >= BALANCE_SHARE
        return Inference(dict(roles), indexes[0], width, checked, [list(r) for r in data[:SAMPLE_ROWS]])

    width, indexes = _data_rows(rows)
    data = [(rows[i] + [""] * width)[:width] for i in indexes]
    if len(data) < 2:
        raise HeaderNotFoundError(NO_HEADING)

    columns = [[r[c].strip() for r in data] for c in range(width)]
    filled = [[c for c in col if c] for col in columns]
    dates = [c for c in range(width) if filled[c] and _share(filled[c], _is_date) >= DATE_SHARE]
    money = [
        c
        for c in range(width)
        if c not in dates
        and filled[c]
        and _share(filled[c], lambda t: _money(t) is not None) >= MONEY_SHARE
        and _share(filled[c], lambda t: bool(_TWO_DECIMALS.search(t))) >= DECIMALS_SHARE
    ]
    flags = [c for c in range(width) if filled[c] and all(t.lower().strip(".") in _FLAGS for t in filled[c])]
    text = [c for c in range(width) if c not in dates and c not in money and c not in flags and filled[c]]
    if not dates or not money or not text:
        raise HeaderNotFoundError(NO_HEADING)

    def text_score(c: int) -> float:
        return sum(len(t) for t in filled[c]) / len(data)  # average length, empty cells count as 0

    base = {"date": min(dates), "description": max(text, key=text_score)}
    indicator = {"indicator": flags[0]} if flags else {}

    def full(c: int) -> bool:
        return len(filled[c]) >= FULL_SHARE * len(data)

    def exclusive(a: int, b: int) -> bool:
        both = any(columns[a][i] and columns[b][i] for i in range(len(data)))
        return not both and bool(filled[a]) and bool(filled[b])

    def layouts(others: list[int]) -> list[dict[str, int]]:
        if len(others) == 2 and exclusive(*others):
            left, right = others
            return [{"debit": left, "credit": right}, {"debit": right, "credit": left}]
        if len(others) == 1:
            return [{"amount": others[0], **indicator}]
        return []

    best: tuple[float, dict[str, int]] | None = None
    for balance in (c for c in money if full(c)):
        for layout in layouts([c for c in money if c != balance]):
            candidate = {**base, **layout, "balance": balance}
            share = _balance_share(data, candidate, balance)
            if share >= BALANCE_SHARE and (best is None or share > best[0]):
                best = (share, candidate)
    if best is not None:
        found, checked = best[1], True
    else:  # nothing to check against: left of a debit/credit pair is assumed to be the debit
        checked = False
        pair = next(((a, b) for a, b in zip(money, money[1:], strict=False) if exclusive(a, b)), None)
        found = {
            **base,
            **({"debit": pair[0], "credit": pair[1]} if pair else {"amount": money[0], **indicator}),
        }
        rest = [c for c in money if c not in found.values() and full(c)]
        if rest:
            found["balance"] = rest[-1]
    return Inference(found, indexes[0], width, checked, [list(r) for r in data[:SAMPLE_ROWS]])
