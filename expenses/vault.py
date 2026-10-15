"""Remembered PDF passwords, one per account, encrypted at rest (spec rule I12).

Fernet (AES-128-CBC + HMAC-SHA256) from `cryptography`. The key lives in a separate file next to
the database, so a copy of the database alone doesn't reveal the passwords. In-memory databases
(the web version) use a key that exists only in this process's memory.
Passwords are never logged, shown or put in messages.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

KEY_FILE_NAME = "pdf-passwords.key"
_memory_key = Fernet.generate_key()  # for in-memory databases: never written anywhere

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pdf_passwords (
    account TEXT PRIMARY KEY,
    token BLOB NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _db_file(conn: sqlite3.Connection) -> str:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return row[2] or ""
    return ""


def _fernet(conn: sqlite3.Connection, create: bool) -> Fernet | None:
    """The key for this database; None when its key file is missing and `create` is False."""
    db_file = _db_file(conn)
    if not db_file:
        return Fernet(_memory_key)
    path = Path(db_file).with_name(KEY_FILE_NAME)
    if not path.exists():
        if not create:
            return None
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # owner-only where supported
        with os.fdopen(fd, "wb") as fh:
            fh.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def _table(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def save(conn: sqlite3.Connection, account: str, password: str) -> None:
    """Remember (or replace) the PDF password of an account.

    Inside a transaction (an import's `with conn:`) it is part of it; otherwise it commits at once.
    """
    _table(conn)
    joined = conn.in_transaction
    token = _fernet(conn, create=True).encrypt(password.encode("utf-8"))
    conn.execute(
        "INSERT INTO pdf_passwords (account, token, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(account) DO UPDATE SET token = excluded.token, updated_at = excluded.updated_at",
        (account.strip(), token, datetime.now().isoformat(timespec="seconds")),
    )
    if not joined:
        conn.commit()
    log.info("pdf password saved")  # no account, no password (privacy rule)


def get(conn: sqlite3.Connection, account: str) -> str | None:
    """The saved password, or None (none saved, or the key file was lost)."""
    _table(conn)
    row = conn.execute("SELECT token FROM pdf_passwords WHERE account = ?", (account.strip(),)).fetchone()
    if row is None:
        return None
    fernet = _fernet(conn, create=False)
    if fernet is None:
        return None
    try:
        return fernet.decrypt(bytes(row[0])).decode("utf-8")
    except InvalidToken:
        return None


def accounts(conn: sqlite3.Connection) -> list[str]:
    _table(conn)
    return [r[0] for r in conn.execute("SELECT account FROM pdf_passwords ORDER BY account COLLATE NOCASE")]


def forget(conn: sqlite3.Connection, account: str) -> bool:
    _table(conn)
    with conn:
        deleted = conn.execute("DELETE FROM pdf_passwords WHERE account = ?", (account.strip(),)).rowcount
    return deleted > 0


def forget_all(conn: sqlite3.Connection) -> None:
    _table(conn)
    with conn:
        conn.execute("DELETE FROM pdf_passwords")
