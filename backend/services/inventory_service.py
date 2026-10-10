"""Stock on hand, stored inside the owning company's database.

`products.stock_quantity` is the number every other screen already reads --
the catalogue list, the assistant's stock answers, the Inventory screen this
module adds. This service is the only writer of it. A quantity is never set
directly: every change is `record_movement`, which computes the new balance
from the product's current one and files the row in `stock_movements` that
explains it, so "why does this product show 12" always has an answer.

The ledger is the foundation Purchasing and Orders are meant to write through
once they exist (see PROJECT_MASTER.md's commerce roadmap): a received
purchase order calls this with `movement_type="receipt"`, a completed sale
calls it with `movement_type="sale"`, and `products.stock_quantity` stays the
single number both of them -- and the catalogue -- agree on.

Table creation belongs to `database/schema_tenant.py` alone. This service only
reads and writes `products` and `stock_movements`.
"""

from __future__ import annotations

from typing import Any

from database.manager import database_manager, utc_now_iso


class InventoryService:
    MOVEMENT_TYPES = ("receipt", "sale", "return", "adjustment", "write_off")

    MAX_LIMIT = 200

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _product_summary(row: Any) -> dict[str, Any]:
        product = dict(row)
        product["in_stock"] = bool(product.get("in_stock"))
        return product

    @staticmethod
    def _movement_row(row: Any) -> dict[str, Any]:
        return dict(row)

    # ------------------------------------------------------------------
    # Movements
    # ------------------------------------------------------------------

    def record_movement(
        self,
        *,
        company_id: int,
        product_id: int,
        movement_type: str,
        quantity_delta: int,
        reason: str | None = None,
        reference: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Apply one signed change to a product's stock and file the ledger row.

        Returns ``None`` when the product does not exist (or belongs to
        another company), the same way `catalogue_service.update_product`
        tells a caller "nothing to update" rather than raising -- that is a
        404, not a bad request.
        """
        company_id = int(company_id)
        product_id = int(product_id)
        quantity_delta = int(quantity_delta)

        if movement_type not in self.MOVEMENT_TYPES:
            raise ValueError(
                f"Movement type must be one of: {', '.join(self.MOVEMENT_TYPES)}."
            )

        if quantity_delta == 0:
            raise ValueError("A stock movement must change the quantity.")

        reason = (reason or "").strip() or None
        reference = (reference or "").strip() or None
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            product = conn.execute(
                """
                SELECT id, stock_quantity FROM products
                WHERE id = ? AND company_id = ?
                LIMIT 1
                """,
                (product_id, company_id),
            ).fetchone()

            if not product:
                return None

            current = int(product["stock_quantity"] or 0)
            new_quantity = current + quantity_delta

            if new_quantity < 0:
                raise ValueError(
                    f"Not enough stock: {current} on hand, "
                    f"cannot apply a change of {quantity_delta}."
                )

            conn.execute(
                """
                UPDATE products
                SET stock_quantity = ?, in_stock = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (new_quantity, 1 if new_quantity > 0 else 0, now, product_id, company_id),
            )

            cursor = conn.execute(
                """
                INSERT INTO stock_movements (
                    company_id, product_id, movement_type, quantity_delta,
                    quantity_after, reason, reference, created_by_user_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, product_id, movement_type, quantity_delta,
                    new_quantity, reason, reference,
                    int(user_id) if user_id is not None else None, now,
                ),
            )
            conn.commit()
            movement_id = int(cursor.lastrowid)

        return self.get_movement(company_id=company_id, movement_id=movement_id)

    def get_movement(
        self, *, company_id: int, movement_id: int
    ) -> dict[str, Any] | None:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT stock_movements.*, products.name AS product_name,
                       products.sku AS product_sku
                FROM stock_movements
                JOIN products ON products.id = stock_movements.product_id
                WHERE stock_movements.id = ? AND stock_movements.company_id = ?
                LIMIT 1
                """,
                (int(movement_id), company_id),
            ).fetchone()

        return self._movement_row(row) if row else None

    def list_movements(
        self,
        *,
        company_id: int,
        product_id: int | None = None,
        movement_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))

        where = ["stock_movements.company_id = ?"]
        params: list[Any] = [company_id]

        if product_id is not None:
            where.append("stock_movements.product_id = ?")
            params.append(int(product_id))

        if movement_type:
            if movement_type not in self.MOVEMENT_TYPES:
                raise ValueError(
                    f"Movement type must be one of: {', '.join(self.MOVEMENT_TYPES)}."
                )
            where.append("stock_movements.movement_type = ?")
            params.append(movement_type)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM stock_movements WHERE {clause}",
                    params,
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT stock_movements.*, products.name AS product_name,
                       products.sku AS product_sku
                FROM stock_movements
                JOIN products ON products.id = stock_movements.product_id
                WHERE {clause}
                ORDER BY stock_movements.created_at DESC, stock_movements.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {
            "items": [self._movement_row(row) for row in rows],
            "total": total,
        }

    # ------------------------------------------------------------------
    # Reorder point
    # ------------------------------------------------------------------

    def set_reorder_point(
        self,
        *,
        company_id: int,
        product_id: int,
        reorder_point: int | None,
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        product_id = int(product_id)

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                "SELECT id FROM products WHERE id = ? AND company_id = ? LIMIT 1",
                (product_id, company_id),
            ).fetchone()

            if not existing:
                return None

            conn.execute(
                """
                UPDATE products
                SET reorder_point = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (
                    int(reorder_point) if reorder_point is not None else None,
                    utc_now_iso(),
                    product_id,
                    company_id,
                ),
            )
            conn.commit()

        return self._get_product(company_id=company_id, product_id=product_id)

    def _get_product(
        self, *, company_id: int, product_id: int
    ) -> dict[str, Any] | None:
        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT id, sku, name, stock_quantity, in_stock, reorder_point, status
                FROM products
                WHERE id = ? AND company_id = ?
                LIMIT 1
                """,
                (product_id, company_id),
            ).fetchone()

        return self._product_summary(row) if row else None

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def low_stock_products(self, *, company_id: int) -> list[dict[str, Any]]:
        """Active products at or under their own reorder point.

        A product with no reorder point set never appears here -- silence
        about a threshold the company never set is correct, not a gap.
        """
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT id, sku, name, stock_quantity, in_stock, reorder_point, status
                FROM products
                WHERE company_id = ?
                  AND status = 'active'
                  AND reorder_point IS NOT NULL
                  AND COALESCE(stock_quantity, 0) <= reorder_point
                ORDER BY (COALESCE(stock_quantity, 0) - reorder_point) ASC, name COLLATE NOCASE
                """,
                (company_id,),
            ).fetchall()

        return [self._product_summary(row) for row in rows]

    def stock_summary(self, *, company_id: int) -> dict[str, Any]:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_products,
                    COALESCE(SUM(COALESCE(stock_quantity, 0)), 0) AS total_units,
                    SUM(CASE WHEN COALESCE(stock_quantity, 0) <= 0 THEN 1 ELSE 0 END)
                        AS out_of_stock_count,
                    SUM(
                        CASE WHEN reorder_point IS NOT NULL
                             AND COALESCE(stock_quantity, 0) <= reorder_point
                        THEN 1 ELSE 0 END
                    ) AS low_stock_count
                FROM products
                WHERE company_id = ? AND status = 'active'
                """,
                (company_id,),
            ).fetchone()

        return {
            "total_products": int(row["total_products"] or 0),
            "total_units": int(row["total_units"] or 0),
            "out_of_stock_count": int(row["out_of_stock_count"] or 0),
            "low_stock_count": int(row["low_stock_count"] or 0),
        }


inventory_service = InventoryService()
