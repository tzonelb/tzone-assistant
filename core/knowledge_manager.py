import json
import logging
import sqlite3
from pathlib import Path

from database.database import db


logger = logging.getLogger(__name__)


class KnowledgeManager:
    BASE_FILE = Path("config") / "knowledge_base.json"
    TRAINING_FILE = Path("config") / "training_knowledge.json"

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


knowledge_manager = KnowledgeManager()
