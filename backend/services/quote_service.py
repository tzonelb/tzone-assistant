"""Price quotes raised from a conversation.

"Create quote" in the chat panel needs somewhere real to land: a company's own
encrypted database, scoped to that company and (usually) to the conversation it
came from. Line items are kept as one flexible JSON blob rather than a normal-
formed items table -- there is no line-item editor yet, so a rigid schema would
only be a promise this file does not keep. `total` is stored, not only derived,
so a quote's number does not drift if a catalogue price it was based on changes
after the fact.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)

MAX_QUOTES = 5000
MAX_TITLE_LENGTH = 200
MAX_NOTES_LENGTH = 4000
MAX_ITEMS = 100

VALID_STATUSES = ("draft", "sent", "accepted", "declined")


class QuoteError(Exception):
    """A quote that cannot be stored, with a reason a person may read."""


def _clean_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in items[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:200]
        if not name:
            continue
        try:
            quantity = max(float(item.get("quantity", 1)), 0)
        except (TypeError, ValueError):
            quantity = 1
        try:
            price = max(float(item.get("price", 0)), 0)
        except (TypeError, ValueError):
            price = 0
        cleaned.append({"name": name, "quantity": quantity, "price": price})
    return cleaned


def _items_total(items: list[dict[str, Any]]) -> float:
    return round(sum(i["quantity"] * i["price"] for i in items), 2)


def _row(row: Any) -> dict[str, Any]:
    data = dict(row)
    try:
        data["items"] = json.loads(data.pop("items_json") or "[]")
    except (TypeError, ValueError):
        data["items"] = []
    return data


class QuoteService:
    def list(
        self, company_id: int, *, conversation_id: int | None = None
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT id, company_id, conversation_id, customer_id, title, "
            "items_json, currency, total, status, notes, created_by_user_id, "
            "created_at, updated_at FROM quotes WHERE company_id = ?"
        )
        params: list[Any] = [int(company_id)]
        if conversation_id is not None:
            query += " AND conversation_id = ?"
            params.append(int(conversation_id))
        query += " ORDER BY created_at DESC, id DESC"

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row(r) for r in rows]

    def get(self, company_id: int, quote_id: int) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT * FROM quotes WHERE id = ? AND company_id = ?",
                (int(quote_id), int(company_id)),
            ).fetchone()
        return _row(row) if row else None

    def create(
        self,
        *,
        company_id: int,
        title: str,
        items: list[dict] | None = None,
        amount: float | None = None,
        currency: str = "USD",
        notes: str | None = None,
        conversation_id: int | None = None,
        customer_id: int | None = None,
        created_by_user_id: int | None = None,
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise QuoteError("A quote needs a title.")
        if len(title) > MAX_TITLE_LENGTH:
            raise QuoteError(f"A quote title must be under {MAX_TITLE_LENGTH} characters.")

        notes = str(notes or "").strip()[:MAX_NOTES_LENGTH] or None
        currency = (str(currency or "USD").strip().upper() or "USD")[:8]

        cleaned_items = _clean_items(items)
        if cleaned_items:
            total = _items_total(cleaned_items)
        else:
            # No line-item editor exists yet, so the common path is a single
            # flat amount typed on the quick-create dialog -- stored as one
            # item so the total is never disconnected from what it represents.
            try:
                flat = max(float(amount or 0), 0)
            except (TypeError, ValueError):
                flat = 0
            if flat <= 0:
                raise QuoteError("A quote needs an amount greater than zero.")
            cleaned_items = [{"name": title, "quantity": 1, "price": flat}]
            total = flat

        now = utc_now_iso()
        with database_manager.tenant(int(company_id)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM quotes WHERE company_id = ?",
                (int(company_id),),
            ).fetchone()["n"]
            if int(count) >= MAX_QUOTES:
                raise QuoteError(f"A company can have at most {MAX_QUOTES} quotes.")

            cursor = conn.execute(
                "INSERT INTO quotes (company_id, conversation_id, customer_id, "
                "title, items_json, currency, total, status, notes, "
                "created_by_user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
                (
                    int(company_id),
                    int(conversation_id) if conversation_id else None,
                    int(customer_id) if customer_id else None,
                    title,
                    json.dumps(cleaned_items),
                    currency,
                    total,
                    notes,
                    int(created_by_user_id) if created_by_user_id else None,
                    now,
                    now,
                ),
            )
            conn.commit()
            new_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT * FROM quotes WHERE id = ? AND company_id = ?",
                (new_id, int(company_id)),
            ).fetchone()
        return _row(row)

    def set_status(
        self, *, company_id: int, quote_id: int, status: str
    ) -> dict[str, Any]:
        status = str(status or "").strip()
        if status not in VALID_STATUSES:
            raise QuoteError(
                f"Status must be one of: {', '.join(VALID_STATUSES)}."
            )
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "UPDATE quotes SET status = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (status, utc_now_iso(), int(quote_id), int(company_id)),
            )
            conn.commit()
            if cursor.rowcount != 1:
                raise QuoteError("That quote does not exist.")
            row = conn.execute(
                "SELECT * FROM quotes WHERE id = ? AND company_id = ?",
                (int(quote_id), int(company_id)),
            ).fetchone()
        return _row(row)

    def delete(self, *, company_id: int, quote_id: int) -> bool:
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "DELETE FROM quotes WHERE id = ? AND company_id = ?",
                (int(quote_id), int(company_id)),
            )
            conn.commit()
            return cursor.rowcount > 0


quote_service = QuoteService()
