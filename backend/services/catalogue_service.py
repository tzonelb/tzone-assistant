"""Company-scoped CRUD for the Master Catalogue module: one product
catalogue the AI bot can reference when answering product questions.
Mirrors the layered service pattern in task_service.py -- the `products`
table itself lives in database/database.py's central schema init, so this
module does not own/create its own tables and has no ensure_schema() of
its own to call at startup or in tests.

This is deliberately independent of core/business_connectors.py, which
only gates whether the "products" connector is enabled at all
(is_enabled("products", company_id)) and does not read catalogue rows
itself -- that stub is a future integration point, not something this
service needs to coordinate with."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ALLOWED_STATUSES = {"active", "archived", "out_of_stock"}
ALLOWED_AVAILABILITY = {"in_stock", "out_of_stock", "preorder", "discontinued"}


class CatalogueConflictError(Exception):
    """Raised when an update's optimistic-concurrency token is stale, i.e.
    the product was changed by someone else since the client loaded it."""


class CatalogueValidationError(ValueError):
    """Raised for invalid field values: a bad status/availability code or
    an empty name."""


class CatalogueService:
    EDITABLE_FIELDS = {
        "sku",
        "name",
        "description",
        "category",
        "brand",
        "price",
        "currency",
        "quantity",
        "availability_status",
        "status",
    }

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _clean_values(self, values: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in values.items():
            if key not in self.EDITABLE_FIELDS:
                continue
            if key in ("sku", "name", "description", "category", "brand"):
                cleaned[key] = self._clean_text(value)
            elif key == "currency":
                currency_value = self._clean_text(value)
                cleaned[key] = (currency_value or "USD").upper()[:8]
            elif key == "status":
                status_value = self._clean_text(value)
                status_value = (status_value or "active").lower()
                if status_value not in ALLOWED_STATUSES:
                    raise CatalogueValidationError(
                        f"status must be one of {sorted(ALLOWED_STATUSES)}"
                    )
                cleaned[key] = status_value
            elif key == "availability_status":
                availability_value = self._clean_text(value)
                if availability_value is None:
                    cleaned[key] = None
                    continue
                availability_value = availability_value.lower()
                if availability_value not in ALLOWED_AVAILABILITY:
                    raise CatalogueValidationError(
                        f"availability_status must be one of {sorted(ALLOWED_AVAILABILITY)}"
                    )
                cleaned[key] = availability_value
            elif key in ("price", "quantity"):
                if value is None or value == "":
                    cleaned[key] = None
                    continue
                try:
                    cleaned[key] = float(value)
                except (TypeError, ValueError) as exc:
                    raise CatalogueValidationError(f"{key} must be a number") from exc
            else:
                cleaned[key] = value
        return cleaned

    def list_products(
        self,
        *,
        company_id: int,
        status: str | None = None,
        category: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if status and status != "all":
            where.append("status = ?")
            params.append(status)

        if category and category != "all":
            where.append("category = ?")
            params.append(category)

        if search and search.strip():
            pattern = f"%{search.strip()}%"
            where.append("(name LIKE ? OR sku LIKE ? OR description LIKE ?)")
            params.extend([pattern, pattern, pattern])

        clause = " AND ".join(where)

        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM products WHERE {clause}", params
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                SELECT * FROM products
                WHERE {clause}
                ORDER BY
                    CASE status WHEN 'active' THEN 0 WHEN 'out_of_stock' THEN 1 ELSE 2 END,
                    name COLLATE NOCASE ASC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def get_product(self, *, company_id: int, product_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE id = ? AND company_id = ? LIMIT 1",
                (product_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Product not found")
        return dict(row)

    def create_product(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        name = cleaned.get("name")
        if not name:
            raise CatalogueValidationError("name is required")

        status_value = cleaned.get("status") or "active"
        currency_value = cleaned.get("currency") or "USD"
        now = utc_now_iso()

        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO products (
                    company_id, sku, name, description, category, brand,
                    price, currency, quantity, availability_status, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    cleaned.get("sku"),
                    name,
                    cleaned.get("description"),
                    cleaned.get("category"),
                    cleaned.get("brand"),
                    cleaned.get("price"),
                    currency_value,
                    cleaned.get("quantity"),
                    cleaned.get("availability_status"),
                    status_value,
                    now,
                    now,
                ),
            )
            product_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_product(company_id=company_id, product_id=product_id)

    def update_product(
        self,
        *,
        company_id: int,
        product_id: int,
        values: dict[str, Any],
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id, updated_at FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Product not found")

            # Optimistic concurrency: if the caller told us which version
            # they were editing, refuse to overwrite a record that has
            # since moved on. Runs even for a no-op save so a stale editor
            # is always told to reload rather than silently "succeeding".
            if (
                expected_updated_at is not None
                and str(existing["updated_at"]) != str(expected_updated_at)
            ):
                raise CatalogueConflictError(
                    "This product was changed elsewhere. Reload to see the "
                    "latest details before editing."
                )

            if "name" in cleaned and not cleaned["name"]:
                raise CatalogueValidationError("name cannot be empty")

            if not cleaned:
                return self.get_product(company_id=company_id, product_id=product_id)

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE products SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, product_id, company_id],
            )
            conn.commit()

        return self.get_product(company_id=company_id, product_id=product_id)

    def delete_product(self, *, company_id: int, product_id: int) -> bool:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_categories(self, *, company_id: int) -> list[str]:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT category FROM products
                WHERE company_id = ? AND category IS NOT NULL AND category != ''
                ORDER BY category COLLATE NOCASE ASC
                """,
                (company_id,),
            ).fetchall()
        return [row["category"] for row in rows]


catalogue_service = CatalogueService()
