from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SavedReplyService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    department TEXT NOT NULL DEFAULT '',
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_saved_replies_company ON saved_replies(company_id)"
            )
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(saved_replies)")
            }
            if "department" not in existing_columns:
                conn.execute(
                    "ALTER TABLE saved_replies ADD COLUMN department TEXT NOT NULL DEFAULT ''"
                )
            conn.commit()

    def list_for_company(self, *, company_id: int, department: str | None = None) -> list[dict[str, Any]]:
        with db.connect() as conn:
            if department:
                rows = conn.execute(
                    "SELECT * FROM saved_replies WHERE company_id = ? AND department = ? ORDER BY title ASC",
                    (company_id, department),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM saved_replies WHERE company_id = ? ORDER BY title ASC",
                    (company_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def create(
        self,
        *,
        company_id: int,
        title: str,
        body: str,
        department: str = "",
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        body = (body or "").strip()
        department = (department or "").strip()
        if not title or not body:
            raise ValueError("Both a title and a message body are required.")

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO saved_replies (company_id, title, body, department, created_by_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (company_id, title, body, department, actor_user_id, now, now),
            )
            reply_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, reply_id=reply_id)

    def get(self, *, company_id: int, reply_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM saved_replies WHERE id = ? AND company_id = ?",
                (reply_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Saved reply not found")
        return dict(row)

    def update(
        self,
        *,
        company_id: int,
        reply_id: int,
        title: str | None,
        body: str | None,
        department: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get(company_id=company_id, reply_id=reply_id)
        new_title = (title or "").strip() or existing["title"]
        new_body = (body or "").strip() or existing["body"]
        new_department = existing["department"] if department is None else department.strip()

        with db.connect() as conn:
            conn.execute(
                "UPDATE saved_replies SET title = ?, body = ?, department = ?, updated_at = ? WHERE id = ? AND company_id = ?",
                (new_title, new_body, new_department, utc_now_iso(), reply_id, company_id),
            )
            conn.commit()
        return self.get(company_id=company_id, reply_id=reply_id)

    def delete(self, *, company_id: int, reply_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM saved_replies WHERE id = ? AND company_id = ?", (reply_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Saved reply not found")


saved_reply_service = SavedReplyService()
