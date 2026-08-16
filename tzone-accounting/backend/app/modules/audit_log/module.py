"""Audit log.

This module is the smallest complete demonstration of the architecture: it is a *pure listener*.
It adds one table, subscribes to two kernel hooks, and exposes one endpoint. It imports no other
module, no other module imports it, and deleting its directory removes the feature cleanly.

Every entity any module ever adds is logged here automatically, because it listens to the
kernel's generic `record_stored` hook rather than to anything accounting-specific.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query

from ...core.registry import Registry
from ...db import read_only
from ...security import current_user

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    username   TEXT NOT NULL DEFAULT '',
    device_id  TEXT NOT NULL DEFAULT '',
    entity     TEXT NOT NULL,
    record_id  TEXT NOT NULL,
    action     TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_at     ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, record_id);
"""

router = APIRouter(prefix="/api/audit", tags=["audit"])


def setup(registry: Registry) -> None:
    registry.add_schema(SCHEMA)
    registry.add_router(router)
    registry.on("record_stored", on_record_stored)
    registry.on("sync_pushed", on_sync_pushed)


def _summarise(entity: str, record: dict[str, Any]) -> str:
    """A short human line, using whichever naming field the entity happens to have."""
    for field in ("entry_no", "doc_no", "name", "name_en", "code"):
        if record.get(field):
            return f"{field}={record[field]}"
    return ""


def on_record_stored(
    conn: sqlite3.Connection, entity: str, record_id: str, record: dict, user: dict
) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, username, device_id, entity, record_id, action, summary)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            record.get("updated_at") or "",
            user.get("username", ""),
            record.get("origin", ""),
            entity,
            record_id,
            "delete" if record.get("deleted") else "upsert",
            _summarise(entity, record),
        ),
    )


def on_sync_pushed(
    conn: sqlite3.Connection, device_id: str, accepted: list, rejected: list, user: dict
) -> None:
    """Rejections are the interesting half: they mean a device holds data the server refused."""
    for rejection in rejected:
        conn.execute(
            "INSERT INTO audit_log (at, username, device_id, entity, record_id, action,"
            " summary) VALUES (datetime('now'),?,?,?,?,'rejected',?)",
            (
                user.get("username", ""),
                device_id,
                rejection.get("entity", ""),
                rejection.get("id", ""),
                rejection.get("reason", ""),
            ),
        )


@router.get("")
def list_audit(
    limit: int = Query(100, ge=1, le=1000),
    entity: str | None = None,
    action: str | None = None,
    user: dict = Depends(current_user),
) -> dict:
    clauses, params = [], []
    if entity:
        clauses.append("entity = ?")
        params.append(entity)
    if action:
        clauses.append("action = ?")
        params.append(action)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_only() as conn:
        rows = conn.execute(
            f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?", (*params, limit)
        ).fetchall()
    return {"entries": [dict(row) for row in rows]}
