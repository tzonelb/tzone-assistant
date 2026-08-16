"""The product catalogue, stored inside the owning company's database.

Two very different callers read this module.

The screen reads it through the ``/api/catalogue`` router, where the company is
resolved from the caller's token.

The assistant reads it through ``search_for_assistant``. Those rows are the only
thing standing between a customer asking "how much is the iPhone 15?" and the
model inventing a number: ``core/ai_router.py`` treats a connector result marked
``ok`` as a verified fact and stops replacing prices in the reply. So the
assistant path is deliberately narrow — active products only, matched against
this company's own catalogue, and never a row the company does not sell.

Table creation belongs to ``database/schema_tenant.py`` alone. This service only
reads and writes ``products`` and ``product_categories``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CatalogueService:
    ALLOWED_STATUS = ("active", "draft", "archived")

    STOCK_FILTERS = ("in_stock", "out_of_stock")

    MAX_LIMIT = 200

    PRODUCT_FIELDS = (
        "category_id",
        "sku",
        "name",
        "name_en",
        "description",
        "brand",
        "price",
        "sale_price",
        "currency",
        "stock_quantity",
        "in_stock",
        "image_url",
        "attributes",
        "status",
    )

    # Words that appear in almost every product question and match almost every
    # product row. Left in, "do you have a phone" would return the whole
    # catalogue and the assistant would answer about an unrelated item.
    ASSISTANT_STOPWORDS = frozenset(
        {
            "a", "an", "and", "any", "are", "at", "available", "cost", "do",
            "does", "for", "got", "have", "how", "in", "is", "it", "many",
            "me", "much", "of", "or", "please", "price", "prices", "sell",
            "stock", "tell", "the", "there", "this", "to", "want", "what",
            "whats", "you", "your",
            "بكم", "بدي", "بدنا", "على", "عن", "عندك", "عندكم", "في", "فيه",
            "كم", "لو", "ما", "متوفر", "متوفرة", "موجود", "موجودة", "هل",
            "هلق", "و", "يا", "سعر", "سعرها", "سعره", "السعر", "كمية",
        }
    )

    # How many tokens of a customer message are turned into SQL conditions. A
    # long message must not build an unbounded query.
    MAX_ASSISTANT_TOKENS = 6

    MAX_ASSISTANT_RESULTS = 5

    # A shorter token matches too much: "sa" would hit every Samsung row.
    MIN_TOKEN_LENGTH = 3

    ASSISTANT_PRICE_PHRASES = (
        "price", "how much", "cost", "$",
        "سعر", "قديش", "بكم", "كم حق",
    )

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
    def _money(value: Any) -> float | None:
        if value is None or value == "":
            return None

        try:
            amount = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Price must be a number.") from exc

        if amount < 0:
            raise ValueError("Price cannot be negative.")

        return amount

    @staticmethod
    def _quantity(value: Any) -> int | None:
        if value is None or value == "":
            return None

        try:
            quantity = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Stock quantity must be a whole number.") from exc

        if quantity < 0:
            raise ValueError("Stock quantity cannot be negative.")

        return quantity

    def _normalize_status(self, value: Any, default: str = "active") -> str:
        status = (self._clean(value) or default).lower()

        if status not in self.ALLOWED_STATUS:
            raise ValueError(
                f"Status must be one of: {', '.join(self.ALLOWED_STATUS)}."
            )

        return status

    @staticmethod
    def _attributes_json(value: Any) -> str:
        if value is None or value == "":
            return "{}"

        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("Attributes must be valid JSON.") from exc
        else:
            parsed = value

        if not isinstance(parsed, dict):
            raise ValueError("Attributes must be a JSON object.")

        return json.dumps(parsed, ensure_ascii=False)

    @staticmethod
    def _parse_attributes(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

        return parsed if isinstance(parsed, dict) else {}

    def _require_category(self, conn, *, company_id: int, category_id: int) -> None:
        row = conn.execute(
            """
            SELECT id FROM product_categories
            WHERE id = ? AND company_id = ?
            LIMIT 1
            """,
            (int(category_id), int(company_id)),
        ).fetchone()

        if not row:
            raise ValueError("That product category does not exist.")

    def _require_free_sku(
        self,
        conn,
        *,
        company_id: int,
        sku: str,
        product_id: int | None = None,
    ) -> None:
        row = conn.execute(
            """
            SELECT id FROM products
            WHERE company_id = ? AND sku = ? AND id IS NOT ?
            LIMIT 1
            """,
            (int(company_id), sku, product_id),
        ).fetchone()

        if row:
            raise ValueError(f"A product with SKU {sku!r} already exists.")

    def _row_to_product(self, row: Any) -> dict[str, Any]:
        product = dict(row)
        product["attributes"] = self._parse_attributes(product.get("attributes_json"))
        product["in_stock"] = bool(product.get("in_stock"))
        return product

    def _values_to_columns(self, values: dict[str, Any]) -> dict[str, Any]:
        """Translate one screen payload into database columns.

        Only keys actually present are translated, so a partial update never
        blanks a field the form did not send.
        """
        columns: dict[str, Any] = {}

        for field in self.PRODUCT_FIELDS:
            if field not in values:
                continue

            value = values[field]

            if field in ("name", "name_en", "description", "brand", "sku", "image_url"):
                columns[field] = self._clean(value)
            elif field == "currency":
                currency = self._clean(value)
                columns[field] = (currency or "USD").upper()[:8]
            elif field in ("price", "sale_price"):
                columns[field] = self._money(value)
            elif field == "stock_quantity":
                columns[field] = self._quantity(value)
            elif field == "in_stock":
                columns[field] = 1 if bool(value) else 0
            elif field == "category_id":
                columns[field] = int(value) if value is not None else None
            elif field == "attributes":
                columns["attributes_json"] = self._attributes_json(value)
            elif field == "status":
                columns[field] = self._normalize_status(value)

        return columns

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def list_products(
        self,
        *,
        company_id: int,
        search: str | None = None,
        category_id: int | None = None,
        stock: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))

        where = ["products.company_id = ?"]
        params: list[Any] = [company_id]

        search = self._clean(search)
        if search:
            pattern = f"%{search}%"
            where.append(
                "(products.name LIKE ? OR products.name_en LIKE ? "
                "OR products.sku LIKE ? OR products.brand LIKE ?)"
            )
            params.extend([pattern] * 4)

        if category_id is not None:
            where.append("products.category_id = ?")
            params.append(int(category_id))

        stock = self._clean(stock)
        if stock:
            if stock not in self.STOCK_FILTERS:
                raise ValueError(
                    f"Stock filter must be one of: {', '.join(self.STOCK_FILTERS)}."
                )

            where.append("products.in_stock = ?")
            params.append(1 if stock == "in_stock" else 0)

        status = self._clean(status)
        if status:
            where.append("products.status = ?")
            params.append(self._normalize_status(status))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM products WHERE {clause}",
                    params,
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT products.*, categories.name AS category_name
                FROM products
                LEFT JOIN product_categories categories
                    ON categories.id = products.category_id
                WHERE {clause}
                ORDER BY products.updated_at DESC, products.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {
            "items": [self._row_to_product(row) for row in rows],
            "total": total,
        }

    def get_product(
        self,
        *,
        company_id: int,
        product_id: int,
    ) -> dict[str, Any] | None:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT products.*, categories.name AS category_name
                FROM products
                LEFT JOIN product_categories categories
                    ON categories.id = products.category_id
                WHERE products.id = ? AND products.company_id = ?
                LIMIT 1
                """,
                (int(product_id), company_id),
            ).fetchone()

        return self._row_to_product(row) if row else None

    def create_product(
        self,
        *,
        company_id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        company_id = int(company_id)

        columns = self._values_to_columns(data)

        if not columns.get("name"):
            raise ValueError("A product needs a name.")

        columns.setdefault("currency", "USD")
        columns.setdefault("status", "active")
        columns.setdefault("attributes_json", "{}")

        if "in_stock" not in columns:
            quantity = columns.get("stock_quantity")
            columns["in_stock"] = 0 if quantity == 0 else 1

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            if columns.get("category_id") is not None:
                self._require_category(
                    conn, company_id=company_id, category_id=columns["category_id"]
                )

            if columns.get("sku"):
                self._require_free_sku(
                    conn, company_id=company_id, sku=columns["sku"]
                )

            names = list(columns)
            placeholders = ", ".join("?" for _ in names)

            cursor = conn.execute(
                f"""
                INSERT INTO products (
                    company_id, {", ".join(names)}, created_at, updated_at
                )
                VALUES (?, {placeholders}, ?, ?)
                """,
                [company_id, *columns.values(), now, now],
            )
            conn.commit()
            product_id = int(cursor.lastrowid)

        logger.info("Created product id=%s company id=%s", product_id, company_id)

        return self.get_product(company_id=company_id, product_id=product_id)

    def update_product(
        self,
        *,
        company_id: int,
        product_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        product_id = int(product_id)

        columns = self._values_to_columns(values)

        if "name" in columns and not columns["name"]:
            raise ValueError("A product needs a name.")

        if not columns:
            return self.get_product(company_id=company_id, product_id=product_id)

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                "SELECT id FROM products WHERE id = ? AND company_id = ? LIMIT 1",
                (product_id, company_id),
            ).fetchone()

            if not existing:
                return None

            if columns.get("category_id") is not None:
                self._require_category(
                    conn, company_id=company_id, category_id=columns["category_id"]
                )

            if columns.get("sku"):
                self._require_free_sku(
                    conn,
                    company_id=company_id,
                    sku=columns["sku"],
                    product_id=product_id,
                )

            assignments = ", ".join(f"{name} = ?" for name in columns)

            conn.execute(
                f"""
                UPDATE products
                SET {assignments}, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                [*columns.values(), utc_now_iso(), product_id, company_id],
            )
            conn.commit()

        logger.info(
            "Updated product id=%s company id=%s fields=%s",
            product_id,
            company_id,
            sorted(columns),
        )

        return self.get_product(company_id=company_id, product_id=product_id)

    def delete_product(self, *, company_id: int, product_id: int) -> bool:
        company_id = int(company_id)
        product_id = int(product_id)

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                "DELETE FROM products WHERE id = ? AND company_id = ?",
                (product_id, company_id),
            )
            conn.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info("Deleted product id=%s company id=%s", product_id, company_id)

        return deleted

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def list_categories(self, *, company_id: int) -> list[dict[str, Any]]:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT categories.*,
                       (
                           SELECT COUNT(*)
                           FROM products
                           WHERE products.category_id = categories.id
                       ) AS product_count
                FROM product_categories categories
                WHERE categories.company_id = ?
                ORDER BY categories.sort_order, categories.name COLLATE NOCASE
                """,
                (company_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_category(
        self,
        *,
        company_id: int,
        category_id: int,
    ) -> dict[str, Any] | None:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT categories.*,
                       (
                           SELECT COUNT(*)
                           FROM products
                           WHERE products.category_id = categories.id
                       ) AS product_count
                FROM product_categories categories
                WHERE categories.id = ? AND categories.company_id = ?
                LIMIT 1
                """,
                (int(category_id), company_id),
            ).fetchone()

        return dict(row) if row else None

    def create_category(
        self,
        *,
        company_id: int,
        name: str,
        parent_id: int | None = None,
        sort_order: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        clean_name = self._clean(name)

        if not clean_name:
            raise ValueError("A category needs a name.")

        normalized_status = self._normalize_status(status)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                """
                SELECT id FROM product_categories
                WHERE company_id = ? AND name = ?
                LIMIT 1
                """,
                (company_id, clean_name),
            ).fetchone()

            if existing:
                raise ValueError(f"A category named {clean_name!r} already exists.")

            if parent_id is not None:
                self._require_category(
                    conn, company_id=company_id, category_id=int(parent_id)
                )

            cursor = conn.execute(
                """
                INSERT INTO product_categories (
                    company_id, name, parent_id, sort_order,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    clean_name,
                    int(parent_id) if parent_id is not None else None,
                    int(sort_order or 0),
                    normalized_status,
                    now,
                    now,
                ),
            )
            conn.commit()
            category_id = int(cursor.lastrowid)

        logger.info(
            "Created product category id=%s company id=%s", category_id, company_id
        )

        return self.get_category(company_id=company_id, category_id=category_id)

    def update_category(
        self,
        *,
        company_id: int,
        category_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        category_id = int(category_id)

        columns: dict[str, Any] = {}

        if "name" in values:
            clean_name = self._clean(values["name"])

            if not clean_name:
                raise ValueError("A category needs a name.")

            columns["name"] = clean_name

        if "parent_id" in values:
            parent_id = values["parent_id"]

            if parent_id is not None and int(parent_id) == category_id:
                raise ValueError("A category cannot be its own parent.")

            columns["parent_id"] = int(parent_id) if parent_id is not None else None

        if "sort_order" in values:
            columns["sort_order"] = int(values["sort_order"] or 0)

        if "status" in values:
            columns["status"] = self._normalize_status(values["status"])

        if not columns:
            return self.get_category(company_id=company_id, category_id=category_id)

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                """
                SELECT id FROM product_categories
                WHERE id = ? AND company_id = ?
                LIMIT 1
                """,
                (category_id, company_id),
            ).fetchone()

            if not existing:
                return None

            if columns.get("name"):
                clash = conn.execute(
                    """
                    SELECT id FROM product_categories
                    WHERE company_id = ? AND name = ? AND id != ?
                    LIMIT 1
                    """,
                    (company_id, columns["name"], category_id),
                ).fetchone()

                if clash:
                    raise ValueError(
                        f"A category named {columns['name']!r} already exists."
                    )

            if columns.get("parent_id") is not None:
                self._require_category(
                    conn, company_id=company_id, category_id=columns["parent_id"]
                )

            assignments = ", ".join(f"{name} = ?" for name in columns)

            conn.execute(
                f"""
                UPDATE product_categories
                SET {assignments}, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                [*columns.values(), utc_now_iso(), category_id, company_id],
            )
            conn.commit()

        return self.get_category(company_id=company_id, category_id=category_id)

    def delete_category(self, *, company_id: int, category_id: int) -> bool:
        """Remove a category and unfile its products.

        Deleting the category must not delete the products in it: the rows the
        assistant quotes prices from would disappear because somebody tidied up
        the category list.
        """
        company_id = int(company_id)
        category_id = int(category_id)

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                UPDATE products
                SET category_id = NULL, updated_at = ?
                WHERE category_id = ? AND company_id = ?
                """,
                (utc_now_iso(), category_id, company_id),
            )
            cursor = conn.execute(
                "DELETE FROM product_categories WHERE id = ? AND company_id = ?",
                (category_id, company_id),
            )
            conn.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info(
                "Deleted product category id=%s company id=%s",
                category_id,
                company_id,
            )

        return deleted

    # ------------------------------------------------------------------
    # The assistant
    # ------------------------------------------------------------------

    def assistant_tokens(self, query: str) -> list[str]:
        """The searchable words of a customer message.

        Punctuation and the words that appear in every product question are
        dropped. What is left is what the customer actually named — a brand, a
        model or a SKU.
        """
        lowered = str(query or "").lower()
        parts = re.split(r"[^0-9a-z؀-ۿ]+", lowered)

        tokens: list[str] = []

        for part in parts:
            if not part or part in self.ASSISTANT_STOPWORDS:
                continue

            # Model numbers are short and are exactly what identifies a product,
            # so digits survive the minimum length.
            if len(part) < self.MIN_TOKEN_LENGTH and not part.isdigit():
                continue

            if part not in tokens:
                tokens.append(part)

        return tokens[: self.MAX_ASSISTANT_TOKENS]

    def search_for_assistant(
        self,
        *,
        company_id: int,
        query: str,
        limit: int = MAX_ASSISTANT_RESULTS,
    ) -> list[dict[str, Any]]:
        """Products this company sells that the customer's message names.

        Active products only. A draft or archived row is something the company
        has not published, and the assistant quoting its price to a customer is
        the same mistake as inventing one.
        """
        company_id = int(company_id)
        tokens = self.assistant_tokens(query)

        if not tokens:
            return []

        limit = max(1, min(int(limit), self.MAX_ASSISTANT_RESULTS))

        # One score point per token found in the product's identifying columns,
        # so "samsung a54" ranks the A54 above every other Samsung.
        match_sql = (
            "(products.name LIKE ? OR products.name_en LIKE ? "
            "OR products.sku LIKE ? OR products.brand LIKE ?)"
        )

        score_parts: list[str] = []
        score_params: list[Any] = []
        where_parts: list[str] = []
        where_params: list[Any] = []

        for token in tokens:
            pattern = f"%{token}%"
            score_parts.append(f"(CASE WHEN {match_sql} THEN 1 ELSE 0 END)")
            score_params.extend([pattern] * 4)
            where_parts.append(match_sql)
            where_params.extend([pattern] * 4)

        score_sql = " + ".join(score_parts)
        where_sql = " OR ".join(where_parts)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT products.*,
                       categories.name AS category_name,
                       ({score_sql}) AS match_score
                FROM products
                LEFT JOIN product_categories categories
                    ON categories.id = products.category_id
                WHERE products.company_id = ?
                  AND products.status = 'active'
                  AND ({where_sql})
                ORDER BY match_score DESC, products.in_stock DESC,
                         products.name COLLATE NOCASE
                LIMIT ?
                """,
                [*score_params, company_id, *where_params, limit],
            ).fetchall()

        return [self.assistant_fact(row) for row in rows]

    def assistant_fact(self, row: Any) -> dict[str, Any]:
        """One product reduced to the facts the assistant may state.

        Internal columns are left out on purpose: the model is handed only what
        a customer is allowed to be told.
        """
        product = self._row_to_product(row)

        price = product.get("price")
        sale_price = product.get("sale_price")
        effective_price = sale_price if sale_price is not None else price

        return {
            "id": product.get("id"),
            "sku": product.get("sku"),
            "name": product.get("name"),
            "name_en": product.get("name_en"),
            "brand": product.get("brand"),
            "category": product.get("category_name"),
            "description": product.get("description"),
            "price": price,
            "sale_price": sale_price,
            "effective_price": effective_price,
            "currency": product.get("currency"),
            "price_confirmed": effective_price is not None,
            "in_stock": bool(product.get("in_stock")),
            "stock_quantity": product.get("stock_quantity"),
            "attributes": product.get("attributes"),
        }

    def message_asks_price(self, query: str) -> bool:
        lowered = str(query or "").lower()
        return any(phrase in lowered for phrase in self.ASSISTANT_PRICE_PHRASES)


catalogue_service = CatalogueService()
