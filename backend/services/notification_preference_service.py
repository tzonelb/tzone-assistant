from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Which notification_type values map to each user-configurable category.
# Types not listed here are always delivered (e.g. conversation_reminder,
# system) — enforcement only ever *suppresses* the categories a user chose
# to mute, so default/current behaviour is preserved.
_NEW_MESSAGE_TYPES = {"customer_message", "new_message", "message"}
_ESCALATION_TYPES = {
    "ai_escalation",
    "escalation",
    "handoff",
    "handoff_request",
    "human_help",
    "transfer",
}
_MENTION_TYPES = {"conversation_mention", "mention"}
_TASK_TYPES = {"task", "task_assigned", "task_due", "task_reminder"}


DEFAULTS: dict[str, Any] = {
    "notify_new_message": "all",   # enum: "all" | "none"
    "notify_ai_escalation": True,
    "notify_mentions": True,
    "notify_tasks": True,
}


class NotificationPreferenceService:
    """Per-user notification preferences.

    Preferences live WITH the user (user_id + company_id scoped), not with
    the company — every teammate, including the owner, tunes their own
    delivery to taste.
    """

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL,
                    notify_new_message TEXT NOT NULL DEFAULT 'all',
                    notify_ai_escalation INTEGER NOT NULL DEFAULT 1,
                    notify_mentions INTEGER NOT NULL DEFAULT 1,
                    notify_tasks INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, company_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
                """
            )
            conn.commit()

    @staticmethod
    def _row_to_prefs(row: Any) -> dict[str, Any]:
        return {
            "notify_new_message": (row["notify_new_message"] or "all"),
            "notify_ai_escalation": bool(row["notify_ai_escalation"]),
            "notify_mentions": bool(row["notify_mentions"]),
            "notify_tasks": bool(row["notify_tasks"]),
        }

    def get_for_user(self, *, user_id: int, company_id: int) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM notification_preferences WHERE user_id = ? AND company_id = ?",
                    (user_id, company_id),
                ).fetchone()
        except Exception:
            return dict(DEFAULTS)
        if not row:
            return dict(DEFAULTS)
        return self._row_to_prefs(row)

    def update_for_user(self, *, user_id: int, company_id: int, **fields: Any) -> dict[str, Any]:
        current = self.get_for_user(user_id=user_id, company_id=company_id)

        new_message = str(fields.get("notify_new_message", current["notify_new_message"]) or "all").strip().lower()
        if new_message not in ("all", "none"):
            new_message = "all"

        def _as_bool(key: str) -> bool:
            value = fields.get(key, current[key])
            return bool(value)

        merged = {
            "notify_new_message": new_message,
            "notify_ai_escalation": _as_bool("notify_ai_escalation"),
            "notify_mentions": _as_bool("notify_mentions"),
            "notify_tasks": _as_bool("notify_tasks"),
        }

        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_preferences (
                    user_id, company_id, notify_new_message, notify_ai_escalation,
                    notify_mentions, notify_tasks, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, company_id) DO UPDATE SET
                    notify_new_message = excluded.notify_new_message,
                    notify_ai_escalation = excluded.notify_ai_escalation,
                    notify_mentions = excluded.notify_mentions,
                    notify_tasks = excluded.notify_tasks,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id, company_id, merged["notify_new_message"],
                    1 if merged["notify_ai_escalation"] else 0,
                    1 if merged["notify_mentions"] else 0,
                    1 if merged["notify_tasks"] else 0,
                    utc_now_iso(),
                ),
            )
            conn.commit()
        return merged

    def should_notify(self, *, user_id: int, company_id: int, notification_type: str) -> bool:
        """Return whether the recipient wants this notification category.

        Fail-open: anything not covered by a category, and any lookup error,
        results in True so existing behaviour is never accidentally silenced.
        """
        ntype = (notification_type or "").strip()
        prefs = self.get_for_user(user_id=user_id, company_id=company_id)

        if ntype in _NEW_MESSAGE_TYPES:
            return prefs["notify_new_message"] != "none"
        if ntype in _ESCALATION_TYPES:
            return bool(prefs["notify_ai_escalation"])
        if ntype in _MENTION_TYPES:
            return bool(prefs["notify_mentions"])
        if ntype in _TASK_TYPES:
            return bool(prefs["notify_tasks"])
        return True


notification_preference_service = NotificationPreferenceService()
