import json
import logging
import sqlite3
from pathlib import Path

from database.database import db


logger = logging.getLogger(__name__)


class KnowledgeManager:
    BASE_FILE = Path("config") / "knowledge_base.json"
    TRAINING_FILE = Path("config") / "training_knowledge.json"

    # Item type used to tag rows created through the FAQ CRUD API
    # (backend/api/routes/knowledge.py) so they stay distinguishable from
    # other future knowledge_items rows. This is a separate, DB-backed,
    # per-company store — it is NOT read by list_for_ai(), which still
    # serves the live AI engine from the JSON files below.
    FAQ_ITEM_TYPE = "faq"

    def load_json_items(self, path: Path) -> list[dict]:
        if not path.exists():
            return []

        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data.get("items", [])

    def load_static_items(self) -> list[dict]:
        """The original, company-agnostic knowledge source (static JSON
        files shared by every company). Kept as its own method so it can
        be used both as the no-company_id path (fully backward compatible)
        and as the per-company fallback when a company has no DB-configured
        knowledge yet."""
        items = []
        items.extend(self.load_json_items(self.BASE_FILE))
        items.extend(self.load_json_items(self.TRAINING_FILE))
        return items

    def load_db_items(self, company_id: int) -> list[dict]:
        """Company-scoped knowledge from the knowledge_items /
        knowledge_categories tables that Company Settings already writes
        to. Returns [] (never raises) if the tables are missing or the
        query fails, so callers can safely fall back to the static files."""
        try:
            with db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        knowledge_items.id AS id,
                        knowledge_items.external_id AS external_id,
                        knowledge_items.title AS title,
                        knowledge_items.content_ar AS content_ar,
                        knowledge_items.content_en AS content_en,
                        knowledge_items.instructions AS instructions,
                        knowledge_items.priority AS priority,
                        COALESCE(
                            knowledge_items.department,
                            knowledge_categories.department,
                            'information'
                        ) AS department
                    FROM knowledge_items
                    LEFT JOIN knowledge_categories
                        ON knowledge_categories.id = knowledge_items.category_id
                    WHERE knowledge_items.company_id = ?
                        AND knowledge_items.status = 'active'
                    ORDER BY knowledge_items.priority DESC, knowledge_items.id ASC
                    """,
                    (company_id,),
                ).fetchall()
        except sqlite3.Error:
            logger.exception(
                "Failed to load DB knowledge items for company_id=%s; "
                "falling back to static knowledge files",
                company_id,
            )
            return []

        return [self.row_to_item(row) for row in rows]

    def row_to_item(self, row: sqlite3.Row) -> dict:
        external_id = row["external_id"]
        item_id = external_id if external_id else f"db_{row['id']}"

        return {
            "id": item_id,
            "department": row["department"] or "information",
            "title": row["title"] or "",
            "content_ar": row["content_ar"] or "",
            "content_en": row["content_en"] or "",
            "instructions": row["instructions"] or "",
            "priority": row["priority"] or 0,
        }

    def load_items(self, company_id: int | None = None) -> list[dict]:
        """
        company_id=None (the default) preserves the exact pre-existing
        behavior: only the shared static JSON files are read. This is the
        safety net for any caller that has not been updated to pass a
        company_id yet.

        When a company_id is given, this looks up that company's own
        DB-configured knowledge first. If the company has zero active rows
        in the DB (e.g. a freshly registered company, or before the admin
        has added anything through Company Settings), it falls back to the
        same static files used today so the bot is never left with an
        empty knowledge base.
        """
        if company_id is None:
            return self.load_static_items()

        db_items = self.load_db_items(company_id)

        if db_items:
            return db_items

        return self.load_static_items()

    def list_for_ai(
        self,
        department: str | None = None,
        company_id: int | None = None,
    ) -> list[dict]:
        items = self.load_items(company_id=company_id)

        if not department:
            return items

        return [
            item for item in items
            if item.get("department") in [department, "information"]
        ]

    # ------------------------------------------------------------------
    # Company-scoped FAQ CRUD, backed by the knowledge_items /
    # knowledge_categories DB tables (database/database.py). Used by
    # backend/api/routes/knowledge.py. `service` maps onto the existing
    # knowledge_items.department column.
    # ------------------------------------------------------------------

    @staticmethod
    def _category_id(conn, company_id: int, category_name: str | None) -> int | None:
        if not category_name:
            return None

        name = str(category_name).strip()

        if not name:
            return None

        row = conn.execute(
            """
            SELECT id
            FROM knowledge_categories
            WHERE company_id = ?
              AND name = ?
            """,
            (company_id, name),
        ).fetchone()

        if row:
            return row["id"]

        cursor = conn.execute(
            """
            INSERT INTO knowledge_categories (company_id, name)
            VALUES (?, ?)
            """,
            (company_id, name),
        )

        return cursor.lastrowid

    @staticmethod
    def _category_name(conn, category_id: int | None) -> str | None:
        if not category_id:
            return None

        row = conn.execute(
            "SELECT name FROM knowledge_categories WHERE id = ?",
            (category_id,),
        ).fetchone()

        return row["name"] if row else None

    def _row_to_faq(self, conn, row: dict) -> dict:
        return {
            "id": row.get("external_id") or str(row["id"]),
            "title_ar": row.get("title_ar"),
            "title_en": row.get("title"),
            "body_ar": row.get("content_ar"),
            "body_en": row.get("content_en"),
            "department": row.get("department"),
            "category": self._category_name(conn, row.get("category_id")),
            "enabled": row.get("status") == "active",
        }

    def list_all_faqs(self, company_id: int) -> list[dict]:
        """Every FAQ item for a single company, across all departments.

        The per-department list_faqs() only answers "what is in this one
        department"; the AI Teaching management UI needs the whole
        company-scoped set at once so it can group items by department and
        category. Strictly scoped to the given company_id -- it never returns
        another tenant's rows."""
        from database.database import db

        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM knowledge_items
                WHERE company_id = ?
                  AND item_type = ?
                ORDER BY department ASC, priority DESC, id ASC
                """,
                (company_id, self.FAQ_ITEM_TYPE),
            ).fetchall()

            return [self._row_to_faq(conn, dict(row)) for row in rows]

    def list_faqs(self, company_id: int, service: str) -> list[dict]:
        from database.database import db

        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM knowledge_items
                WHERE company_id = ?
                  AND department = ?
                  AND item_type = ?
                ORDER BY priority DESC, id ASC
                """,
                (company_id, service, self.FAQ_ITEM_TYPE),
            ).fetchall()

            return [self._row_to_faq(conn, dict(row)) for row in rows]

    def get_faq(self, company_id: int, service: str, faq_id: str) -> dict | None:
        from database.database import db

        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM knowledge_items
                WHERE company_id = ?
                  AND department = ?
                  AND item_type = ?
                  AND external_id = ?
                """,
                (company_id, service, self.FAQ_ITEM_TYPE, faq_id),
            ).fetchone()

            if not row:
                return None

            return self._row_to_faq(conn, dict(row))

    def save_faq(
        self,
        company_id: int,
        service: str,
        faq: dict,
        actor_user_id: int | None = None,
    ) -> dict:
        from database.database import db

        faq_id = str(faq.get("id") or "").strip()

        if not faq_id:
            raise ValueError("FAQ id is required.")

        title_en = str(faq.get("title_en") or "").strip()

        if not title_en:
            raise ValueError("title_en is required.")

        status = "active" if faq.get("enabled", True) else "inactive"

        with db.connect() as conn:
            category_id = self._category_id(conn, company_id, faq.get("category"))

            existing = conn.execute(
                """
                SELECT id
                FROM knowledge_items
                WHERE company_id = ?
                  AND external_id = ?
                """,
                (company_id, faq_id),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE knowledge_items
                    SET title = ?,
                        title_ar = ?,
                        content_ar = ?,
                        content_en = ?,
                        category_id = ?,
                        department = ?,
                        item_type = ?,
                        status = ?,
                        version = version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        title_en,
                        faq.get("title_ar"),
                        faq.get("body_ar"),
                        faq.get("body_en"),
                        category_id,
                        service,
                        self.FAQ_ITEM_TYPE,
                        status,
                        existing["id"],
                    ),
                )
                item_id = existing["id"]
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO knowledge_items (
                        company_id,
                        category_id,
                        external_id,
                        title,
                        title_ar,
                        content_ar,
                        content_en,
                        department,
                        item_type,
                        source_type,
                        status,
                        created_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)
                    """,
                    (
                        company_id,
                        category_id,
                        faq_id,
                        title_en,
                        faq.get("title_ar"),
                        faq.get("body_ar"),
                        faq.get("body_en"),
                        service,
                        self.FAQ_ITEM_TYPE,
                        status,
                        actor_user_id,
                    ),
                )
                item_id = cursor.lastrowid

            conn.commit()

            row = conn.execute(
                "SELECT * FROM knowledge_items WHERE id = ?",
                (item_id,),
            ).fetchone()

            return self._row_to_faq(conn, dict(row))

    def delete_faq(self, company_id: int, service: str, faq_id: str) -> bool:
        from database.database import db

        with db.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM knowledge_items
                WHERE company_id = ?
                  AND department = ?
                  AND item_type = ?
                  AND external_id = ?
                """,
                (company_id, service, self.FAQ_ITEM_TYPE, faq_id),
            )

            conn.commit()

            return cursor.rowcount > 0


knowledge_manager = KnowledgeManager()
