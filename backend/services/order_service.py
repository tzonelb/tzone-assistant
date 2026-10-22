"""Customer orders, and fulfilling them.

The customer-facing mirror of `purchasing_service`: an order's own
`quantity_fulfilled` per line is never set directly -- `fulfil_items` is the
only path, and it calls `inventory_service.record_movement` for the same
product first, with `movement_type="sale"`. A line can never be fulfilled
past what stock actually allows: `record_movement` itself refuses a change
that would take a product's stock below zero, so over-fulfilling is refused
at the inventory layer rather than re-implemented here.

Table creation belongs to `database/schema_tenant.py` alone. This service
only reads and writes `orders` and `order_items`.
"""

from __future__ import annotations

from typing import Any

from backend.services.inventory_service import inventory_service
from database.manager import database_manager, utc_now_iso


class OrderError(ValueError):
    """An order request that cannot be carried out as asked."""


class OrderService:
    # `partially_fulfilled` and `fulfilled` are never set directly -- only
    # `fulfil_items` moves a row into either, by summing what its own lines
    # have had shipped.
    STATUS = ("draft", "confirmed", "partially_fulfilled", "fulfilled", "cancelled")
    SETTABLE_STATUS = ("draft", "confirmed", "cancelled")
    FULFILLABLE_STATUS = ("confirmed", "partially_fulfilled")

    MAX_LIMIT = 200
    MAX_ITEMS_PER_ORDER = 200

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _row_to_order(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _row_to_item(row: Any) -> dict[str, Any]:
        return dict(row)

    def _items_for(self, conn, *, company_id: int, order_id: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT order_items.*, products.name AS product_name,
                   products.sku AS product_sku
            FROM order_items
            JOIN products ON products.id = order_items.product_id
            WHERE order_items.order_id = ? AND order_items.company_id = ?
            ORDER BY order_items.id
            """,
            (order_id, company_id),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def list_orders(
        self,
        *,
        company_id: int,
        status: str | None = None,
        customer_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))

        where = ["orders.company_id = ?"]
        params: list[Any] = [company_id]

        status = self._clean(status)
        if status:
            if status not in self.STATUS:
                raise OrderError(f"Status must be one of: {', '.join(self.STATUS)}.")
            where.append("orders.status = ?")
            params.append(status)

        if customer_id is not None:
            where.append("orders.customer_id = ?")
            params.append(int(customer_id))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM orders WHERE {clause}", params
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT orders.*,
                       COALESCE(customers.internal_name, customers.display_name) AS customer_name
                FROM orders
                JOIN customers ON customers.id = orders.customer_id
                WHERE {clause}
                ORDER BY orders.created_at DESC, orders.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {"items": [self._row_to_order(row) for row in rows], "total": total}

    def get_order(self, *, company_id: int, order_id: int) -> dict[str, Any] | None:
        company_id = int(company_id)
        order_id = int(order_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT orders.*,
                       COALESCE(customers.internal_name, customers.display_name) AS customer_name
                FROM orders
                JOIN customers ON customers.id = orders.customer_id
                WHERE orders.id = ? AND orders.company_id = ?
                LIMIT 1
                """,
                (order_id, company_id),
            ).fetchone()

            if not row:
                return None

            order = self._row_to_order(row)
            order["items"] = self._items_for(conn, company_id=company_id, order_id=order_id)

        return order

    def create_order(
        self,
        *,
        company_id: int,
        customer_id: int,
        items: list[dict[str, Any]],
        quote_id: int | None = None,
        conversation_id: int | None = None,
        reference: str | None = None,
        notes: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        customer_id = int(customer_id)

        if not items:
            raise OrderError("An order needs at least one line item.")

        if len(items) > self.MAX_ITEMS_PER_ORDER:
            raise OrderError(
                f"An order cannot carry more than {self.MAX_ITEMS_PER_ORDER} lines."
            )

        clean_items: list[dict[str, Any]] = []
        total = 0.0

        for item in items:
            try:
                product_id = int(item["product_id"])
                quantity = int(item["quantity"])
                unit_price = float(item.get("unit_price") or 0)
            except (KeyError, TypeError, ValueError) as exc:
                raise OrderError(
                    "Each line needs a product, a whole-number quantity and a price."
                ) from exc

            if quantity <= 0:
                raise OrderError("A line's quantity must be greater than zero.")
            if unit_price < 0:
                raise OrderError("A line's price cannot be negative.")

            clean_items.append(
                {"product_id": product_id, "quantity": quantity, "unit_price": unit_price}
            )
            total += quantity * unit_price

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            customer = conn.execute(
                "SELECT id FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
                (customer_id, company_id),
            ).fetchone()
            if not customer:
                raise OrderError("That customer does not exist.")

            if quote_id is not None:
                quote = conn.execute(
                    "SELECT id FROM quotes WHERE id = ? AND company_id = ? LIMIT 1",
                    (int(quote_id), company_id),
                ).fetchone()
                if not quote:
                    raise OrderError("That quote does not exist.")

            if conversation_id is not None:
                conversation = conn.execute(
                    "SELECT id FROM conversations WHERE id = ? AND company_id = ? LIMIT 1",
                    (int(conversation_id), company_id),
                ).fetchone()
                if not conversation:
                    raise OrderError("That conversation does not exist.")

            for line in clean_items:
                product = conn.execute(
                    "SELECT id FROM products WHERE id = ? AND company_id = ? LIMIT 1",
                    (line["product_id"], company_id),
                ).fetchone()
                if not product:
                    raise OrderError(f"Product {line['product_id']} does not exist.")

            cursor = conn.execute(
                """
                INSERT INTO orders (
                    company_id, customer_id, quote_id, conversation_id, status,
                    reference, notes, total, created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, customer_id,
                    int(quote_id) if quote_id is not None else None,
                    int(conversation_id) if conversation_id is not None else None,
                    self._clean(reference), self._clean(notes), total,
                    int(user_id) if user_id is not None else None, now, now,
                ),
            )
            order_id = int(cursor.lastrowid)

            conn.executemany(
                """
                INSERT INTO order_items (
                    company_id, order_id, product_id, quantity,
                    quantity_fulfilled, unit_price, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                [
                    (
                        company_id, order_id, line["product_id"], line["quantity"],
                        line["unit_price"], now, now,
                    )
                    for line in clean_items
                ],
            )
            conn.commit()

        return self.get_order(company_id=company_id, order_id=order_id)

    def update_order_status(
        self, *, company_id: int, order_id: int, status: str
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        order_id = int(order_id)
        status = self._clean(status) or ""

        if status not in self.SETTABLE_STATUS:
            raise OrderError(
                "Status must be one of: "
                + ", ".join(self.SETTABLE_STATUS)
                + " -- partially_fulfilled and fulfilled follow from fulfilling, "
                "they are not set directly."
            )

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT status FROM orders WHERE id = ? AND company_id = ? LIMIT 1",
                (order_id, company_id),
            ).fetchone()

            if not row:
                return None

            current = str(row["status"])
            allowed_from = {
                "confirmed": {"draft"},
                "cancelled": {"draft", "confirmed"},
                "draft": set(),
            }

            if current != status and current not in allowed_from.get(status, set()):
                raise OrderError(f"A {current} order cannot be moved to {status}.")

            conn.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE id = ? AND company_id = ?",
                (status, utc_now_iso(), order_id, company_id),
            )
            conn.commit()

        return self.get_order(company_id=company_id, order_id=order_id)

    def fulfil_items(
        self,
        *,
        company_id: int,
        order_id: int,
        items: list[dict[str, Any]],
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Ship one or more of an order's own lines.

        Each line calls `inventory_service.record_movement` for its product
        before this order's own row is touched -- the same ordering, and the
        same reasoning, as `purchasing_service.receive_items`: a failure on
        one line stops before anything about that line is written, and lines
        already processed in this same call stay applied rather than being
        rolled back behind it.
        """
        company_id = int(company_id)
        order_id = int(order_id)

        if not items:
            raise OrderError("Name at least one line to fulfil.")

        with database_manager.tenant(company_id) as conn:
            order = conn.execute(
                "SELECT id, status FROM orders WHERE id = ? AND company_id = ? LIMIT 1",
                (order_id, company_id),
            ).fetchone()

        if not order:
            return None

        if str(order["status"]) not in self.FULFILLABLE_STATUS:
            raise OrderError(f"A {order['status']} order cannot be fulfilled.")

        for entry in items:
            try:
                item_id = int(entry["item_id"])
                quantity = int(entry["quantity"])
            except (KeyError, TypeError, ValueError) as exc:
                raise OrderError(
                    "Each fulfilment line needs an item id and a whole-number quantity."
                ) from exc

            if quantity <= 0:
                raise OrderError("A fulfilled quantity must be greater than zero.")

            with database_manager.tenant(company_id) as conn:
                line = conn.execute(
                    """
                    SELECT id, product_id, quantity, quantity_fulfilled
                    FROM order_items
                    WHERE id = ? AND order_id = ? AND company_id = ?
                    LIMIT 1
                    """,
                    (item_id, order_id, company_id),
                ).fetchone()

            if not line:
                raise OrderError(f"Line {item_id} does not belong to this order.")

            remaining = int(line["quantity"]) - int(line["quantity_fulfilled"])
            if quantity > remaining:
                raise OrderError(
                    f"Line {item_id} has {remaining} left to fulfil, not {quantity}."
                )

            inventory_service.record_movement(
                company_id=company_id,
                product_id=int(line["product_id"]),
                movement_type="sale",
                quantity_delta=-quantity,
                reference=f"order:{order_id}",
                user_id=user_id,
            )

            with database_manager.tenant(company_id) as conn:
                conn.execute(
                    "UPDATE order_items "
                    "SET quantity_fulfilled = quantity_fulfilled + ?, updated_at = ? "
                    "WHERE id = ? AND company_id = ?",
                    (quantity, utc_now_iso(), item_id, company_id),
                )
                conn.commit()

        with database_manager.tenant(company_id) as conn:
            lines = conn.execute(
                "SELECT quantity, quantity_fulfilled FROM order_items "
                "WHERE order_id = ? AND company_id = ?",
                (order_id, company_id),
            ).fetchall()

            if all(line["quantity_fulfilled"] >= line["quantity"] for line in lines):
                new_status = "fulfilled"
            elif any(line["quantity_fulfilled"] > 0 for line in lines):
                new_status = "partially_fulfilled"
            else:
                new_status = str(order["status"])

            conn.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE id = ? AND company_id = ?",
                (new_status, utc_now_iso(), order_id, company_id),
            )
            conn.commit()

        return self.get_order(company_id=company_id, order_id=order_id)


order_service = OrderService()
