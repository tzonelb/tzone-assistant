"""Partners: one table for customers and suppliers, because most real counterparties are both."""

from __future__ import annotations

from ...core.entities import EntityDescriptor
from ...core.registry import Registry

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    id                    TEXT PRIMARY KEY,
    code                  TEXT NOT NULL DEFAULT '',
    name                  TEXT NOT NULL,
    kind                  TEXT NOT NULL CHECK (kind IN ('customer','supplier','both')),
    phone                 TEXT NOT NULL DEFAULT '',
    email                 TEXT NOT NULL DEFAULT '',
    tax_number            TEXT NOT NULL DEFAULT '',
    address               TEXT NOT NULL DEFAULT '',
    -- Override the company-level control accounts for this partner only.
    receivable_account_id TEXT,
    payable_account_id    TEXT,
    credit_limit          INTEGER NOT NULL DEFAULT 0,
    is_active             INTEGER NOT NULL DEFAULT 1,
    rev        INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    change_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_partners_seq  ON partners(change_seq);
CREATE INDEX IF NOT EXISTS idx_partners_name ON partners(name);
"""


def setup(registry: Registry) -> None:
    registry.add_schema(SCHEMA)
    registry.add_entity(
        EntityDescriptor(
            name="partner",
            table="partners",
            columns=(
                "code", "name", "kind", "phone", "email", "tax_number", "address",
                "receivable_account_id", "payable_account_id", "credit_limit", "is_active",
            ),
            required=("name", "kind"),
            defaults={
                "code": "",
                "phone": "",
                "email": "",
                "tax_number": "",
                "address": "",
                "credit_limit": 0,
                "is_active": 1,
            },
        )
    )
