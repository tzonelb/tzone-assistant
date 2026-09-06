"""What one employee wants to be told about, inside one company.

There are two gates on a notification and they answer different questions.
`company_settings_service`'s `notifications` section is the *company* deciding
whether an event is worth a row at all — it is enforced in
`notification_service._wanted`, before anything is written. This module is one
*person* deciding whether a row that was written is delivered to them.

Neither can stand in for the other. Folding this into the company section would
have made an owner muting their own task reminders mute them for the whole team;
folding the company's switches into here would have written a row per employee
for an event the company had already said it did not want.

The table lives in the company's own encrypted database. `user_id` points at a
control-plane user and is stored as a plain integer, the same way
`company_settings_service` stores `actor_user_id` and for the same reason:
SQLite cannot join across two files. Table creation belongs to
`database/schema_tenant.py` alone.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULTS: dict[str, Any] = {
    # "all" or "none". A three-way choice was drawn once and had no third
    # meaning behind it, so it is two.
    "notify_new_message": "all",
    "notify_ai_escalation": True,
    "notify_mentions": True,
    "notify_tasks": True,
}

_FLAGS: tuple[str, ...] = (
    "notify_ai_escalation",
    "notify_mentions",
    "notify_tasks",
)

# Which `notification_type` values each preference governs.
#
# A type that appears in none of these is always delivered. That is deliberate
# and is the whole safety property of this module: it can only ever *suppress*
# a category somebody explicitly turned off, so a notification type added later
# is delivered until somebody decides otherwise, rather than silently dropped
# because nobody remembered to list it.
_GOVERNS: dict[str, frozenset[str]] = {
    "notify_new_message": frozenset({"customer_message", "new_message", "message"}),
    "notify_ai_escalation": frozenset(
        {"ai_escalation", "escalation", "handover", "handoff", "human_help"}
    ),
    "notify_mentions": frozenset({"mention", "conversation_mention", "team_chat_mention"}),
    "notify_tasks": frozenset({"task", "task_assigned", "task_due", "task_reminder"}),
}


class NotificationPreferenceService:
    @staticmethod
    def _row_to_values(row: Any) -> dict[str, Any]:
        return {
            "notify_new_message": str(row["notify_new_message"] or "all"),
            "notify_ai_escalation": bool(row["notify_ai_escalation"]),
            "notify_mentions": bool(row["notify_mentions"]),
            "notify_tasks": bool(row["notify_tasks"]),
        }

    def get_for_user(self, *, company_id: int, user_id: int) -> dict[str, Any]:
        """This employee's choices, or the defaults if they have made none.

        A missing row is not an error and never becomes one: somebody who has
        never opened the screen gets everything, which is what they had before
        the screen existed.
        """
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                """
                SELECT notify_new_message, notify_ai_escalation,
                       notify_mentions, notify_tasks
                FROM notification_preferences
                WHERE company_id = ? AND user_id = ?
                LIMIT 1
                """,
                (int(company_id), int(user_id)),
            ).fetchone()

        return self._row_to_values(row) if row else dict(DEFAULTS)

    def update_for_user(
        self, *, company_id: int, user_id: int, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge a partial change over what is stored.

        Partial because the screen sends only what it drew, and a screen that
        omitted a key would otherwise reset it to the default — a save that
        silently undoes a choice the employee made on a different visit.
        """
        company_id, user_id = int(company_id), int(user_id)
        current = self.get_for_user(company_id=company_id, user_id=user_id)

        new_message = str(
            values.get("notify_new_message", current["notify_new_message"]) or "all"
        ).strip().lower()

        # Refused rather than stored: an unrecognised value reads back exactly
        # like a decision that was applied and would deliver everything anyway,
        # which is the failure this whole screen exists to make visible.
        if new_message not in ("all", "none"):
            raise ValueError(
                "notify_new_message must be 'all' or 'none'. "
                f"It was {new_message!r}."
            )

        merged = {"notify_new_message": new_message}

        for key in _FLAGS:
            merged[key] = bool(values.get(key, current[key]))

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                INSERT INTO notification_preferences (
                    company_id, user_id, notify_new_message, notify_ai_escalation,
                    notify_mentions, notify_tasks, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, user_id)
                DO UPDATE SET
                    notify_new_message = excluded.notify_new_message,
                    notify_ai_escalation = excluded.notify_ai_escalation,
                    notify_mentions = excluded.notify_mentions,
                    notify_tasks = excluded.notify_tasks,
                    updated_at = excluded.updated_at
                """,
                (
                    company_id,
                    user_id,
                    merged["notify_new_message"],
                    1 if merged["notify_ai_escalation"] else 0,
                    1 if merged["notify_mentions"] else 0,
                    1 if merged["notify_tasks"] else 0,
                    now,
                    now,
                ),
            )
            conn.commit()

        return merged

    def wants(
        self, *, company_id: int, user_id: int, notification_type: str
    ) -> bool:
        """Whether this employee asked to be told about this kind of event.

        Fails open, the same way the company gate above it does. A read that
        will not complete is a reason to deliver the notification anyway — the
        alternative is somebody quietly stopping being told their assistant is
        failing because a database was busy.
        """
        kind = (notification_type or "").strip()

        governing = next(
            (key for key, types in _GOVERNS.items() if kind in types), None
        )

        if governing is None:
            return True

        try:
            values = self.get_for_user(company_id=company_id, user_id=user_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not read the notification preferences of user %s in company %s",
                user_id,
                company_id,
            )
            return True

        if governing == "notify_new_message":
            return values["notify_new_message"] != "none"

        return bool(values[governing])


notification_preference_service = NotificationPreferenceService()
