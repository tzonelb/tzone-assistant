"""The knowledge the assistant is allowed to answer from.

Every knowledge item belongs to exactly one company and lives inside that
company's own encrypted database. There is no shared knowledge file any more:
the assistant used to read ``config/knowledge_base.json`` and
``config/training_knowledge.json``, which meant every company on the platform
answered its customers out of the same single-tenant file.

Table creation belongs to ``database/schema_tenant.py`` alone. This service only
reads and writes ``knowledge_items`` and ``knowledge_categories``.

Column meaning, since the table is short and the names are not self-explaining:

``title``
    The short label of the item — in practice the customer question it answers.
``content_ar`` / ``content_en``
    The answer in each language. At least one of the two must be present, or the
    item teaches the assistant nothing.
``keywords``
    Free text matching hints: alternative phrasings and usage notes. It is
    searched and it is handed to the matcher, it is not a controlled vocabulary.
``external_id``
    A stable identifier carried over from an import. It is what the assistant
    sees as the item id, so a re-import updates an item instead of duplicating
    it and previously logged ``used_knowledge_ids`` keep pointing at the same
    entry.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.services.plan_service import PlanLimitExceeded, plan_service
from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeService:
    ALLOWED_STATUS = ("active", "draft", "archived")

    # The whole set is serialized into one OpenAI request, so it has to be
    # bounded. A company that grows past this needs the department filter, not a
    # larger prompt.
    MAX_ASSISTANT_ITEMS = 200

    MAX_LIMIT = 200

    ITEM_FIELDS = (
        "title",
        "content_ar",
        "content_en",
        "department",
        "keywords",
        "category_id",
        "external_id",
        "status",
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

    def _normalize_status(self, value: Any, default: str = "active") -> str:
        status = (self._clean(value) or default).lower()

        if status not in self.ALLOWED_STATUS:
            raise ValueError(
                f"Status must be one of: {', '.join(self.ALLOWED_STATUS)}."
            )

        return status

    @staticmethod
    def _assert_knowledge_available(conn, company_id: int) -> None:
        """Refuse an item the plan has no room for.

        Raises `ValueError`, which every caller of `create_item` already turns
        into a 400 — the message carries the limit and the usage, so an owner
        reads what to do rather than only that something failed.
        """
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM knowledge_items WHERE company_id = ?",
            (int(company_id),),
        ).fetchone()

        try:
            plan_service.check(
                company_id,
                "max_knowledge_items",
                int(row["total"]) if row else 0,
            )
        except PlanLimitExceeded as exc:
            raise ValueError(str(exc)) from exc

    def _require_category(self, conn, *, company_id: int, category_id: int) -> None:
        row = conn.execute(
            """
            SELECT id FROM knowledge_categories
            WHERE id = ? AND company_id = ?
            LIMIT 1
            """,
            (int(category_id), int(company_id)),
        ).fetchone()

        if not row:
            raise ValueError("That knowledge category does not exist.")

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def list_items(
        self,
        *,
        company_id: int,
        search: str | None = None,
        department: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))

        where = ["items.company_id = ?"]
        params: list[Any] = [company_id]

        search = self._clean(search)
        if search:
            pattern = f"%{search}%"
            where.append(
                "(items.title LIKE ? OR items.content_ar LIKE ? "
                "OR items.content_en LIKE ? OR items.keywords LIKE ? "
                "OR items.external_id LIKE ?)"
            )
            params.extend([pattern] * 5)

        department = self._clean(department)
        if department:
            where.append("items.department = ?")
            params.append(department)

        status = self._clean(status)
        if status:
            where.append("items.status = ?")
            params.append(self._normalize_status(status))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS total
                    FROM knowledge_items items
                    WHERE {clause}
                    """,
                    params,
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT items.*, categories.name AS category_name
                FROM knowledge_items items
                LEFT JOIN knowledge_categories categories
                    ON categories.id = items.category_id
                WHERE {clause}
                ORDER BY items.updated_at DESC, items.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": total}

    def get_item(self, *, company_id: int, item_id: int) -> dict[str, Any] | None:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT items.*, categories.name AS category_name
                FROM knowledge_items items
                LEFT JOIN knowledge_categories categories
                    ON categories.id = items.category_id
                WHERE items.id = ? AND items.company_id = ?
                LIMIT 1
                """,
                (int(item_id), company_id),
            ).fetchone()

        return dict(row) if row else None

    def create_item(self, *, company_id: int, data: dict[str, Any]) -> dict[str, Any]:
        company_id = int(company_id)

        title = self._clean(data.get("title"))
        if not title:
            raise ValueError("A knowledge item needs a title.")

        content_ar = self._clean(data.get("content_ar"))
        content_en = self._clean(data.get("content_en"))

        if not content_ar and not content_en:
            raise ValueError(
                "A knowledge item needs Arabic or English content, otherwise it "
                "teaches the assistant nothing."
            )

        status = self._normalize_status(data.get("status"))
        category_id = data.get("category_id")
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            # The count below and the insert underneath it are one decision,
            # so they are one transaction. Without this each statement was its
            # own, every concurrent create read the same count, and all of them
            # passed a limit with room for one: twelve simultaneous creates
            # against an allowance of five left twelve rows.
            #
            # `BEGIN IMMEDIATE` takes the write lock before the count rather
            # than at the insert, so a second writer waits and then reads a
            # count that includes the first. `channel_account_service` has
            # always done this, and its limit holds exactly where this one did
            # not.
            conn.execute("BEGIN IMMEDIATE")

            if category_id is not None:
                self._require_category(
                    conn, company_id=company_id, category_id=int(category_id)
                )

            # Counted here, inside the company's own database, against a limit
            # that lives in the control plane. The two databases are opened in
            # sequence rather than joined — SQLite cannot join across files,
            # and the encryption keys are different.
            #
            # Every row counts, not only active ones. An archived item is
            # storage the company is still using, and counting only the active
            # ones would let a base grow without limit by archiving as it goes.
            self._assert_knowledge_available(conn, company_id)

            cursor = conn.execute(
                """
                INSERT INTO knowledge_items (
                    company_id, category_id, external_id, title,
                    content_ar, content_en, department, keywords,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    int(category_id) if category_id is not None else None,
                    self._clean(data.get("external_id")),
                    title,
                    content_ar,
                    content_en,
                    self._clean(data.get("department")),
                    self._clean(data.get("keywords")),
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()
            item_id = int(cursor.lastrowid)

        logger.info("Created knowledge item id=%s company id=%s", item_id, company_id)

        return self.get_item(company_id=company_id, item_id=item_id)

    def update_item(
        self,
        *,
        company_id: int,
        item_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        item_id = int(item_id)

        updates: dict[str, Any] = {}

        for field in self.ITEM_FIELDS:
            if field not in values:
                continue

            value = values[field]

            if field == "status":
                updates[field] = self._normalize_status(value)
            elif field == "category_id":
                updates[field] = int(value) if value is not None else None
            else:
                updates[field] = self._clean(value)

        if "title" in updates and not updates["title"]:
            raise ValueError("A knowledge item needs a title.")

        if not updates:
            return self.get_item(company_id=company_id, item_id=item_id)

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                """
                SELECT content_ar, content_en FROM knowledge_items
                WHERE id = ? AND company_id = ?
                LIMIT 1
                """,
                (item_id, company_id),
            ).fetchone()

            if not existing:
                return None

            content_ar = updates.get("content_ar", existing["content_ar"])
            content_en = updates.get("content_en", existing["content_en"])

            if not content_ar and not content_en:
                raise ValueError(
                    "A knowledge item needs Arabic or English content, otherwise "
                    "it teaches the assistant nothing."
                )

            if updates.get("category_id") is not None:
                self._require_category(
                    conn,
                    company_id=company_id,
                    category_id=int(updates["category_id"]),
                )

            assignments = ", ".join(f"{field} = ?" for field in updates)

            conn.execute(
                f"""
                UPDATE knowledge_items
                SET {assignments}, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                [*updates.values(), utc_now_iso(), item_id, company_id],
            )
            conn.commit()

        logger.info(
            "Updated knowledge item id=%s company id=%s fields=%s",
            item_id,
            company_id,
            sorted(updates),
        )

        return self.get_item(company_id=company_id, item_id=item_id)

    def delete_item(self, *, company_id: int, item_id: int) -> bool:
        company_id = int(company_id)
        item_id = int(item_id)

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge_items WHERE id = ? AND company_id = ?",
                (item_id, company_id),
            )
            conn.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info(
                "Deleted knowledge item id=%s company id=%s", item_id, company_id
            )

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
                           FROM knowledge_items items
                           WHERE items.category_id = categories.id
                       ) AS item_count
                FROM knowledge_categories categories
                WHERE categories.company_id = ?
                ORDER BY categories.name COLLATE NOCASE
                """,
                (company_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def create_category(
        self,
        *,
        company_id: int,
        name: str,
        department: str | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        clean_name = self._clean(name)

        if not clean_name:
            raise ValueError("A category needs a name.")

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                """
                SELECT id FROM knowledge_categories
                WHERE company_id = ? AND name = ?
                LIMIT 1
                """,
                (company_id, clean_name),
            ).fetchone()

            if existing:
                raise ValueError(f"A category named {clean_name!r} already exists.")

            cursor = conn.execute(
                """
                INSERT INTO knowledge_categories (
                    company_id, name, department, status, created_at
                )
                VALUES (?, ?, ?, 'active', ?)
                """,
                (company_id, clean_name, self._clean(department), utc_now_iso()),
            )
            conn.commit()
            category_id = int(cursor.lastrowid)

            row = conn.execute(
                "SELECT * FROM knowledge_categories WHERE id = ?", (category_id,)
            ).fetchone()

        logger.info(
            "Created knowledge category id=%s company id=%s", category_id, company_id
        )

        return {**dict(row), "item_count": 0}

    def ensure_category(
        self,
        *,
        company_id: int,
        name: str,
        department: str | None = None,
    ) -> dict[str, Any]:
        """Return the category with this name, creating it when it is missing.

        ``create_category`` refuses duplicates so the screen can report them.
        An import runs repeatedly against the same company and must not fail on
        the second run, so it uses this instead.
        """
        company_id = int(company_id)
        clean_name = self._clean(name)

        if not clean_name:
            raise ValueError("A category needs a name.")

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT * FROM knowledge_categories
                WHERE company_id = ? AND name = ?
                LIMIT 1
                """,
                (company_id, clean_name),
            ).fetchone()

        if row:
            return dict(row)

        return self.create_category(
            company_id=company_id,
            name=clean_name,
            department=department,
        )

    def upsert_by_external_id(
        self,
        *,
        company_id: int,
        external_id: str,
        data: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Create or refresh the item carrying this external id.

        Returns ``(item, created)``. Matching on the external id is what makes
        an import repeatable: re-running it corrects the existing entries
        instead of stacking a second copy of the whole knowledge base, and the
        id the assistant reported in ``used_knowledge_ids`` keeps resolving.
        """
        company_id = int(company_id)
        clean_external_id = self._clean(external_id)

        if not clean_external_id:
            raise ValueError("An imported item needs an external id.")

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT id FROM knowledge_items
                WHERE company_id = ? AND external_id = ?
                ORDER BY id
                LIMIT 1
                """,
                (company_id, clean_external_id),
            ).fetchone()

        if row:
            item = self.update_item(
                company_id=company_id,
                item_id=int(row["id"]),
                values=dict(data),
            )
            return item, False

        item = self.create_item(
            company_id=company_id,
            data={**data, "external_id": clean_external_id},
        )
        return item, True

    def departments(self, *, company_id: int) -> list[str]:
        """Every department actually used by this company's items."""
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT department
                FROM knowledge_items
                WHERE company_id = ?
                  AND department IS NOT NULL
                  AND TRIM(department) <> ''
                ORDER BY department COLLATE NOCASE
                """,
                (company_id,),
            ).fetchall()

        return [str(row["department"]) for row in rows]

    # ------------------------------------------------------------------
    # The assistant
    # ------------------------------------------------------------------

    def for_assistant(
        self,
        company_id: int,
        department: str | None = None,
    ) -> list[dict[str, Any]]:
        """The active knowledge one company's assistant may answer from.

        The returned dicts keep the shape ``core.ai_knowledge_matcher`` already
        works with: a string ``id`` it can hand back in ``selected_ids``, a
        ``department``, and the answer text in both languages. Nothing else in
        the AI path had to learn a new format.

        ``department`` narrows the set the same way the old file-backed loader
        did: items for that department plus items that are general information.
        """
        company_id = int(company_id)
        department = self._clean(department)

        where = ["company_id = ?", "status = 'active'"]
        params: list[Any] = [company_id]

        if department:
            where.append(
                "(department = ? OR department = 'information' "
                "OR department IS NULL OR TRIM(department) = '')"
            )
            params.append(department)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT id, external_id, title, content_ar, content_en,
                       department, keywords
                FROM knowledge_items
                WHERE {clause}
                ORDER BY id
                LIMIT ?
                """,
                [*params, self.MAX_ASSISTANT_ITEMS],
            ).fetchall()

        return [
            {
                "id": self._clean(row["external_id"]) or f"item-{int(row['id'])}",
                "department": self._clean(row["department"]) or "information",
                "title": row["title"],
                "answer_ar": row["content_ar"] or "",
                "answer_en": row["content_en"] or "",
                "keywords": row["keywords"] or "",
            }
            for row in rows
        ]


knowledge_service = KnowledgeService()
