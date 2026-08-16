"""Catalog: what gets sold and bought, and which accounts each line lands in."""

from __future__ import annotations

from ...core.entities import EntityDescriptor
from ...core.registry import Registry

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id                 TEXT PRIMARY KEY,
    sku                TEXT NOT NULL DEFAULT '',
    name_en            TEXT NOT NULL,
    name_ar            TEXT NOT NULL DEFAULT '',
    kind               TEXT NOT NULL DEFAULT 'product',  -- product | service
    unit               TEXT NOT NULL DEFAULT 'pcs',
    sale_price         INTEGER NOT NULL DEFAULT 0,       -- minor units, base currency
    purchase_price     INTEGER NOT NULL DEFAULT 0,
    tax_rate_bp        INTEGER NOT NULL DEFAULT 0,       -- basis points: 1500 == 15%
    income_account_id  TEXT,
    expense_account_id TEXT,
    is_active          INTEGER NOT NULL DEFAULT 1,
    rev        INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    change_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_seq ON items(change_seq);
CREATE INDEX IF NOT EXISTS idx_items_sku ON items(sku);
"""


def setup(registry: Registry) -> None:
    registry.add_schema(SCHEMA)
    registry.add_entity(
        EntityDescriptor(
            name="item",
            table="items",
            columns=(
                "sku", "name_en", "name_ar", "kind", "unit", "sale_price", "purchase_price",
                "tax_rate_bp", "income_account_id", "expense_account_id", "is_active",
            ),
            required=("name_en",),
            defaults={
                "sku": "",
                "name_ar": "",
                "kind": "product",
                "unit": "pcs",
                "sale_price": 0,
                "purchase_price": 0,
                "tax_rate_bp": 0,
                "is_active": 1,
            },
        )
    )
