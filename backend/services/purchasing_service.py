"""Suppliers, and the purchase orders raised against them.

A purchase order's own received-quantity is not a separate fact from the
product's stock history -- `receive_items` is the only path that advances it,
and every call also records the receipt through `inventory_service`, so a
product's stock movements and this order's own line items are two views
of the same write rather than two ledgers that could drift apart.

`status` moves through draft -> sent -> {partially_received, received} ->
(cancelled from draft or sent only). The two receiving states are never set
directly: `update_order_status` refuses them, because they are not a
decision anyone makes -- they are what the line items, summed, already say.

Table creation belongs to `database/schema_tenant.py` alone. This service
only reads and writes `suppliers`, `purchase_orders` and
`purchase_order_items`.
"""

from __future__ import annotations

from typing import Any

from backend.services.inventory_service import inventory_service
from database.manager import database_manager, utc_now_iso


class PurchasingError(ValueError):
    """A purchasing request that cannot be carried out as asked."""


class PurchasingService:
    SUPPLIER_STATUS = ("active", "archived")

    # The full lifecycle a row can be in.
    ORDER_STATUS = ("draft", "sent", "partially_received", "received", "cancelled")
    # What `update_order_status` may be asked to set directly. The other two
    # are `receive_items`'s alone to reach.
    SETTABLE_ORDER_STATUS = ("draft", "sent", "cancelled")
    # The only state a line item may be received against.
    RECEIVABLE_ORDER_STATUS = ("sent", "partially_received")

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

    def _normalize_supplier_status(self, value: Any, default: str = "active") -> str:
        status = (self._clean(value) or default).lower()
        if status not in self.SUPPLIER_STATUS:
            raise PurchasingError(
                f"Supplier status must be one of: {', '.join(self.SUPPLIER_STATUS)}."
            )
        return status

    @staticmethod
    def _row_to_supplier(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _row_to_order(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _row_to_item(row: Any) -> dict[str, Any]:
        return dict(row)

    # ------------------------------------------------------------------
    # Suppliers
    # ------------------------------------------------------------------

    def list_suppliers(
        self,
        *,
        company_id: int,
        search: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        search = self._clean(search)
        if search:
            pattern = f"%{search}%"
            where.append("(name LIKE ? OR contact_name LIKE ? OR email LIKE ?)")
            params.extend([pattern] * 3)

        status = self._clean(status)
        if status:
            where.append("status = ?")
            params.append(self._normalize_supplier_status(status))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM suppliers WHERE {clause}", params
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM suppliers
                WHERE {clause}
                ORDER BY name COLLATE NOCASE
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {"items": [self._row_to_supplier(row) for row in rows], "total": total}

    def get_supplier(self, *, company_id: int, supplier_id: int) -> dict[str, Any] | None:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT * FROM suppliers WHERE id = ? AND company_id = ? LIMIT 1",
                (int(supplier_id), company_id),
            ).fetchone()

        return self._row_to_supplier(row) if row else None

    def create_supplier(self, *, company_id: int, data: dict[str, Any]) -> dict[str, Any]:
        company_id = int(company_id)

        name = self._clean(data.get("name"))
        if not name:
            raise PurchasingError("A supplier needs a name.")

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO suppliers (
                    company_id, name, contact_name, email, phone, address,
                    notes, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, name,
                    self._clean(data.get("contact_name")),
                    self._clean(data.get("email")),
                    self._clean(data.get("phone")),
                    self._clean(data.get("address")),
                    self._clean(data.get("notes")),
                    self._normalize_supplier_status(data.get("status")),
                    now, now,
                ),
            )
            conn.commit()
            supplier_id = int(cursor.lastrowid)

        return self.get_supplier(company_id=company_id, supplier_id=supplier_id)

    def update_supplier(
        self, *, company_id: int, supplier_id: int, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        supplier_id = int(supplier_id)

        columns: dict[str, Any] = {}

        if "name" in values:
            name = self._clean(values["name"])
            if not name:
                raise PurchasingError("A supplier needs a name.")
            columns["name"] = name

        for field in ("contact_name", "email", "phone", "address", "notes"):
            if field in values:
                columns[field] = self._clean(values[field])

        if "status" in values:
            columns["status"] = self._normalize_supplier_status(values["status"])

        if not columns:
            return self.get_supplier(company_id=company_id, supplier_id=supplier_id)

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                "SELECT id FROM suppliers WHERE id = ? AND company_id = ? LIMIT 1",
                (supplier_id, company_id),
            ).fetchone()

            if not existing:
                return None

            assignments = ", ".join(f"{name} = ?" for name in columns)
            conn.execute(
                f"UPDATE suppliers SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*columns.values(), utc_now_iso(), supplier_id, company_id],
            )
            conn.commit()

        return self.get_supplier(company_id=company_id, supplier_id=supplier_id)

    def archive_supplier(self, *, company_id: int, supplier_id: int) -> bool:
        """Retire a supplier rather than erase it -- past purchase orders still
        name it, and a hard delete would leave their `supplier_id` pointing at
        nothing."""
        company_id = int(company_id)
        supplier_id = int(supplier_id)

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                "UPDATE suppliers SET status = 'archived', updated_at = ? "
                "WHERE id = ? AND company_id = ? AND status != 'archived'",
                (utc_now_iso(), supplier_id, company_id),
            )
            conn.commit()

        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Purchase orders
    # ------------------------------------------------------------------

    def list_orders(
        self,
        *,
        company_id: int,
        status: str | None = None,
        supplier_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))

        where = ["purchase_orders.company_id = ?"]
        params: list[Any] = [company_id]

        status = self._clean(status)
        if status:
            if status not in self.ORDER_STATUS:
                raise PurchasingError(
                    f"Status must be one of: {', '.join(self.ORDER_STATUS)}."
                )
            where.append("purchase_orders.status = ?")
            params.append(status)

        if supplier_id is not None:
            where.append("purchase_orders.supplier_id = ?")
            params.append(int(supplier_id))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM purchase_orders WHERE {clause}",
                    params,
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT purchase_orders.*, suppliers.name AS supplier_name
                FROM purchase_orders
                JOIN suppliers ON suppliers.id = purchase_orders.supplier_id
                WHERE {clause}
                ORDER BY purchase_orders.created_at DESC, purchase_orders.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {"items": [self._row_to_order(row) for row in rows], "total": total}

    def _items_for(self, conn, *, company_id: int, order_id: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT purchase_order_items.*, products.name AS product_name,
                   products.sku AS product_sku
            FROM purchase_order_items
            JOIN products ON products.id = purchase_order_items.product_id
            WHERE purchase_order_items.purchase_order_id = ?
              AND purchase_order_items.company_id = ?
            ORDER BY purchase_order_items.id
            """,
            (order_id, company_id),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get_order(self, *, company_id: int, order_id: int) -> dict[str, Any] | None:
        company_id = int(company_id)
        order_id = int(order_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT purchase_orders.*, suppliers.name AS supplier_name
                FROM purchase_orders
                JOIN suppliers ON suppliers.id = purchase_orders.supplier_id
                WHERE purchase_orders.id = ? AND purchase_orders.company_id = ?
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
        supplier_id: int,
        items: list[dict[str, Any]],
        reference: str | None = None,
        notes: str | None = None,
        expected_date: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        supplier_id = int(supplier_id)

        if not items:
            raise PurchasingError("A purchase order needs at least one line item.")

        if len(items) > self.MAX_ITEMS_PER_ORDER:
            raise PurchasingError(
                f"A purchase order cannot carry more than {self.MAX_ITEMS_PER_ORDER} lines."
            )

        clean_items: list[dict[str, Any]] = []
        total = 0.0

        for item in items:
            try:
                product_id = int(item["product_id"])
                quantity = int(item["quantity"])
                unit_cost = float(item.get("unit_cost") or 0)
            except (KeyError, TypeError, ValueError) as exc:
                raise PurchasingError(
                    "Each line needs a product, a whole-number quantity and a cost."
                ) from exc

            if quantity <= 0:
                raise PurchasingError("A line's quantity must be greater than zero.")
            if unit_cost < 0:
                raise PurchasingError("A line's cost cannot be negative.")

            clean_items.append(
                {"product_id": product_id, "quantity": quantity, "unit_cost": unit_cost}
            )
            total += quantity * unit_cost

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            supplier = conn.execute(
                "SELECT id FROM suppliers WHERE id = ? AND company_id = ? LIMIT 1",
                (supplier_id, company_id),
            ).fetchone()

            if not supplier:
                raise PurchasingError("That supplier does not exist.")

            for line in clean_items:
                product = conn.execute(
                    "SELECT id FROM products WHERE id = ? AND company_id = ? LIMIT 1",
                    (line["product_id"], company_id),
                ).fetchone()
                if not product:
                    raise PurchasingError(
                        f"Product {line['product_id']} does not exist."
                    )

            cursor = conn.execute(
                """
                INSERT INTO purchase_orders (
                    company_id, supplier_id, status, reference, notes,
                    expected_date, total, created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, supplier_id, self._clean(reference), self._clean(notes),
                    self._clean(expected_date), total,
                    int(user_id) if user_id is not None else None, now, now,
                ),
            )
            order_id = int(cursor.lastrowid)

            conn.executemany(
                """
                INSERT INTO purchase_order_items (
                    company_id, purchase_order_id, product_id, quantity_ordered,
                    quantity_received, unit_cost, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                [
                    (
                        company_id, order_id, line["product_id"], line["quantity"],
                        line["unit_cost"], now, now,
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

        if status not in self.SETTABLE_ORDER_STATUS:
            raise PurchasingError(
                "Status must be one of: "
                + ", ".join(self.SETTABLE_ORDER_STATUS)
                + " -- partially_received and received follow from receiving, "
                "they are not set directly."
            )

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT status FROM purchase_orders WHERE id = ? AND company_id = ? LIMIT 1",
                (order_id, company_id),
            ).fetchone()

            if not row:
                return None

            current = str(row["status"])
            allowed_from = {
                "sent": {"draft"},
                "cancelled": {"draft", "sent"},
                "draft": set(),
            }

            if current != status and current not in allowed_from.get(status, set()):
                raise PurchasingError(f"A {current} order cannot be moved to {status}.")

            conn.execute(
                "UPDATE purchase_orders SET status = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (status, utc_now_iso(), order_id, company_id),
            )
            conn.commit()

        return self.get_order(company_id=company_id, order_id=order_id)

    def receive_items(
        self,
        *,
        company_id: int,
        order_id: int,
        items: list[dict[str, Any]],
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Record delivery against one or more of an order's own lines.

        Each line calls `inventory_service.record_movement` for its product
        before this order's own row is touched, so the two ledgers are never
        out of step: a failure on one line (an over-receive, say) stops before
        anything about that line is written, and lines already processed in
        this same call stay applied rather than being rolled back behind it --
        the simplest rule that is still always true: this order's
        quantity_received for a line is never ahead of what the product's own
        stock history shows was actually received.
        """
        company_id = int(company_id)
        order_id = int(order_id)

        if not items:
            raise PurchasingError("Name at least one line to receive.")

        with database_manager.tenant(company_id) as conn:
            order = conn.execute(
                "SELECT id, status FROM purchase_orders WHERE id = ? AND company_id = ? LIMIT 1",
                (order_id, company_id),
            ).fetchone()

        if not order:
            return None

        if str(order["status"]) not in self.RECEIVABLE_ORDER_STATUS:
            raise PurchasingError(
                f"A {order['status']} order cannot receive stock."
            )

        for entry in items:
            try:
                item_id = int(entry["item_id"])
                quantity = int(entry["quantity"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PurchasingError(
                    "Each receiving line needs an item id and a whole-number quantity."
                ) from exc

            if quantity <= 0:
                raise PurchasingError("A received quantity must be greater than zero.")

            with database_manager.tenant(company_id) as conn:
                line = conn.execute(
                    """
                    SELECT id, product_id, quantity_ordered, quantity_received
                    FROM purchase_order_items
                    WHERE id = ? AND purchase_order_id = ? AND company_id = ?
                    LIMIT 1
                    """,
                    (item_id, order_id, company_id),
                ).fetchone()

            if not line:
                raise PurchasingError(f"Line {item_id} does not belong to this order.")

            remaining = int(line["quantity_ordered"]) - int(line["quantity_received"])
            if quantity > remaining:
                raise PurchasingError(
                    f"Line {item_id} has {remaining} left to receive, not {quantity}."
                )

            inventory_service.record_movement(
                company_id=company_id,
                product_id=int(line["product_id"]),
                movement_type="receipt",
                quantity_delta=quantity,
                reference=f"purchase_order:{order_id}",
                user_id=user_id,
            )

            with database_manager.tenant(company_id) as conn:
                conn.execute(
                    "UPDATE purchase_order_items "
                    "SET quantity_received = quantity_received + ?, updated_at = ? "
                    "WHERE id = ? AND company_id = ?",
                    (quantity, utc_now_iso(), item_id, company_id),
                )
                conn.commit()

        with database_manager.tenant(company_id) as conn:
            lines = conn.execute(
                "SELECT quantity_ordered, quantity_received FROM purchase_order_items "
                "WHERE purchase_order_id = ? AND company_id = ?",
                (order_id, company_id),
            ).fetchall()

            if all(line["quantity_received"] >= line["quantity_ordered"] for line in lines):
                new_status = "received"
            elif any(line["quantity_received"] > 0 for line in lines):
                new_status = "partially_received"
            else:
                new_status = str(order["status"])

            conn.execute(
                "UPDATE purchase_orders SET status = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (new_status, utc_now_iso(), order_id, company_id),
            )
            conn.commit()

        return self.get_order(company_id=company_id, order_id=order_id)


purchasing_service = PurchasingService()
