import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeManager:
    """Company-scoped knowledge base the AI draws on when replying.

    Each company builds and manages its own entries (title/question +
    answer) — this is what makes the AI actually know that specific
    company's business instead of replying generically.

    Scoping is fully open-ended via tags: an entry can carry any
    number of free-form tags (e.g. "whatsapp", "sales", "vip",
    "ramadan-campaign") — not limited to a fixed channel/department
    dimension. An entry with NO tags applies everywhere. An entry
    matches the current conversation if it shares at least one tag
    with the conversation's context tags (channel name, department
    name, and anything else the caller passes in). Department is kept
    as its own field too (useful for the management UI's filter
    dropdown, and it's automatically included as a tag during
    matching), but a company is free to invent entirely new tagging
    dimensions beyond channel/department without any code change.

    Falls back to the old static JSON files (config/knowledge_base.json,
    config/training_knowledge.json) ONLY when a company has zero
    entries of its own yet, so a brand-new company isn't left with a
    completely empty knowledge base before they've had a chance to add
    anything — this fallback content is generic/legacy and companies
    are expected to replace it with their own.
    """

    BASE_FILE = Path("config") / "knowledge_base.json"
    TRAINING_FILE = Path("config") / "training_knowledge.json"

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    department TEXT NOT NULL DEFAULT 'Unassigned',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(knowledge_entries)").fetchall()
            }
            if "tags_json" not in existing_columns:
                conn.execute("ALTER TABLE knowledge_entries ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_entries_company ON knowledge_entries(company_id)"
            )
            conn.commit()

    # ---- Legacy static-file fallback (unchanged behavior, only used when a company has no entries yet) ----

    def load_json_items(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("items", [])

    def load_legacy_items(self) -> list[dict]:
        items = []
        items.extend(self.load_json_items(self.BASE_FILE))
        items.extend(self.load_json_items(self.TRAINING_FILE))
        return items

    # ---- Real per-company knowledge (the actual feature) ----

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        if not tags:
            return []
        seen = []
        for tag in tags:
            cleaned = (tag or "").strip().lower()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen

    def _row_to_dict(self, row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["tags"] = json.loads(data.get("tags_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            data["tags"] = []
        return data

    def list_for_company(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_entries WHERE company_id = ? AND status = 'active' ORDER BY department, title",
                (company_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def create(
        self, *, company_id: int, title: str, content: str, department: str = "Unassigned",
        tags: list[str] | None = None, actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        content = (content or "").strip()
        if not title or not content:
            raise ValueError("Both a title/question and an answer are required.")

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_entries (company_id, department, title, content, tags_json, created_by_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (company_id, department or "Unassigned", title, content,
                 json.dumps(self._normalize_tags(tags)), actor_user_id, now, now),
            )
            entry_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, entry_id=entry_id)

    def get(self, *, company_id: int, entry_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_entries WHERE id = ? AND company_id = ?",
                (entry_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Knowledge entry not found")
        return self._row_to_dict(row)

    def update(
        self, *, company_id: int, entry_id: int, title: str | None, content: str | None, department: str | None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        existing = self.get(company_id=company_id, entry_id=entry_id)
        new_title = (title or "").strip() or existing["title"]
        new_content = (content or "").strip() or existing["content"]
        new_department = department or existing["department"]
        new_tags = self._normalize_tags(tags) if tags is not None else existing["tags"]

        with db.connect() as conn:
            conn.execute(
                "UPDATE knowledge_entries SET title = ?, content = ?, department = ?, tags_json = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (new_title, new_content, new_department, json.dumps(new_tags), utc_now_iso(), entry_id, company_id),
            )
            conn.commit()
        return self.get(company_id=company_id, entry_id=entry_id)

    def delete(self, *, company_id: int, entry_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge_entries WHERE id = ? AND company_id = ?", (entry_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Knowledge entry not found")

    def list_for_ai(
        self, company_id: int | None, department: str | None = None, context_tags: list[str] | None = None,
    ) -> list[dict]:
        """Called by the AI reply engine. Returns this company's own
        knowledge entries — falling back to the legacy shared static
        files only if the company hasn't added any of its own yet.

        Scoping: an entry with no tags applies everywhere. An entry
        with tags only applies if it shares at least one tag with
        context_tags (typically the current channel name and
        department name, lowercased — but callers can pass any tags).
        The old `department` param still works for simple filtering
        and is automatically folded into context_tags too.
        """
        items: list[dict] = []
        if company_id is not None:
            items = self.list_for_company(company_id=company_id)

        if not items:
            items = self.load_legacy_items()

        effective_context = set(self._normalize_tags(context_tags))
        if department:
            effective_context.add(department.strip().lower())

        if not department and not context_tags:
            return items

        return [
            item for item in items
            if (effective_context & set(t.lower() for t in item.get("tags", [])))  # shares a tag -> matches
            or (not item.get("tags") and item.get("department") in [department, "Unassigned"])  # no tags -> fall back to department matching
        ]


knowledge_manager = KnowledgeManager()
