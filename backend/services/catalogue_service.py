from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Products in the Master Catalogue. Status is a fixed, small enumeration
# (like Tasks' STATUSES) so the UI can render predictable filters/selects
# without per-company config. Category is free-form per company (like
# Departments), derived on demand from the rows in use — no separate table.
STATUSES = ["active", "archived"]
DEFAULT_STATUS = "active"


class CatalogueService:
    def __init__(self) -> None:
        # Schema setup happens explicitly via main.py's lifespan (after
        # database.database.db.create_tables()), not here — see the
        # matching note in TaskService.__init__.
        pass

    def ensure_schema(self) -> None:
        # NOTE: database.database.db.create_tables() (which always runs
        # before this, per main.py's lifespan) already creates a "products"
        # table for an unrelated external-sync/business-connectors feature
        # (see core/business_connectors.py, dashboard.py's product count).
        # That legacy table already has company_id/name/sku/description/
        # category/status columns we can reuse, but not price_cents/
        # stock_quantity/image_url/created_by_user_id. Rather than fork a
        # second products table (or touch database.py, which is out of
        # scope here), we create the full desired schema for a genuinely
        # fresh DB and additively ALTER the legacy table when it predates
        # these columns — same "IF NOT EXISTS" idempotency as the rest of
        # this codebase's ensure_schema() methods, just column-level.
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    sku TEXT,
                    description TEXT,
                    category TEXT,
                    price_cents INTEGER NOT NULL DEFAULT 0,
                    stock_quantity INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    image_url TEXT,
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )

            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
            if "price_cents" not in existing_columns:
                conn.execute("ALTER TABLE products ADD COLUMN price_cents INTEGER NOT NULL DEFAULT 0")
            if "stock_quantity" not in existing_columns:
                conn.execute("ALTER TABLE products ADD COLUMN stock_quantity INTEGER NOT NULL DEFAULT 0")
            if "image_url" not in existing_columns:
                conn.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
            if "created_by_user_id" not in existing_columns:
                conn.execute("ALTER TABLE products ADD COLUMN created_by_user_id INTEGER")
            if "created_at" not in existing_columns:
                conn.execute("ALTER TABLE products ADD COLUMN created_at TEXT")
            if "updated_at" not in existing_columns:
                conn.execute("ALTER TABLE products ADD COLUMN updated_at TEXT")

            # Enforce SKU uniqueness per company via a partial unique index
            # (SQLite can't ALTER TABLE ADD CONSTRAINT on an existing table),
            # ignoring NULL skus so legacy/external-sync rows without one
            # don't collide.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_company_sku "
                "ON products(company_id, sku) WHERE sku IS NOT NULL"
            )
            conn.commit()

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _validate_non_negative(value: Any, field_label: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_label} must be a whole number.")
        if number < 0:
            raise ValueError(f"{field_label} cannot be negative.")
        return number

    def create_product(
        self,
        *,
        company_id: int,
        name: str,
        sku: str | None = None,
        description: str | None = None,
        category: str | None = None,
        price_cents: int = 0,
        stock_quantity: int = 0,
        image_url: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        clean_name = self._clean(name)
        if not clean_name:
            raise ValueError("Product name is required.")

        clean_sku = self._clean(sku)
        description = self._clean(description)
        category = self._clean(category)
        image_url = self._clean(image_url)

        clean_price_cents = self._validate_non_negative(price_cents, "Price")
        clean_stock_quantity = self._validate_non_negative(stock_quantity, "Stock quantity")

        now = utc_now_iso()

        with db.connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO products (
                        company_id, name, sku, description, category,
                        price_cents, stock_quantity, status, image_url,
                        created_by_user_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id, clean_name, clean_sku, description, category,
                        clean_price_cents, clean_stock_quantity, DEFAULT_STATUS, image_url,
                        actor_user_id, now, now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("A product with this SKU already exists.") from exc
            product_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_product(company_id=company_id, product_id=product_id)

    def get_product(self, *, company_id: int, product_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Product not found")
        return dict(row)

    def list_products(
        self,
        *,
        company_id: int,
        search: str | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if search and str(search).strip():
            pattern = f"%{str(search).strip()}%"
            where.append("(name LIKE ? OR sku LIKE ? OR description LIKE ?)")
            params.extend([pattern] * 3)

        if category is not None and str(category).strip():
            where.append("category = ?")
            params.append(str(category).strip())

        if status is not None and str(status).strip():
            where.append("status = ?")
            params.append(str(status).strip().lower())

        clause = " AND ".join(where)
        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM products WHERE {clause}", params
            ).fetchone()["total"]
            rows = conn.execute(
                f"SELECT * FROM products WHERE {clause} ORDER BY name ASC",
                params,
            ).fetchall()

        items = [dict(row) for row in rows]
        return {"items": items, "total": int(total or 0)}

    def update_product(
        self,
        *,
        company_id: int,
        product_id: int,
        values: dict[str, Any],
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}

        if "name" in values:
            clean_name = self._clean(values["name"])
            if not clean_name:
                raise ValueError("Product name is required.")
            cleaned["name"] = clean_name

        if "sku" in values:
            cleaned["sku"] = self._clean(values["sku"])

        if "description" in values:
            cleaned["description"] = self._clean(values["description"])

        if "category" in values:
            cleaned["category"] = self._clean(values["category"])

        if "image_url" in values:
            cleaned["image_url"] = self._clean(values["image_url"])

        if "price_cents" in values and values["price_cents"] is not None:
            cleaned["price_cents"] = self._validate_non_negative(values["price_cents"], "Price")

        if "stock_quantity" in values and values["stock_quantity"] is not None:
            cleaned["stock_quantity"] = self._validate_non_negative(values["stock_quantity"], "Stock quantity")

        if "status" in values and values["status"] is not None:
            new_status = str(values["status"]).strip().lower()
            if new_status not in STATUSES:
                raise ValueError(f'"{new_status}" is not a valid status. Choose one of: {", ".join(STATUSES)}.')
            cleaned["status"] = new_status

        if not cleaned:
            return self.get_product(company_id=company_id, product_id=product_id)

        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Product not found")

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            try:
                conn.execute(
                    f"UPDATE products SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                    [*cleaned.values(), now, product_id, company_id],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("A product with this SKU already exists.") from exc
            conn.commit()

        return self.get_product(company_id=company_id, product_id=product_id)

    def delete_product(self, *, company_id: int, product_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Product not found")

    def list_categories(self, *, company_id: int) -> list[str]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM products WHERE company_id = ? AND category IS NOT NULL ORDER BY category ASC",
                (company_id,),
            ).fetchall()
        return [row["category"] for row in rows]


catalogue_service = CatalogueService()
