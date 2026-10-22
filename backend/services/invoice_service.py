"""Invoices, and the payments recorded against them.

`amount_paid` on an invoice is never set directly -- `record_payment` is the
only path that moves it, by inserting the `payments` row and updating the
invoice's own total in the same call, so the two can never disagree about how
much has actually come in. An invoice cannot be overpaid: `record_payment`
refuses a payment that would take `amount_paid` past `total`, before either
row is written.

Table creation belongs to `database/schema_tenant.py` alone. This service
only reads and writes `invoices`, `invoice_items` and `payments`.
"""

from __future__ import annotations

from typing import Any

from database.manager import database_manager, utc_now_iso


class InvoiceError(ValueError):
    """An invoicing request that cannot be carried out as asked."""


class InvoiceService:
    # `partially_paid` and `paid` are never set directly -- only
    # `record_payment` moves a row into either, from what `payments` sums to.
    STATUS = ("draft", "sent", "partially_paid", "paid", "void")
    SETTABLE_STATUS = ("draft", "sent", "void")
    PAYABLE_STATUS = ("sent", "partially_paid")

    METHODS = ("cash", "card", "transfer", "other")

    MAX_LIMIT = 200
    MAX_ITEMS_PER_INVOICE = 200

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
    def _row_to_invoice(row: Any) -> dict[str, Any]:
        invoice = dict(row)
        invoice["balance"] = round(
            float(invoice.get("total") or 0) - float(invoice.get("amount_paid") or 0), 2
        )
        return invoice

    @staticmethod
    def _row_to_item(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _row_to_payment(row: Any) -> dict[str, Any]:
        return dict(row)

    def _items_for(self, conn, *, company_id: int, invoice_id: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT invoice_items.*, products.name AS product_name
            FROM invoice_items
            LEFT JOIN products ON products.id = invoice_items.product_id
            WHERE invoice_items.invoice_id = ? AND invoice_items.company_id = ?
            ORDER BY invoice_items.id
            """,
            (invoice_id, company_id),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def _payments_for(self, conn, *, company_id: int, invoice_id: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM payments
            WHERE invoice_id = ? AND company_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (invoice_id, company_id),
        ).fetchall()
        return [self._row_to_payment(row) for row in rows]

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------

    def list_invoices(
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

        where = ["invoices.company_id = ?"]
        params: list[Any] = [company_id]

        status = self._clean(status)
        if status:
            if status not in self.STATUS:
                raise InvoiceError(f"Status must be one of: {', '.join(self.STATUS)}.")
            where.append("invoices.status = ?")
            params.append(status)

        if customer_id is not None:
            where.append("invoices.customer_id = ?")
            params.append(int(customer_id))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM invoices WHERE {clause}", params
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT invoices.*,
                       COALESCE(customers.internal_name, customers.display_name) AS customer_name
                FROM invoices
                JOIN customers ON customers.id = invoices.customer_id
                WHERE {clause}
                ORDER BY invoices.created_at DESC, invoices.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {"items": [self._row_to_invoice(row) for row in rows], "total": total}

    def get_invoice(self, *, company_id: int, invoice_id: int) -> dict[str, Any] | None:
        company_id = int(company_id)
        invoice_id = int(invoice_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT invoices.*,
                       COALESCE(customers.internal_name, customers.display_name) AS customer_name
                FROM invoices
                JOIN customers ON customers.id = invoices.customer_id
                WHERE invoices.id = ? AND invoices.company_id = ?
                LIMIT 1
                """,
                (invoice_id, company_id),
            ).fetchone()

            if not row:
                return None

            invoice = self._row_to_invoice(row)
            invoice["items"] = self._items_for(conn, company_id=company_id, invoice_id=invoice_id)
            invoice["payments"] = self._payments_for(
                conn, company_id=company_id, invoice_id=invoice_id
            )

        return invoice

    def create_invoice(
        self,
        *,
        company_id: int,
        customer_id: int,
        items: list[dict[str, Any]],
        order_id: int | None = None,
        due_date: str | None = None,
        reference: str | None = None,
        notes: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        customer_id = int(customer_id)

        if not items:
            raise InvoiceError("An invoice needs at least one line item.")

        if len(items) > self.MAX_ITEMS_PER_INVOICE:
            raise InvoiceError(
                f"An invoice cannot carry more than {self.MAX_ITEMS_PER_INVOICE} lines."
            )

        clean_items: list[dict[str, Any]] = []
        total = 0.0

        for item in items:
            description = self._clean(item.get("description"))
            if not description:
                raise InvoiceError("Each line needs a description.")

            try:
                quantity = int(item.get("quantity") or 1)
                unit_price = float(item.get("unit_price") or 0)
                product_id = item.get("product_id")
                product_id = int(product_id) if product_id is not None else None
            except (TypeError, ValueError) as exc:
                raise InvoiceError(
                    "Each line needs a whole-number quantity and a price."
                ) from exc

            if quantity <= 0:
                raise InvoiceError("A line's quantity must be greater than zero.")
            if unit_price < 0:
                raise InvoiceError("A line's price cannot be negative.")

            clean_items.append(
                {
                    "description": description, "quantity": quantity,
                    "unit_price": unit_price, "product_id": product_id,
                }
            )
            total += quantity * unit_price

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            customer = conn.execute(
                "SELECT id FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
                (customer_id, company_id),
            ).fetchone()
            if not customer:
                raise InvoiceError("That customer does not exist.")

            if order_id is not None:
                order = conn.execute(
                    "SELECT id FROM orders WHERE id = ? AND company_id = ? LIMIT 1",
                    (int(order_id), company_id),
                ).fetchone()
                if not order:
                    raise InvoiceError("That order does not exist.")

            for line in clean_items:
                if line["product_id"] is not None:
                    product = conn.execute(
                        "SELECT id FROM products WHERE id = ? AND company_id = ? LIMIT 1",
                        (line["product_id"], company_id),
                    ).fetchone()
                    if not product:
                        raise InvoiceError(f"Product {line['product_id']} does not exist.")

            cursor = conn.execute(
                """
                INSERT INTO invoices (
                    company_id, customer_id, order_id, status, reference, notes,
                    total, amount_paid, due_date, created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, 'draft', ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    company_id, customer_id,
                    int(order_id) if order_id is not None else None,
                    self._clean(reference), self._clean(notes), total,
                    self._clean(due_date),
                    int(user_id) if user_id is not None else None, now, now,
                ),
            )
            invoice_id = int(cursor.lastrowid)

            conn.executemany(
                """
                INSERT INTO invoice_items (
                    company_id, invoice_id, product_id, description, quantity,
                    unit_price, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        company_id, invoice_id, line["product_id"], line["description"],
                        line["quantity"], line["unit_price"], now,
                    )
                    for line in clean_items
                ],
            )
            conn.commit()

        return self.get_invoice(company_id=company_id, invoice_id=invoice_id)

    def update_invoice_status(
        self, *, company_id: int, invoice_id: int, status: str
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        invoice_id = int(invoice_id)
        status = self._clean(status) or ""

        if status not in self.SETTABLE_STATUS:
            raise InvoiceError(
                "Status must be one of: "
                + ", ".join(self.SETTABLE_STATUS)
                + " -- partially_paid and paid follow from recording payments, "
                "they are not set directly."
            )

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT status FROM invoices WHERE id = ? AND company_id = ? LIMIT 1",
                (invoice_id, company_id),
            ).fetchone()

            if not row:
                return None

            current = str(row["status"])
            allowed_from = {
                "sent": {"draft"},
                "void": {"draft", "sent"},
                "draft": set(),
            }

            if current != status and current not in allowed_from.get(status, set()):
                raise InvoiceError(f"A {current} invoice cannot be moved to {status}.")

            conn.execute(
                "UPDATE invoices SET status = ?, updated_at = ? WHERE id = ? AND company_id = ?",
                (status, utc_now_iso(), invoice_id, company_id),
            )
            conn.commit()

        return self.get_invoice(company_id=company_id, invoice_id=invoice_id)

    def record_payment(
        self,
        *,
        company_id: int,
        invoice_id: int,
        amount: float,
        method: str = "cash",
        reference: str | None = None,
        notes: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        invoice_id = int(invoice_id)

        try:
            amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise InvoiceError("The amount must be a number.") from exc

        if amount <= 0:
            raise InvoiceError("A payment must be greater than zero.")

        method = (self._clean(method) or "cash").lower()
        if method not in self.METHODS:
            raise InvoiceError(f"Method must be one of: {', '.join(self.METHODS)}.")

        with database_manager.tenant(company_id) as conn:
            invoice = conn.execute(
                "SELECT status, total, amount_paid FROM invoices "
                "WHERE id = ? AND company_id = ? LIMIT 1",
                (invoice_id, company_id),
            ).fetchone()

            if not invoice:
                return None

            if str(invoice["status"]) not in self.PAYABLE_STATUS:
                raise InvoiceError(
                    f"A {invoice['status']} invoice cannot take a payment."
                )

            new_paid = float(invoice["amount_paid"]) + amount
            total = float(invoice["total"])

            if new_paid > total + 0.01:
                remaining = round(total - float(invoice["amount_paid"]), 2)
                raise InvoiceError(
                    f"This invoice has {remaining} left to pay, not {amount}."
                )

            now = utc_now_iso()

            conn.execute(
                """
                INSERT INTO payments (
                    company_id, invoice_id, amount, method, reference, notes,
                    received_by_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, invoice_id, amount, method,
                    self._clean(reference), self._clean(notes),
                    int(user_id) if user_id is not None else None, now,
                ),
            )

            new_status = "paid" if new_paid >= total - 0.01 else "partially_paid"

            conn.execute(
                "UPDATE invoices SET amount_paid = ?, status = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (new_paid, new_status, now, invoice_id, company_id),
            )
            conn.commit()

        return self.get_invoice(company_id=company_id, invoice_id=invoice_id)


invoice_service = InvoiceService()
