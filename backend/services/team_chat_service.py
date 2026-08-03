from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamChatService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS team_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    sender_user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    mentioned_user_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(sender_user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(team_messages)").fetchall()
            }
            for column in ("attachment_url", "attachment_type", "attachment_filename"):
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE team_messages ADD COLUMN {column} TEXT")
            conn.commit()

    def _validate_mentions(self, conn, *, company_id: int, mentioned_user_ids: list[int]) -> list[int]:
        if not mentioned_user_ids:
            return []
        placeholders = ",".join("?" for _ in mentioned_user_ids)
        rows = conn.execute(
            f"""
            SELECT users.id FROM users
            JOIN company_users ON company_users.user_id = users.id
            WHERE company_users.company_id = ? AND company_users.status = 'active'
              AND users.id IN ({placeholders})
            """,
            (company_id, *mentioned_user_ids),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def send_message(
        self, *, company_id: int, sender_user_id: int, text: str, mentioned_user_ids: list[int] | None = None,
        attachment_url: str | None = None, attachment_type: str | None = None, attachment_filename: str | None = None,
    ) -> dict[str, Any]:
        text = (text or "").strip()
        if not text and not attachment_url:
            raise ValueError("Message text cannot be empty.")
        if len(text) > 4000:
            raise ValueError("Message is too long (max 4000 characters).")

        now = utc_now_iso()
        with db.connect() as conn:
            valid_mentions = self._validate_mentions(
                conn, company_id=company_id, mentioned_user_ids=mentioned_user_ids or []
            )
            cursor = conn.execute(
                """
                INSERT INTO team_messages (
                    company_id, sender_user_id, text, mentioned_user_ids_json,
                    attachment_url, attachment_type, attachment_filename, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (company_id, sender_user_id, text, json.dumps(valid_mentions),
                 attachment_url, attachment_type, attachment_filename, now),
            )
            message_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_message(company_id=company_id, message_id=message_id)

    def _row_to_dict(self, row) -> dict[str, Any]:
        item = dict(row)
        item["mentioned_user_ids"] = json.loads(item.pop("mentioned_user_ids_json") or "[]")
        return item

    def get_message(self, *, company_id: int, message_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT tm.*, COALESCE(u.full_name, u.email) AS sender_name
                FROM team_messages tm
                JOIN users u ON u.id = tm.sender_user_id
                WHERE tm.id = ? AND tm.company_id = ?
                """,
                (message_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Message not found")
        return self._row_to_dict(row)

    def list_messages(self, *, company_id: int, before_id: int | None = None, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        where = ["tm.company_id = ?"]
        params: list[Any] = [company_id]
        if before_id is not None:
            where.append("tm.id < ?")
            params.append(before_id)
        clause = " AND ".join(where)

        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT tm.*, COALESCE(u.full_name, u.email) AS sender_name
                FROM team_messages tm
                JOIN users u ON u.id = tm.sender_user_id
                WHERE {clause}
                ORDER BY tm.id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        items = [self._row_to_dict(row) for row in rows]
        items.reverse()
        return {"items": items, "total": len(items)}

    def delete_message(self, *, company_id: int, message_id: int, actor_user_id: int) -> None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT sender_user_id FROM team_messages WHERE id = ? AND company_id = ?",
                (message_id, company_id),
            ).fetchone()
            if not row:
                raise KeyError("Message not found")
            if int(row["sender_user_id"]) != int(actor_user_id):
                raise PermissionError("You can only delete your own messages.")
            conn.execute(
                "DELETE FROM team_messages WHERE id = ? AND company_id = ?",
                (message_id, company_id),
            )
            conn.commit()


team_chat_service = TeamChatService()
