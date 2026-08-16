"""Accounting core: the chart of accounts and the double-entry journal."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ...core.entities import CHANGE_SEQ, ChildTable, EntityDescriptor
from ...core.registry import Registry
from ...db import next_counter, utcnow
from . import ledger

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# Canonical default chart, shared with the frontend so both sides seed identical rows.
CHART_PATH = Path(__file__).resolve().parents[4] / "shared" / "chart-of-accounts.json"

SETTINGS_DEFAULTS = {
    "base_currency": "USD",
    "currencies": [
        {"code": "USD", "decimals": 2, "symbol": "$"},
        {"code": "IQD", "decimals": 0, "symbol": "د.ع"},
        {"code": "LBP", "decimals": 0, "symbol": "ل.ل"},
        {"code": "EUR", "decimals": 2, "symbol": "€"},
    ],
    "fiscal_year_start": "01-01",
    # Role -> account id. Modules look accounts up by role instead of hardcoding codes,
    # so a company can re-map its chart without any module changing.
    "account_roles": {
        "receivable": "acc-1130",
        "payable": "acc-2110",
        "tax_receivable": "acc-1150",
        "tax_payable": "acc-2120",
        "sales": "acc-4100",
        "cogs": "acc-5100",
        "opening_equity": "acc-3900",
        "cash": "acc-1110",
        "bank": "acc-1120",
    },
}

JOURNAL_LINES = ChildTable(
    table="journal_lines",
    parent_column="entry_id",
    order_column="line_no",
    columns=(
        "account_id",
        "partner_id",
        "description",
        "debit",
        "credit",
        "base_debit",
        "base_credit",
    ),
    payload_key="lines",
    defaults={"description": "", "debit": 0, "credit": 0, "base_debit": 0, "base_credit": 0},
)


def setup(registry: Registry) -> None:
    registry.add_schema(SCHEMA)
    registry.add_settings_defaults(SETTINGS_DEFAULTS)

    registry.add_entity(
        EntityDescriptor(
            name="account",
            table="accounts",
            columns=(
                "code", "name_en", "name_ar", "type", "parent_id", "is_group", "is_cash",
                "currency", "is_active",
            ),
            required=("code", "name_en", "type"),
            defaults={"name_ar": "", "is_group": 0, "is_cash": 0, "is_active": 1},
        )
    )

    registry.add_entity(
        EntityDescriptor(
            name="journal_entry",
            table="journal_entries",
            columns=(
                "entry_no", "date", "memo", "currency", "fx_rate", "status", "source_kind",
                "source_id", "reverses_id", "created_by",
            ),
            required=("entry_no", "date", "currency", "status"),
            defaults={
                "memo": "",
                "fx_rate": 1_000_000,
                "source_kind": "manual",
                "created_by": "",
            },
            child=JOURNAL_LINES,
            validators=[ledger.validate_entry],
        )
    )

    registry.add_seed(seed_chart_of_accounts)


def load_chart() -> list[dict]:
    return json.loads(CHART_PATH.read_text(encoding="utf-8"))["accounts"]


def seed_chart_of_accounts(conn: sqlite3.Connection) -> None:
    """Install the default chart.

    Account ids are derived from the code (`acc-1130`), not random, which is what lets a client
    that seeded the same chart offline converge with a freshly seeded server instead of
    producing two parallel trees. Existing rows are updated, never duplicated.
    """
    now = utcnow()
    for account in load_chart():
        conn.execute(
            """
            INSERT INTO accounts (id, code, name_en, name_ar, type, parent_id, is_group,
                is_cash, currency, is_active, rev, updated_at, deleted, origin, change_seq)
            VALUES (?,?,?,?,?,?,?,?,NULL,1,1,?,0,'seed',?)
            ON CONFLICT(id) DO UPDATE SET
                code=excluded.code, name_en=excluded.name_en, name_ar=excluded.name_ar,
                type=excluded.type, parent_id=excluded.parent_id,
                is_group=excluded.is_group, is_cash=excluded.is_cash,
                change_seq=excluded.change_seq
            """,
            (
                f"acc-{account['code']}",
                account["code"],
                account["name_en"],
                account["name_ar"],
                account["type"],
                f"acc-{account['parent']}" if account.get("parent") else None,
                int(bool(account.get("is_group"))),
                int(bool(account.get("is_cash"))),
                now,
                next_counter(conn, CHANGE_SEQ),
            ),
        )
