"""Follow-ups an employee set on a conversation.

"Come back to this at four" is a note to the team, and optionally a message the
platform sends when the time arrives. Both live in the company's own encrypted
database, because both are about that company's customer.

One live reminder per conversation, enforced by the unique key on
``(channel, external_user_id)`` rather than by the caller: setting a second one
replaces the first, which is what "remind me at" means to the person clicking it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.services.company_gate import company_gate
from backend.services.message_service import message_service
from backend.services.notification_service import notification_service
from backend.services.subscription_gate import subscription_gate
from backend.services.work_index_service import KIND_REMINDER, work_index_service
from channels.sender import send_text
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)


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

            # Before the commit, on purpose (see work_index_service's own
            # docstring, rule 2): a control-plane failure here must abort the
            # reminder rather than commit one no sweep will ever be told about.
            work_index_service.note(int(company_id), KIND_REMINDER, when)

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

    def fire_due(self, company_id: int, now: str | None = None) -> int:
        """Fire every reminder whose time has arrived. Returns how many fired.

        Gated the same as every other worker that can put a message in front
        of a customer on this company's behalf -- the same reasoning
        `publish_due_posts` applies to a post and `process_due_replies`
        applies to an assistant reply applies here to a message an employee
        pre-wrote and scheduled. A lapsed or suspended company gets neither
        its message sent nor its "come back to this" notification raised;
        both stay queued (nothing is claimed here) until the sweep runs again
        after the company is reinstated.
        """
        if subscription_gate.lapsed(company_id) or company_gate.suspended(company_id):
            return 0

        fired = 0

        for reminder in self.due(company_id=company_id, now=now):
            try:
                self._fire_one(company_id=company_id, reminder=reminder)
                fired += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Could not fire reminder %s for company %s",
                    reminder.get("id"),
                    company_id,
                )
            finally:
                # A reminder fires once, like an alarm rather than a timer
                # that repeats. Cleared even when something above raised, so a
                # transient failure cannot turn one reminder into a retry
                # storm that opens this company again every sweep forever.
                self.clear(
                    company_id=company_id,
                    channel=reminder["channel"],
                    external_user_id=reminder["external_user_id"],
                )

        return fired

    def _fire_one(self, *, company_id: int, reminder: dict[str, Any]) -> None:
        channel = str(reminder["channel"])
        external_user_id = str(reminder["external_user_id"])
        created_by_user_id = reminder.get("created_by_user_id")
        wants_auto_send = bool(reminder.get("auto_send")) and bool(
            reminder.get("message_text")
        )
        sent = False
        send_error: str | None = None

        if wants_auto_send:
            sent, send_error = self._send_message(
                company_id=company_id,
                channel=channel,
                external_user_id=external_user_id,
                text=reminder["message_text"],
                created_by_user_id=created_by_user_id,
                reminder_id=reminder.get("id"),
            )

        if created_by_user_id:
            self._notify(
                company_id=company_id,
                channel=channel,
                external_user_id=external_user_id,
                recipient_user_id=int(created_by_user_id),
                note=reminder.get("note"),
                wants_auto_send=wants_auto_send,
                sent=sent,
                send_error=send_error,
            )

    @staticmethod
    def _send_message(
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        text: str,
        created_by_user_id: int | None,
        reminder_id: int | None,
    ) -> tuple[bool, str | None]:
        """Send the message an employee pre-wrote, and put it on the Timeline.

        A reply scheduled rather than typed -- see the docstring on the route
        that creates one (`backend/api/routes/conversations.py`'s
        `set_conversation_reminder`) -- so it is recorded exactly as a manual
        reply is: `sender_type="employee"`, attributed to whoever set the
        reminder, not to the sweep that happened to be running when it fired.
        """
        try:
            result = send_text(
                channel=channel,
                recipient_id=external_user_id,
                company_id=company_id,
                text=text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Reminder message send failed for company %s channel %s",
                company_id,
                channel,
            )
            return False, type(exc).__name__

        if not result.get("ok"):
            return False, str(result.get("error") or result.get("reason") or "")

        try:
            message_service.save_message(
                company_id=company_id,
                channel=channel,
                external_user_id=external_user_id,
                direction="out",
                text=text,
                sender_type="employee",
                sender_user_id=created_by_user_id,
                source="reminder",
                metadata={"reminder_id": reminder_id},
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not record a fired reminder's message for company %s",
                company_id,
            )

        return True, None

    @staticmethod
    def _notify(
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        recipient_user_id: int,
        note: str | None,
        wants_auto_send: bool,
        sent: bool,
        send_error: str | None,
    ) -> None:
        if wants_auto_send:
            title = (
                "A scheduled reminder message was sent"
                if sent
                else "A scheduled reminder message failed to send"
            )
        else:
            title = "A conversation reminder is due"

        body_lines = [line for line in (note,) if line]

        if wants_auto_send and not sent and send_error:
            body_lines.append(f"The message could not be sent: {send_error}")

        try:
            notification_service.create(
                company_id=company_id,
                notification_type="conversation_reminder",
                title=title,
                body="\n".join(body_lines) or None,
                recipient_user_id=recipient_user_id,
                channel=channel,
                external_user_id=external_user_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not raise a reminder notification for company %s",
                company_id,
            )


conversation_reminder_service = ConversationReminderService()
