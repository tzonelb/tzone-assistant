"""Canned replies an employee drops into a conversation.

Company-owned text, so every read and write goes to that company's own
encrypted database through ``database_manager.tenant``. There is no cross-company
query here and there cannot be one: the file is the tenant.
"""

from __future__ import annotations

from typing import Any

from database.manager import database_manager, utc_now_iso


MAX_TITLE = 120
MAX_BODY = 4000
MAX_DEPARTMENT = 80


class SavedReplyError(RuntimeError):
    """A saved reply was refused for a reason worth showing the caller."""


class SavedReplyNotFound(SavedReplyError):
    """No reply with that id in this company."""


def _clean(value: Any, limit: int, *, field: str, required: bool = True) -> str:
    text = str(value or "").strip()

    if required and not text:
        raise SavedReplyError(f"{field} is required.")

    if len(text) > limit:
        raise SavedReplyError(f"{field} is longer than {limit} characters.")

    return text


class SavedReplyService:
    def list_for_company(
        self, *, company_id: int, department: str | None = None
    ) -> list[dict[str, Any]]:
        """Every reply, or only the ones written for one section.

        A reply with an empty department suits every section, so a filtered read
        returns those too — otherwise a company that files most replies as
        general would see an empty list on every section.
        """
        with database_manager.tenant(int(company_id)) as conn:
            if department:
                rows = conn.execute(
                    """
                    SELECT * FROM saved_replies
                    WHERE department = ? OR department = ''
                    ORDER BY title COLLATE NOCASE ASC
                    """,
                    (str(department).strip(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM saved_replies ORDER BY title COLLATE NOCASE ASC"
                ).fetchall()

        return [dict(row) for row in rows]

    def create(
        self,
        *,
        company_id: int,
        title: str,
        body: str,
        department: str = "",
        created_by_user_id: int | None = None,
    ) -> dict[str, Any]:
        title = _clean(title, MAX_TITLE, field="Title")
        body = _clean(body, MAX_BODY, field="Reply text")
        department = _clean(
            department, MAX_DEPARTMENT, field="Department", required=False
        )
        now = utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO saved_replies (
                    company_id, title, body, department,
                    created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(company_id),
                    title,
                    body,
                    department,
                    int(created_by_user_id) if created_by_user_id else None,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM saved_replies WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()

        return dict(row)

    def update(
        self,
        *,
        company_id: int,
        reply_id: int,
        title: str | None = None,
        body: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
        """Change one reply. Only the fields actually sent are written."""
        updates: list[str] = []
        values: list[Any] = []

        if title is not None:
            updates.append("title = ?")
            values.append(_clean(title, MAX_TITLE, field="Title"))

        if body is not None:
            updates.append("body = ?")
            values.append(_clean(body, MAX_BODY, field="Reply text"))

        if department is not None:
            updates.append("department = ?")
            values.append(
                _clean(department, MAX_DEPARTMENT, field="Department", required=False)
            )

        if not updates:
            raise SavedReplyError("Nothing to change.")

        updates.append("updated_at = ?")
        values.append(utc_now_iso())
        values.append(int(reply_id))

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                f"UPDATE saved_replies SET {', '.join(updates)} WHERE id = ?",
                tuple(values),
            )

            if not cursor.rowcount:
                raise SavedReplyNotFound(f"No saved reply with id {reply_id}.")

            conn.commit()
            row = conn.execute(
                "SELECT * FROM saved_replies WHERE id = ?", (int(reply_id),)
            ).fetchone()

        return dict(row)

    def delete(self, *, company_id: int, reply_id: int) -> None:
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "DELETE FROM saved_replies WHERE id = ?", (int(reply_id),)
            )

            if not cursor.rowcount:
                raise SavedReplyNotFound(f"No saved reply with id {reply_id}.")

            conn.commit()


saved_reply_service = SavedReplyService()
