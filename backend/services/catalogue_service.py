from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone
from typing import Any

import requests

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Products in the Master Catalogue. Status is a fixed, small enumeration
# (like Tasks' STATUSES) so the UI can render predictable filters/selects
# without per-company config. Category is free-form per company (like
# Departments), derived on demand from the rows in use — no separate table.
STATUSES = ["active", "archived"]
DEFAULT_STATUS = "active"


def _log_activity(*, company_id: int, actor_user_id: int | None, action: str, entity_id: int | None, description: str) -> None:
    try:
        from backend.services.activity_log_service import activity_log_service
        activity_log_service.record(
            company_id=company_id, actor_user_id=actor_user_id, action=action,
            entity_type="product", entity_id=entity_id, description=description,
        )
    except Exception:
        pass


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

        _log_activity(
            company_id=company_id, actor_user_id=actor_user_id, action="product_created",
            entity_id=product_id, description=f'Created product "{clean_name}"',
        )
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

    def delete_product(self, *, company_id: int, product_id: int, actor_user_id: int | None = None) -> None:
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT name FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            ).fetchone()
            cursor = conn.execute(
                "DELETE FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Product not found")

        _log_activity(
            company_id=company_id, actor_user_id=actor_user_id, action="product_deleted",
            entity_id=product_id, description=f'Deleted product "{existing["name"] if existing else product_id}"',
        )

    def _upsert_import_row(
        self, conn, *, company_id: int, name: str, sku: str | None, description: str | None,
        category: str | None, price_cents: int, stock_quantity: int, image_url: str | None,
        actor_user_id: int | None, now: str,
    ) -> str:
        """Shared by every import source: create a product, or update it
        in place if a product with this SKU already exists for this
        company — imports are meant to be re-run (a "Sync" button), so
        they must be idempotent rather than erroring on the SKU that
        was created by the previous sync. Returns "created" or "updated"."""
        existing = None
        if sku:
            existing = conn.execute(
                "SELECT id FROM products WHERE company_id = ? AND sku = ?", (company_id, sku),
            ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE products SET name = ?, description = ?, category = ?,
                    price_cents = ?, stock_quantity = ?, image_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, description, category, price_cents, stock_quantity, image_url, now, existing["id"]),
            )
            return "updated"

        conn.execute(
            """
            INSERT INTO products (
                company_id, name, sku, description, category,
                price_cents, stock_quantity, status, image_url,
                created_by_user_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id, name, sku, description, category,
                price_cents, stock_quantity, DEFAULT_STATUS, image_url,
                actor_user_id, now, now,
            ),
        )
        return "created"

    def import_from_csv(self, *, company_id: int, file_content: bytes, actor_user_id: int | None = None) -> dict[str, Any]:
        """Column names are matched case-insensitively; only "name" is
        required. Works for a CSV exported from pretty much any POS or
        website product-catalog admin panel, which is why this single
        importer covers both of those sources rather than needing a
        bespoke integration per vendor (that's a much bigger, separate
        ask — see the e-commerce-platform integration item)."""
        try:
            text = file_content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Could not read this file as UTF-8 text — export it as a plain CSV.") from exc

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("This file has no header row — the first line must name the columns.")
        field_map = {str(field).strip().lower(): field for field in reader.fieldnames}
        if "name" not in field_map:
            raise ValueError('This file needs a "name" column.')

        created = 0
        updated = 0
        errors: list[str] = []
        now = utc_now_iso()

        with db.connect() as conn:
            for index, row in enumerate(reader, start=2):  # header is line 1
                name = self._clean(row.get(field_map["name"]))
                if not name:
                    errors.append(f"Row {index}: missing name, skipped.")
                    continue
                sku = self._clean(row.get(field_map.get("sku", ""))) if "sku" in field_map else None
                description = self._clean(row.get(field_map.get("description", ""))) if "description" in field_map else None
                category = self._clean(row.get(field_map.get("category", ""))) if "category" in field_map else None
                image_url = self._clean(row.get(field_map.get("image_url", ""))) if "image_url" in field_map else None

                price_cents = 0
                if "price" in field_map:
                    raw_price = self._clean(row.get(field_map["price"]))
                    if raw_price:
                        try:
                            price_cents = round(float(raw_price) * 100)
                        except ValueError:
                            errors.append(f'Row {index}: "{raw_price}" is not a valid price, defaulted to 0.')

                stock_quantity = 0
                if "stock_quantity" in field_map or "stock" in field_map:
                    raw_stock = self._clean(row.get(field_map.get("stock_quantity") or field_map.get("stock")))
                    if raw_stock:
                        try:
                            stock_quantity = int(float(raw_stock))
                        except ValueError:
                            errors.append(f'Row {index}: "{raw_stock}" is not a valid stock quantity, defaulted to 0.')

                try:
                    outcome = self._upsert_import_row(
                        conn, company_id=company_id, name=name, sku=sku, description=description,
                        category=category, price_cents=max(price_cents, 0), stock_quantity=max(stock_quantity, 0),
                        image_url=image_url, actor_user_id=actor_user_id, now=now,
                    )
                    if outcome == "created":
                        created += 1
                    else:
                        updated += 1
                except sqlite3.IntegrityError:
                    errors.append(f"Row {index}: could not save (duplicate SKU conflict).")
            conn.commit()

        return {"created": created, "updated": updated, "errors": errors}

    def import_from_whatsapp_catalog(
        self, *, company_id: int, catalog_id: str, actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Pulls products from a Meta Commerce catalog linked to the
        company's WhatsApp Business Account (Graph API's Catalog
        Product endpoint — this is the same catalog WhatsApp's own
        "View catalog" button in a chat reads from). Reuses the token
        from the company's already-connected WhatsApp channel account
        rather than asking the user to paste a raw access token."""
        from backend.services.channel_account_service import channel_account_service
        from config.settings import config

        access_token = channel_account_service.get_active_token(company_id=company_id, channel="whatsapp")
        if not access_token:
            raise ValueError("Connect a WhatsApp channel first (Company Settings → Channels) before importing its catalogue.")

        created = 0
        updated = 0
        errors: list[str] = []
        now = utc_now_iso()

        url = f"https://graph.facebook.com/{config.META_API_VERSION}/{catalog_id}/products"
        params = {
            "fields": "name,description,price,retailer_id,availability,image_url",
            "access_token": access_token,
            "limit": 100,
        }

        with db.connect() as conn:
            pages_fetched = 0
            while url and pages_fetched < 50:  # hard cap — a runaway pagination loop should never hang a request indefinitely
                try:
                    response = requests.get(url, params=params if pages_fetched == 0 else None, timeout=30)
                    data = response.json()
                except requests.RequestException as exc:
                    raise ValueError(f"Could not reach Meta to fetch this catalog: {exc}") from exc

                if "error" in data:
                    raise ValueError(data["error"].get("message", "Meta rejected this catalog request."))

                for item in data.get("data", []):
                    name = self._clean(item.get("name"))
                    if not name:
                        continue
                    sku = self._clean(item.get("retailer_id"))
                    description = self._clean(item.get("description"))
                    image_url = self._clean(item.get("image_url"))

                    price_cents = 0
                    raw_price = str(item.get("price") or "").strip()
                    if raw_price:
                        numeric_part = "".join(char for char in raw_price if char.isdigit() or char == ".")
                        try:
                            price_cents = round(float(numeric_part) * 100) if numeric_part else 0
                        except ValueError:
                            price_cents = 0

                    try:
                        outcome = self._upsert_import_row(
                            conn, company_id=company_id, name=name, sku=sku, description=description,
                            category="WhatsApp Catalogue", price_cents=price_cents, stock_quantity=0,
                            image_url=image_url, actor_user_id=actor_user_id, now=now,
                        )
                        if outcome == "created":
                            created += 1
                        else:
                            updated += 1
                    except sqlite3.IntegrityError:
                        errors.append(f'"{name}": could not save (duplicate SKU conflict).')

                url = (data.get("paging", {}) or {}).get("next")
                pages_fetched += 1
            conn.commit()

        return {"created": created, "updated": updated, "errors": errors}

    def list_categories(self, *, company_id: int) -> list[str]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM products WHERE company_id = ? AND category IS NOT NULL ORDER BY category ASC",
                (company_id,),
            ).fetchall()
        return [row["category"] for row in rows]


catalogue_service = CatalogueService()
