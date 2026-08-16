"""Aging, computed generically over whatever document types are installed.

Nothing here mentions invoices or receipts. A charge document is any type declaring
`settles='receivable'` (or `'payable'`) with `role='charge'`; a settlement document is the same
`settles` value with `role='settlement'`, and its `payload.allocations` reduce the charges it
names. Install a credit-note module tomorrow and it joins this report by declaring its type.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date as date_cls
from typing import Any

BUCKETS = (("current", 0), ("d1_30", 1), ("d31_60", 31), ("d61_90", 61), ("d90_plus", 91))

OPEN_STATUSES = ("posted", "partial")


def bucket_for(days_late: int) -> str:
    name = "current"
    for bucket, lower in BUCKETS:
        if days_late >= lower:
            name = bucket
    return name


def _types_for(all_types: dict, kind: str, role: str) -> list[str]:
    return [
        key
        for key, doc_type in all_types.items()
        if doc_type.settles == kind and doc_type.role == role
    ]


def allocations(
    conn: sqlite3.Connection, settlement_types: list[str], as_of: str
) -> dict[str, int]:
    """Sum every settlement allocation per charge document, in base currency."""
    if not settlement_types:
        return {}
    placeholders = ",".join("?" for _ in settlement_types)
    rows = conn.execute(
        f"SELECT payload FROM documents WHERE doc_type IN ({placeholders})"
        " AND deleted = 0 AND status != 'void' AND date <= ?",
        (*settlement_types, as_of),
    ).fetchall()

    totals: dict[str, int] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        for allocation in payload.get("allocations") or []:
            target = allocation.get("document_id") or allocation.get("invoice_id")
            if not target:
                continue
            amount = allocation.get("base_amount")
            if amount is None:
                amount = allocation.get("amount") or 0
            totals[target] = totals.get(target, 0) + int(amount)
    return totals


def compute(
    conn: sqlite3.Connection, all_types: dict, kind: str, as_of: str, currency: str
) -> dict[str, Any]:
    charge_types = _types_for(all_types, kind, "charge")
    settlement_types = _types_for(all_types, kind, "settlement")
    if not charge_types:
        return {
            "currency": currency,
            "kind": kind,
            "as_of": as_of,
            "items": [],
            "buckets": {name: 0 for name, _ in BUCKETS},
            "total": 0,
        }

    placeholders = ",".join("?" for _ in charge_types)
    status_placeholders = ",".join("?" for _ in OPEN_STATUSES)
    rows = conn.execute(
        f"""
        SELECT d.id, d.doc_no, d.legal_no, d.date, d.due_date, d.partner_id, d.base_total,
               d.doc_type, p.name AS partner_name
        FROM documents d
        LEFT JOIN partners p ON p.id = d.partner_id
        WHERE d.doc_type IN ({placeholders}) AND d.deleted = 0
          AND d.status IN ({status_placeholders}) AND d.date <= ?
        ORDER BY d.date
        """,
        (*charge_types, *OPEN_STATUSES, as_of),
    ).fetchall()

    settled = allocations(conn, settlement_types, as_of)
    as_of_date = date_cls.fromisoformat(as_of)
    buckets = {name: 0 for name, _ in BUCKETS}
    items = []

    for row in rows:
        outstanding = int(row["base_total"]) - settled.get(row["id"], 0)
        if outstanding <= 0:
            continue
        due = row["due_date"] or row["date"]
        days_late = (as_of_date - date_cls.fromisoformat(due)).days
        bucket = bucket_for(max(days_late, 0))
        buckets[bucket] += outstanding
        items.append(
            {
                "document_id": row["id"],
                "doc_type": row["doc_type"],
                "doc_no": row["legal_no"] or row["doc_no"],
                "partner_id": row["partner_id"],
                "partner_name": row["partner_name"],
                "date": row["date"],
                "due_date": due,
                "days_late": days_late,
                "outstanding": outstanding,
                "bucket": bucket,
            }
        )

    return {
        "currency": currency,
        "kind": kind,
        "as_of": as_of,
        "items": items,
        "buckets": buckets,
        "total": sum(buckets.values()),
    }
