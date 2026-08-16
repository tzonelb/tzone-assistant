"""Journal invariants.

The client maps documents into journal entries so it can work offline; the server does not
re-derive that mapping. What it does is refuse to store an entry that breaks the invariants in
docs/ACCOUNTING_MODEL.md §2, so a buggy or tampered client cannot corrupt the audit copy.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...core.errors import ValidationError

DEBIT_NORMAL = ("asset", "expense")


def lock_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT payload FROM settings WHERE id = 'company'").fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"] or "{}").get("lock_date") or None
    except json.JSONDecodeError:
        return None


def _accounts(conn: sqlite3.Connection, ids: set[str]) -> dict[str, sqlite3.Row]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, is_group, is_active, deleted, currency FROM accounts"
        f" WHERE id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {row["id"]: row for row in rows}


def validate_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    """Raise ValidationError if this journal entry must not be stored."""
    status = entry.get("status")
    if status not in ("draft", "posted", "void"):
        raise ValidationError(f"unknown status {status!r}")

    # Drafts are work in progress: replicated as-is, never reaching a report.
    if status == "draft" or entry.get("deleted"):
        return

    lines = entry.get("lines") or []
    if len(lines) < 2:
        raise ValidationError("a posted entry needs at least two lines")

    debit = credit = base_debit = base_credit = 0
    for index, line in enumerate(lines, start=1):
        ln_debit = int(line.get("debit") or 0)
        ln_credit = int(line.get("credit") or 0)
        if ln_debit < 0 or ln_credit < 0:
            raise ValidationError(f"line {index}: negative amount")
        if ln_debit and ln_credit:
            raise ValidationError(f"line {index}: a line is either a debit or a credit")
        if not ln_debit and not ln_credit:
            raise ValidationError(f"line {index}: zero amount")
        if not line.get("account_id"):
            raise ValidationError(f"line {index}: missing account")
        debit += ln_debit
        credit += ln_credit
        base_debit += int(line.get("base_debit") or 0)
        base_credit += int(line.get("base_credit") or 0)

    if debit != credit:
        raise ValidationError(f"unbalanced entry: debit {debit} != credit {credit}")
    if base_debit != base_credit:
        raise ValidationError(
            f"unbalanced in base currency: {base_debit} != {base_credit}"
        )

    accounts = _accounts(conn, {line["account_id"] for line in lines})
    currency = entry.get("currency")
    for line in lines:
        account = accounts.get(line["account_id"])
        if account is None:
            raise ValidationError(f"unknown account {line['account_id']}")
        if account["deleted"] or not account["is_active"]:
            raise ValidationError(f"account {line['account_id']} is not active")
        if account["is_group"]:
            raise ValidationError(f"account {line['account_id']} is a group account")
        if account["currency"] and account["currency"] != currency:
            raise ValidationError(
                f"account {line['account_id']} only accepts {account['currency']}"
            )

    locked = lock_date(conn)
    if locked and str(entry.get("date", "")) <= locked:
        raise ValidationError(f"period is locked up to {locked}")


def signed(account_type: str, debit: int, credit: int) -> int:
    """Balance on the account's normal side, so a healthy account reads positive."""
    return debit - credit if account_type in DEBIT_NORMAL else credit - debit
