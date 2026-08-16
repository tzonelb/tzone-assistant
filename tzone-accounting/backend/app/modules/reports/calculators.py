"""Financial statements in SQL over the consolidated ledger.

These mirror the client calculators in `frontend/src/modules/reports/` — the client versions are
what users see, because they work offline; these exist for cross-device reporting, exports and
audit. Both are asserted to balance from the same data in the test suites.

All arithmetic is on integer minor units of the base currency (`base_debit` / `base_credit`).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..accounting.ledger import DEBIT_NORMAL, signed

_POSTED = "je.status = 'posted' AND je.deleted = 0"


def base_currency(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT payload FROM settings WHERE id = 'company'").fetchone()
    if not row:
        return "USD"
    try:
        return json.loads(row["payload"] or "{}").get("base_currency", "USD")
    except json.JSONDecodeError:
        return "USD"


def trial_balance(conn: sqlite3.Connection, date_from: str, date_to: str) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT a.id, a.code, a.name_en, a.name_ar, a.type,
               COALESCE(SUM(jl.base_debit), 0)  AS debit,
               COALESCE(SUM(jl.base_credit), 0) AS credit
        FROM accounts a
        JOIN journal_lines jl   ON jl.account_id = a.id
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE {_POSTED} AND je.date >= ? AND je.date <= ?
        GROUP BY a.id
        HAVING debit != 0 OR credit != 0
        ORDER BY a.code
        """,
        (date_from, date_to),
    ).fetchall()

    lines = [
        {
            "account_id": row["id"],
            "code": row["code"],
            "name_en": row["name_en"],
            "name_ar": row["name_ar"],
            "type": row["type"],
            "debit": row["debit"],
            "credit": row["credit"],
            "balance": signed(row["type"], row["debit"], row["credit"]),
        }
        for row in rows
    ]
    total_debit = sum(line["debit"] for line in lines)
    total_credit = sum(line["credit"] for line in lines)
    return {
        "currency": base_currency(conn),
        "from": date_from,
        "to": date_to,
        "lines": lines,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }


def _totals_by_type(
    conn: sqlite3.Connection, types: tuple[str, ...], date_from: str | None, date_to: str
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in types)
    params: list[Any] = [*types]
    date_clause = "je.date <= ?"
    if date_from is not None:
        date_clause = "je.date >= ? AND je.date <= ?"
        params.append(date_from)
    params.append(date_to)
    return conn.execute(
        f"""
        SELECT a.id, a.code, a.name_en, a.name_ar, a.type,
               COALESCE(SUM(jl.base_debit), 0)  AS debit,
               COALESCE(SUM(jl.base_credit), 0) AS credit
        FROM accounts a
        JOIN journal_lines jl   ON jl.account_id = a.id
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE a.type IN ({placeholders}) AND {_POSTED} AND {date_clause}
        GROUP BY a.id
        ORDER BY a.code
        """,
        tuple(params),
    ).fetchall()


def _as_lines(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    lines = []
    for row in rows:
        amount = signed(row["type"], row["debit"], row["credit"])
        if amount == 0:
            continue
        lines.append(
            {
                "account_id": row["id"],
                "code": row["code"],
                "name_en": row["name_en"],
                "name_ar": row["name_ar"],
                "type": row["type"],
                "amount": amount,
            }
        )
    return lines


def profit_and_loss(conn: sqlite3.Connection, date_from: str, date_to: str) -> dict[str, Any]:
    income = _as_lines(_totals_by_type(conn, ("income",), date_from, date_to))
    expenses = _as_lines(_totals_by_type(conn, ("expense",), date_from, date_to))
    total_income = sum(line["amount"] for line in income)
    total_expense = sum(line["amount"] for line in expenses)
    return {
        "currency": base_currency(conn),
        "from": date_from,
        "to": date_to,
        "income": income,
        "expenses": expenses,
        "total_income": total_income,
        "total_expense": total_expense,
        "net_profit": total_income - total_expense,
    }


def balance_sheet(conn: sqlite3.Connection, as_of: str) -> dict[str, Any]:
    assets = _as_lines(_totals_by_type(conn, ("asset",), None, as_of))
    liabilities = _as_lines(_totals_by_type(conn, ("liability",), None, as_of))
    equity = _as_lines(_totals_by_type(conn, ("equity",), None, as_of))

    income = _as_lines(_totals_by_type(conn, ("income",), None, as_of))
    expenses = _as_lines(_totals_by_type(conn, ("expense",), None, as_of))
    retained = sum(line["amount"] for line in income) - sum(line["amount"] for line in expenses)

    total_assets = sum(line["amount"] for line in assets)
    total_liabilities = sum(line["amount"] for line in liabilities)
    total_equity = sum(line["amount"] for line in equity) + retained
    return {
        "currency": base_currency(conn),
        "as_of": as_of,
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "retained_earnings": retained,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "balanced": total_assets == total_liabilities + total_equity,
    }


def general_ledger(
    conn: sqlite3.Connection, account_id: str, date_from: str, date_to: str
) -> dict[str, Any]:
    account = conn.execute(
        "SELECT id, code, name_en, name_ar, type FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if account is None:
        return {"account": None, "opening": 0, "lines": [], "closing": 0}

    opening_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(jl.base_debit), 0)  AS debit,
               COALESCE(SUM(jl.base_credit), 0) AS credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE jl.account_id = ? AND {_POSTED} AND je.date < ?
        """,
        (account_id, date_from),
    ).fetchone()

    sign = 1 if account["type"] in DEBIT_NORMAL else -1
    balance = sign * (opening_row["debit"] - opening_row["credit"])
    opening = balance

    rows = conn.execute(
        f"""
        SELECT je.id AS entry_id, je.entry_no, je.date, je.memo, je.source_kind,
               jl.description, jl.base_debit, jl.base_credit, jl.partner_id
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE jl.account_id = ? AND {_POSTED} AND je.date >= ? AND je.date <= ?
        ORDER BY je.date, je.entry_no, jl.line_no
        """,
        (account_id, date_from, date_to),
    ).fetchall()

    lines = []
    for row in rows:
        balance += sign * (row["base_debit"] - row["base_credit"])
        lines.append(
            {
                "entry_id": row["entry_id"],
                "entry_no": row["entry_no"],
                "date": row["date"],
                "memo": row["memo"] or row["description"],
                "source_kind": row["source_kind"],
                "partner_id": row["partner_id"],
                "debit": row["base_debit"],
                "credit": row["base_credit"],
                "balance": balance,
            }
        )

    return {
        "currency": base_currency(conn),
        "account": dict(account),
        "from": date_from,
        "to": date_to,
        "opening": opening,
        "lines": lines,
        "closing": balance,
    }
