import json
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InstructionService:
    """Company-scoped behavioral rules the AI follows when replying —
    distinct from Knowledge (facts it can draw on). Examples:
    "Don't share prices", "Use emojis when appropriate", "Don't send
    follow-up messages". Ordered — earlier instructions take priority
    when the AI reasons about conflicting guidance."""

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_instructions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(ai_instructions)").fetchall()
            }
            if "tags_json" not in existing_columns:
                conn.execute("ALTER TABLE ai_instructions ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_instructions_company ON ai_instructions(company_id)"
            )
            conn.commit()

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
        except (ValueError, TypeError):
            data["tags"] = []
        return data

    def list_for_company(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_instructions WHERE company_id = ? ORDER BY position, id",
                (company_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_texts_for_ai(self, company_id: int | None, context_tags: list[str] | None = None) -> list[str]:
        """Called by the AI reply engine — ordered instruction strings
        that apply to the current context. An instruction with no tags
        applies everywhere; one with tags only applies if it shares at
        least one tag with context_tags (e.g. the current channel and
        department, lowercased)."""
        if company_id is None:
            return []
        effective_context = set(self._normalize_tags(context_tags))
        entries = self.list_for_company(company_id=company_id)
        return [
            entry["text"] for entry in entries
            if not entry.get("tags") or effective_context & set(entry.get("tags", []))
        ]

    def create(self, *, company_id: int, text: str, tags: list[str] | None = None, actor_user_id: int | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("Instruction text is required.")

        now = utc_now_iso()
        with db.connect() as conn:
            max_position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) AS max_pos FROM ai_instructions WHERE company_id = ?",
                (company_id,),
            ).fetchone()["max_pos"]
            cursor = conn.execute(
                """
                INSERT INTO ai_instructions (company_id, text, position, tags_json, created_by_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (company_id, text, max_position + 1, json.dumps(self._normalize_tags(tags)), actor_user_id, now, now),
            )
            instruction_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, instruction_id=instruction_id)

    def get(self, *, company_id: int, instruction_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_instructions WHERE id = ? AND company_id = ?",
                (instruction_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Instruction not found")
        return self._row_to_dict(row)

    def update(self, *, company_id: int, instruction_id: int, text: str, tags: list[str] | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("Instruction text is required.")
        existing = self.get(company_id=company_id, instruction_id=instruction_id)
        new_tags = self._normalize_tags(tags) if tags is not None else existing["tags"]

        with db.connect() as conn:
            conn.execute(
                "UPDATE ai_instructions SET text = ?, tags_json = ?, updated_at = ? WHERE id = ? AND company_id = ?",
                (text, json.dumps(new_tags), utc_now_iso(), instruction_id, company_id),
            )
            conn.commit()
        return self.get(company_id=company_id, instruction_id=instruction_id)

    def delete(self, *, company_id: int, instruction_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM ai_instructions WHERE id = ? AND company_id = ?", (instruction_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Instruction not found")

    def reorder(self, *, company_id: int, ordered_ids: list[int]) -> list[dict[str, Any]]:
        with db.connect() as conn:
            for position, instruction_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE ai_instructions SET position = ? WHERE id = ? AND company_id = ?",
                    (position, instruction_id, company_id),
                )
            conn.commit()
        return self.list_for_company(company_id=company_id)


instruction_service = InstructionService()
