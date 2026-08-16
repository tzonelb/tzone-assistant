"""Documents: one storage model, any number of module-contributed document types."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query

from ...core.entities import EntityDescriptor
from ...core.errors import ValidationError
from ...core.registry import Registry, get_registry
from ...db import next_counter, read_only
from ...security import current_user
from . import aging
from .types import DocumentType

SCHEMA = """
-- `doc_type` has no CHECK constraint on purpose: valid types are whatever modules are
-- installed, and adding a document type must never require a schema migration.
CREATE TABLE IF NOT EXISTS documents (
    id               TEXT PRIMARY KEY,
    doc_type         TEXT NOT NULL,
    doc_no           TEXT NOT NULL,          -- device-scoped, assigned offline
    legal_no         TEXT,                   -- gapless, assigned by the server on first push
    date             TEXT NOT NULL,
    due_date         TEXT,
    partner_id       TEXT,
    currency         TEXT NOT NULL,
    fx_rate          INTEGER NOT NULL DEFAULT 1000000,
    total            INTEGER NOT NULL DEFAULT 0,
    base_total       INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL,
    journal_entry_id TEXT,
    memo             TEXT NOT NULL DEFAULT '',
    payload          TEXT NOT NULL DEFAULT '{}',   -- lines, taxes, allocations
    rev        INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    change_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documents_seq     ON documents(change_seq);
CREATE INDEX IF NOT EXISTS idx_documents_partner ON documents(partner_id);
CREATE INDEX IF NOT EXISTS idx_documents_type    ON documents(doc_type, status);
"""

router = APIRouter(prefix="/api/documents", tags=["documents"])

_OPEN_STATUSES = ("posted", "partial", "paid")


def document_types() -> dict[str, DocumentType]:
    """Every type contributed by an installed module, keyed by `key`."""
    collected = get_registry().hooks.collect("document_types")
    return {doc_type.key: doc_type for doc_type in collected}


def setup(registry: Registry) -> None:
    registry.add_schema(SCHEMA)
    registry.add_entity(
        EntityDescriptor(
            name="document",
            table="documents",
            columns=(
                "doc_type", "doc_no", "legal_no", "date", "due_date", "partner_id",
                "currency", "fx_rate", "total", "base_total", "status", "journal_entry_id",
                "memo", "payload",
            ),
            required=("doc_type", "doc_no", "date", "currency", "status"),
            defaults={
                "fx_rate": 1_000_000,
                "total": 0,
                "base_total": 0,
                "memo": "",
                "payload": "{}",
            },
            json_columns=("payload",),
            validators=[validate_document],
            before_write=[assign_legal_number],
        )
    )
    registry.add_router(router)


def validate_document(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    """Reject a document whose type no module provides — it would be unreadable data."""
    if record.get("deleted"):
        return
    doc_type = str(record.get("doc_type"))
    if doc_type not in document_types():
        raise ValidationError(
            f"no installed module provides document type {doc_type!r}"
        )


def assign_legal_number(
    conn: sqlite3.Connection, record: dict[str, Any]
) -> dict[str, Any] | None:
    """Allocate the gapless, authority-facing number the first time a document arrives posted.

    The device-scoped `doc_no` is what the user saw offline; `legal_no` is the sequential
    number the tax authority expects. Both are kept — the mapping between them is the audit
    trail for why number 41 was issued after number 39 on a given terminal.
    """
    doc_type = document_types().get(str(record.get("doc_type")))
    if doc_type is None or not doc_type.legal_numbering:
        return None
    if record.get("status") not in _OPEN_STATUSES:
        return None

    existing = conn.execute(
        "SELECT legal_no FROM documents WHERE id = ?", (record["id"],)
    ).fetchone()
    if existing and existing["legal_no"]:
        return {"legal_no": existing["legal_no"]}

    number = next_counter(conn, f"legal_{doc_type.key}")
    return {"legal_no": f"{doc_type.prefix}-{number:06d}"}


def _base_currency(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT payload FROM settings WHERE id = 'company'").fetchone()
    if not row:
        return "USD"
    try:
        return json.loads(row["payload"] or "{}").get("base_currency", "USD")
    except json.JSONDecodeError:
        return "USD"


@router.get("/types")
def list_types(user: dict = Depends(current_user)) -> dict:
    types = sorted(document_types().values(), key=lambda t: (t.sequence, t.key))
    return {"types": [doc_type.as_dict() for doc_type in types]}


@router.get("/aging")
def aging_report(
    kind: str = Query("receivable", pattern="^(receivable|payable)$"),
    as_of: str = Query(...),
    user: dict = Depends(current_user),
) -> dict:
    with read_only() as conn:
        return aging.compute(conn, document_types(), kind, as_of, _base_currency(conn))
