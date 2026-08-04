"""Company-scoped rooms + messages for the Team Chat module: internal
team messaging (mentions-by-convention, shared instructions) without
private WhatsApp groups. Mirrors the layered service pattern in
task_service.py -- the `team_chat_rooms`/`team_chat_messages` tables live
in database/database.py's central schema init, so this module does not
own/create its own tables and has no ensure_schema() of its own.

Messages are fetched with simple id-cursor pagination (`after_id` for
polling new messages, `before_id` for scrolling history) -- the same
polling approach the Broadcast page uses for live progress, no websocket
infrastructure required."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


MAX_MESSAGE_LENGTH = 4000


class TeamChatValidationError(ValueError):
    """Raised for invalid values: an empty room name, an empty or
    oversized message body, or a room that doesn't belong to the
    caller's company."""


class TeamChatService:
    DEFAULT_ROOM_NAME = "General"

    def _ensure_default_room(self, conn, company_id: int) -> None:
        row = conn.execute(
            "SELECT 1 FROM team_chat_rooms WHERE company_id = ? LIMIT 1",
            (company_id,),
        ).fetchone()
        if row:
            return
        now = utc_now_iso()
        # INSERT ... SELECT ... WHERE NOT EXISTS re-checks atomically at
        # write time: two requests hitting a company's very first listing
        # concurrently would otherwise both pass the check above and seed
        # two undeletable "General" rooms (there is no UNIQUE constraint
        # on (company_id, name) to stop the second insert).
        conn.execute(
            """
            INSERT INTO team_chat_rooms (
                company_id, name, description, is_default,
                created_by, created_at, updated_at
            )
            SELECT ?, ?, 'Company-wide chat for the whole team.', 1, NULL, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM team_chat_rooms WHERE company_id = ?
            )
            """,
            (company_id, self.DEFAULT_ROOM_NAME, now, now, company_id),
        )

    def list_rooms(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            # Every company gets a default "General" room on first visit so
            # the page is never empty/unusable.
            self._ensure_default_room(conn, company_id)
            conn.commit()

            rows = conn.execute(
                """
                SELECT
                    r.*,
                    (
                        SELECT COUNT(*) FROM team_chat_messages m
                        WHERE m.room_id = r.id AND m.company_id = r.company_id
                    ) AS message_count,
                    (
                        SELECT MAX(m.created_at) FROM team_chat_messages m
                        WHERE m.room_id = r.id AND m.company_id = r.company_id
                    ) AS last_message_at
                FROM team_chat_rooms r
                WHERE r.company_id = ?
                ORDER BY r.is_default DESC, r.name COLLATE NOCASE ASC
                """,
                (company_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def create_room(
        self,
        *,
        company_id: int,
        name: str,
        description: str | None,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        clean_name = (name or "").strip()
        if not clean_name:
            raise TeamChatValidationError("Room name is required.")

        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM team_chat_rooms "
                "WHERE company_id = ? AND name = ? COLLATE NOCASE LIMIT 1",
                (company_id, clean_name),
            ).fetchone()
            if existing:
                raise TeamChatValidationError(
                    "A room with this name already exists."
                )

            cursor = conn.execute(
                """
                INSERT INTO team_chat_rooms (
                    company_id, name, description, is_default,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    company_id,
                    clean_name,
                    (description or "").strip() or None,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            room_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT * FROM team_chat_rooms WHERE id = ?", (room_id,)
            ).fetchone()
            conn.commit()

        return dict(row)

    def delete_room(self, *, company_id: int, room_id: int) -> bool:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT is_default FROM team_chat_rooms "
                "WHERE id = ? AND company_id = ?",
                (room_id, company_id),
            ).fetchone()
            if not row:
                return False
            if row["is_default"]:
                raise TeamChatValidationError(
                    "The default room cannot be deleted."
                )
            conn.execute(
                "DELETE FROM team_chat_rooms WHERE id = ? AND company_id = ?",
                (room_id, company_id),
            )
            conn.commit()
            return True

    def _require_room(self, conn, company_id: int, room_id: int) -> None:
        row = conn.execute(
            "SELECT 1 FROM team_chat_rooms WHERE id = ? AND company_id = ? LIMIT 1",
            (room_id, company_id),
        ).fetchone()
        if not row:
            raise KeyError("Room not found")

    def list_messages(
        self,
        *,
        company_id: int,
        room_id: int,
        after_id: int | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """`after_id`: return messages newer than this id, oldest-first
        (polling for new messages). `before_id`: return messages older
        than this id, for history scroll-back (still returned
        oldest-first for straightforward rendering). Neither: the most
        recent page, oldest-first."""
        limit = max(1, min(200, limit))

        with db.connect() as conn:
            self._require_room(conn, company_id, room_id)

            base = """
                SELECT
                    m.*,
                    sender.full_name AS sender_name,
                    sender.email AS sender_email
                FROM team_chat_messages m
                LEFT JOIN users sender ON sender.id = m.sender_user_id
                WHERE m.company_id = ? AND m.room_id = ?
            """
            params: list[Any] = [company_id, room_id]

            if after_id is not None:
                rows = conn.execute(
                    f"{base} AND m.id > ? ORDER BY m.id ASC LIMIT ?",
                    [*params, after_id, limit],
                ).fetchall()
            elif before_id is not None:
                rows = conn.execute(
                    f"{base} AND m.id < ? ORDER BY m.id DESC LIMIT ?",
                    [*params, before_id, limit],
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(
                    f"{base} ORDER BY m.id DESC LIMIT ?",
                    [*params, limit],
                ).fetchall()
                rows = list(reversed(rows))

        return {"items": [dict(row) for row in rows]}

    def post_message(
        self,
        *,
        company_id: int,
        room_id: int,
        body: str,
        sender_user_id: int | None,
    ) -> dict[str, Any]:
        clean_body = (body or "").strip()
        if not clean_body:
            raise TeamChatValidationError("Message body is required.")
        if len(clean_body) > MAX_MESSAGE_LENGTH:
            raise TeamChatValidationError(
                f"Message must be {MAX_MESSAGE_LENGTH} characters or fewer."
            )

        now = utc_now_iso()

        with db.connect() as conn:
            self._require_room(conn, company_id, room_id)

            cursor = conn.execute(
                """
                INSERT INTO team_chat_messages (
                    company_id, room_id, sender_user_id, body, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (company_id, room_id, sender_user_id, clean_body, now),
            )
            message_id = int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT
                    m.*,
                    sender.full_name AS sender_name,
                    sender.email AS sender_email
                FROM team_chat_messages m
                LEFT JOIN users sender ON sender.id = m.sender_user_id
                WHERE m.id = ?
                """,
                (message_id,),
            ).fetchone()
            conn.commit()

        return dict(row)


team_chat_service = TeamChatService()
