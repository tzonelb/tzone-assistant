"""Follow-ups an employee set on a conversation.

"Come back to this at four" is a note to the team, and optionally a message the
platform sends when the time arrives. Both live in the company's own encrypted
database, because both are about that company's customer.

One live reminder per conversation, enforced by the unique key on
``(channel, external_user_id)`` rather than by the caller: setting a second one
replaces the first, which is what "remind me at" means to the person clicking it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager, utc_now_iso


MAX_NOTE = 500
MAX_MESSAGE = 4000


class ReminderError(RuntimeError):
    """A reminder was refused for a reason worth showing the caller."""


def _parse_when(value: Any) -> str:
    """The moment to come back, normalised to UTC ISO-8601.

    A reminder in the past is refused rather than fired immediately: it is
    almost always a timezone mistake, and a sweep that sends a message the
    instant it is scheduled is the wrong thing to do with a customer.
    """
    text = str(value or "").strip()

    if not text:
        raise ReminderError("A reminder needs a time.")

    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReminderError(
            "That reminder time is not a date and time the platform understands."
        ) from exc

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    when = when.astimezone(timezone.utc)

    if when <= datetime.now(timezone.utc):
        raise ReminderError("That reminder time has already passed.")

    return when.isoformat()


def _clean(value: Any, limit: int, *, field: str) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    if len(text) > limit:
        raise ReminderError(f"{field} is longer than {limit} characters.")

    return text


class ConversationReminderService:
    def get(
        self, *, company_id: int, channel: str, external_user_id: str
    ) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                """
                SELECT * FROM conversation_reminders
                WHERE channel = ? AND external_user_id = ?
                LIMIT 1
                """,
                (str(channel), str(external_user_id)),
            ).fetchone()

        return dict(row) if row else None

    def set(
        self,
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        remind_at: Any,
        note: str | None = None,
        auto_send: bool = False,
        message_text: str | None = None,
        created_by_user_id: int | None = None,
    ) -> dict[str, Any]:
        when = _parse_when(remind_at)
        note = _clean(note, MAX_NOTE, field="Note")
        message_text = _clean(message_text, MAX_MESSAGE, field="Message")

        # A reminder that promises to send something needs something to send.
        # Storing auto_send with no text would leave the sweep with a decision
        # it cannot make and the employee believing a message will go out.
        if auto_send and not message_text:
            raise ReminderError(
                "A reminder set to send a message needs the message text."
            )

        now = utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            conn.execute(
                """
                INSERT INTO conversation_reminders (
                    company_id, channel, external_user_id, remind_at, note,
                    auto_send, message_text, created_by_user_id,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, external_user_id) DO UPDATE SET
                    remind_at = excluded.remind_at,
                    note = excluded.note,
                    auto_send = excluded.auto_send,
                    message_text = excluded.message_text,
                    created_by_user_id = excluded.created_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    int(company_id),
                    str(channel),
                    str(external_user_id),
                    when,
                    note,
                    1 if auto_send else 0,
                    message_text,
                    int(created_by_user_id) if created_by_user_id else None,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT * FROM conversation_reminders
                WHERE channel = ? AND external_user_id = ?
                LIMIT 1
                """,
                (str(channel), str(external_user_id)),
            ).fetchone()

        return dict(row)

    def clear(
        self, *, company_id: int, channel: str, external_user_id: str
    ) -> bool:
        """Remove the reminder. Answers whether there was one to remove."""
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                DELETE FROM conversation_reminders
                WHERE channel = ? AND external_user_id = ?
                """,
                (str(channel), str(external_user_id)),
            )
            conn.commit()

        return bool(cursor.rowcount)

    def due(self, *, company_id: int, now: str | None = None) -> list[dict[str, Any]]:
        """Reminders whose time has arrived, oldest first.

        For the worker that surfaces them; it is the only reader that looks
        across conversations rather than at one.
        """
        moment = now or utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversation_reminders
                WHERE remind_at <= ?
                ORDER BY remind_at ASC
                """,
                (moment,),
            ).fetchall()

        return [dict(row) for row in rows]


conversation_reminder_service = ConversationReminderService()
