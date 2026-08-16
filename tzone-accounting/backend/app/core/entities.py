"""Generic replicated-entity machinery.

The kernel knows how to store, version and replicate *any* entity a module declares. It knows
nothing about accounts, invoices or partners — a module describes its table with an
`EntityDescriptor` and the sync engine, the change feed and the pull endpoint all work for it
immediately. That is the whole trick behind "drop in a module and it syncs".
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import next_counter
from .errors import ValidationError

CHANGE_SEQ = "change_seq"

# Replication envelope carried by every syncable table (see docs/OFFLINE_SYNC.md §2).
ENVELOPE = ("rev", "updated_at", "deleted", "origin")

Validator = Callable[[sqlite3.Connection, dict], None]
WriteHook = Callable[[sqlite3.Connection, dict], "dict | None"]


@dataclass
class ChildTable:
    """A child collection owned by its parent row and replaced wholesale on every write.

    Journal lines are the canonical example: they have no independent identity, they are
    never referenced on their own, and rewriting them as a set removes a whole class of
    partial-update bugs.
    """

    table: str
    parent_column: str
    order_column: str
    columns: tuple[str, ...]
    payload_key: str
    defaults: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityDescriptor:
    """Everything the kernel needs to replicate one module-owned table."""

    name: str
    table: str
    columns: tuple[str, ...]
    module: str = "core"
    required: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    json_columns: tuple[str, ...] = ()
    child: ChildTable | None = None
    validators: list[Validator] = field(default_factory=list)
    # Runs just before the write and may return field overrides (e.g. an allocated number).
    before_write: list[WriteHook] = field(default_factory=list)

    def validate(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        for column in self.required:
            if record.get(column) in (None, ""):
                raise ValidationError(f"{self.name}: missing required field {column!r}")
        for validator in self.validators:
            validator(conn, record)


def _coerce(descriptor: EntityDescriptor, column: str, value: Any) -> Any:
    if column in descriptor.json_columns:
        if isinstance(value, str):
            return value
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    if value is None:
        return descriptor.defaults.get(column)
    return value


def serialise(descriptor: EntityDescriptor, record: dict[str, Any]) -> dict[str, Any]:
    values = {c: _coerce(descriptor, c, record.get(c)) for c in descriptor.columns}
    values["rev"] = int(record.get("rev") or 1)
    values["updated_at"] = record["updated_at"]
    values["deleted"] = int(bool(record.get("deleted")))
    values["origin"] = record.get("origin") or ""
    return values


def row_to_record(descriptor: EntityDescriptor, row: sqlite3.Row) -> dict[str, Any]:
    record: dict[str, Any] = {"id": row["id"]}
    for column in descriptor.columns:
        value = row[column]
        if column in descriptor.json_columns:
            try:
                value = json.loads(value or "{}")
            except (json.JSONDecodeError, TypeError):
                value = {}
        record[column] = value
    for column in ENVELOPE:
        record[column] = row[column]
    record["deleted"] = bool(row["deleted"])
    return record


def wins(incoming: dict[str, Any], existing: sqlite3.Row | None) -> bool:
    """Last-writer-wins on `updated_at`, tie-broken by `origin` so every device agrees."""
    if existing is None:
        return True
    incoming_at = str(incoming.get("updated_at") or "")
    existing_at = str(existing["updated_at"] or "")
    if incoming_at != existing_at:
        return incoming_at > existing_at
    return str(incoming.get("origin") or "") >= str(existing["origin"] or "")


def write(
    conn: sqlite3.Connection, descriptor: EntityDescriptor, record_id: str, values: dict
) -> int:
    seq = next_counter(conn, CHANGE_SEQ)
    columns = ("id", *descriptor.columns, *ENVELOPE, CHANGE_SEQ)
    placeholders = ",".join("?" for _ in columns)
    assignments = ",".join(f"{c}=excluded.{c}" for c in columns if c != "id")
    params = [
        record_id,
        *(values[c] for c in descriptor.columns),
        *(values[c] for c in ENVELOPE),
        seq,
    ]
    conn.execute(
        f"INSERT INTO {descriptor.table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}",
        params,
    )
    return seq


def write_children(
    conn: sqlite3.Connection, child: ChildTable, parent_id: str, rows: list[dict]
) -> None:
    conn.execute(f"DELETE FROM {child.table} WHERE {child.parent_column} = ?", (parent_id,))
    if not rows:
        return
    columns = (child.parent_column, child.order_column, *child.columns)
    placeholders = ",".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {child.table} ({','.join(columns)}) VALUES ({placeholders})",
        [
            (
                parent_id,
                index + 1,
                *(
                    row.get(column, child.defaults.get(column))
                    if row.get(column) is not None
                    else child.defaults.get(column)
                    for column in child.columns
                ),
            )
            for index, row in enumerate(rows)
        ],
    )


def read_children(
    conn: sqlite3.Connection, child: ChildTable, parent_ids: list[str]
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for start in range(0, len(parent_ids), 400):
        chunk = parent_ids[start : start + 400]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT * FROM {child.table} WHERE {child.parent_column} IN ({placeholders})"
            f" ORDER BY {child.parent_column}, {child.order_column}",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            grouped.setdefault(row[child.parent_column], []).append(
                {column: row[column] for column in child.columns}
            )
    return grouped


def apply_op(
    conn: sqlite3.Connection,
    descriptor: EntityDescriptor,
    record_id: str,
    op: str,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply one replicated operation. Returns overrides assigned server-side, if any."""
    record = {**record, "id": record_id}
    if op == "delete":
        record["deleted"] = True
    if not record.get("updated_at"):
        raise ValidationError(f"{descriptor.name}: missing updated_at")

    existing = conn.execute(
        f"SELECT updated_at, origin FROM {descriptor.table} WHERE id = ?", (record_id,)
    ).fetchone()
    if not wins(record, existing):
        return None  # a newer version is already stored; the op is still acknowledged

    descriptor.validate(conn, record)

    assigned: dict[str, Any] = {}
    for hook in descriptor.before_write:
        overrides = hook(conn, record)
        if overrides:
            assigned.update(overrides)
            record = {**record, **overrides}

    write(conn, descriptor, record_id, serialise(descriptor, record))
    if descriptor.child is not None:
        write_children(
            conn, descriptor.child, record_id, list(record.get(descriptor.child.payload_key) or [])
        )
    return assigned or None
