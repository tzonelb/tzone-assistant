"""In-app notifications for one company's employees.

Notifications live in the company's own encrypted database. `recipient_user_id`
and `actor_user_id` reference users in the control-plane database and are stored
as plain integers; a name, when one is needed, is resolved with
`auth_service.user_display_names` rather than a join that cannot cross two files.

Table creation belongs to `database/schema_tenant.py` alone.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotificationService:
    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        try:
            result["data"] = json.loads(result.pop("data_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            result["data"] = {}
            result.pop("data_json", None)
        result["is_read"] = bool(result.get("is_read"))
        return result

    # Which stored preference decides whether a notification of each type is
    # raised. Checked here rather than at each call site for the same reason the
    # module gate is: a gate that has to be remembered is a gate that is
    # eventually forgotten, and the way this one fails is silent — a company
    # turns a bell off and the entries keep piling up, or turns one on and
    # nothing arrives.
    #
    # `team_mention` is deliberately absent. A colleague typed somebody's name
    # to get their attention; that is addressed to a person, not a category of
    # event, and there is no preference offering to suppress it.
    PREFERENCE_FOR: dict[str, str] = {
        "customer_message": "new_customer_message",
        "handover": "handover",
        "ai_error": "ai_error",
    }

    def _wanted(self, company_id: int, notification_type: str) -> bool:
        """Whether this company asked to be told about this kind of event.

        Two questions, in order. The operator's module switch comes first: a
        company whose Notifications module is off cannot open the screen these
        rows appear on, so writing them accumulates a pile nobody can clear.
        Then the company's own preference for this kind of event.

        Both fail open. A read that will not complete is a reason to raise the
        notification anyway — the alternative is a company that silently stops
        being told its assistant is failing because a database was busy.
        """
        from backend.services.module_gate import module_gate

        try:
            if not module_gate.enabled(int(company_id), "notifications"):
                return False
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not read the module state of company %s", company_id
            )

        preference = self.PREFERENCE_FOR.get(notification_type)

        if not preference:
            return True

        try:
            from backend.services.company_settings_service import (
                company_settings_service,
            )

            values = company_settings_service.get_section(
                int(company_id), "notifications"
            )["values"]
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not read the notification preferences of company %s",
                company_id,
            )
            return True

        return bool(values.get(preference, True))

    def create(
        self,
        *,
        company_id: int,
        notification_type: str,
        title: str,
        body: str | None = None,
        recipient_user_id: int | None = None,
        channel: str | None = None,
        external_user_id: str | None = None,
        conversation_id: int | None = None,
        actor_user_id: int | None = None,
        severity: str = "info",
        data: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)

        # Before anything is written, and before the dedupe read. A company that
        # switched this kind of notification off should cost nothing at all,
        # not a row it cannot see or a query it did not ask for.
        if not self._wanted(company_id, notification_type.strip()):
            return {}

        created_at = utc_now_iso()
        payload = json.dumps(data or {}, ensure_ascii=False)

        with database_manager.tenant(company_id) as conn:
            if dedupe_key:
                existing = conn.execute(
                    "SELECT * FROM notifications WHERE company_id = ? AND dedupe_key = ? LIMIT 1",
                    (company_id, dedupe_key),
                ).fetchone()
                if existing:
                    return self._row_to_dict(existing)

            cursor = conn.execute(
                """
                INSERT INTO notifications (
                    company_id, recipient_user_id, notification_type,
                    title, body, channel, external_user_id,
                    conversation_id, actor_user_id, severity,
                    data_json, dedupe_key, is_read, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    company_id, recipient_user_id, notification_type.strip(), title.strip(),
                    body, channel, external_user_id, conversation_id, actor_user_id,
                    severity, payload, dedupe_key, created_at,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM notifications WHERE id = ?", (cursor.lastrowid,)).fetchone()

        # Identifiers only: the title and body carry customer text.
        logger.info(
            "Notification created id=%s company id=%s type=%s recipient id=%s",
            cursor.lastrowid,
            company_id,
            notification_type.strip(),
            recipient_user_id,
        )
        return self._row_to_dict(row)

    def list_for_user(
        self,
        *,
        company_id: int,
        user_id: int,
        status: str = "all",
        notification_type: str | None = None,
        channel: str | None = None,
        notification_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions = [
            "company_id = ?",
            "(recipient_user_id IS NULL OR recipient_user_id = ?)",
            "notification_type NOT IN ('ai_reply', 'ai_replied')",
        ]
        params: list[Any] = [company_id, user_id]
        if status == "unread":
            conditions.append("is_read = 0")
        elif status == "read":
            conditions.append("is_read = 1")
        if notification_type:
            conditions.append("notification_type = ?")
            params.append(notification_type)
        if channel:
            conditions.append("channel = ?")
            params.append(channel)
        if notification_date:
            conditions.append("substr(created_at, 1, 10) = ?")
            params.append(notification_date.isoformat())

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM notifications
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC, id DESC
                LIMIT 500
                """,
                tuple(params),
            ).fetchall()

        raw = [self._row_to_dict(row) for row in rows]
        grouped: list[dict[str, Any]] = []
        positions: dict[tuple[str, str, str], int] = {}

        for item in raw:
            is_message = item.get("notification_type") in {"message", "customer_message", "new_message"}
            external_user_id = str(item.get("external_user_id") or "")
            item_channel = str(item.get("channel") or "")
            if is_message and external_user_id and item_channel:
                key = (str(item.get("notification_type")), item_channel, external_user_id)
                if key in positions:
                    target = grouped[positions[key]]
                    target["grouped_count"] += 1
                    target["is_read"] = bool(target["is_read"] and item["is_read"])
                    target["read_at"] = target["read_at"] if target["is_read"] else None
                    target["data"].setdefault("group_notification_ids", []).append(item["id"])
                    target["data"].setdefault("group_items", []).append({
                        "id": item["id"],
                        "body": item.get("body"),
                        "title": item.get("title"),
                        "created_at": item.get("created_at"),
                        "is_read": item.get("is_read"),
                    })
                    continue
                item["grouped_count"] = 1
                item.setdefault("data", {})["group_notification_ids"] = [item["id"]]
                item["data"]["group_items"] = [{
                    "id": item["id"],
                    "body": item.get("body"),
                    "title": item.get("title"),
                    "created_at": item.get("created_at"),
                    "is_read": item.get("is_read"),
                }]
                positions[key] = len(grouped)
            else:
                item["grouped_count"] = 1
            grouped.append(item)

        start = max(0, offset)
        end = start + max(1, min(250, limit))
        return grouped[start:end]

    def summary(self, *, company_id: int, user_id: int) -> dict[str, int]:
        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread,
                       SUM(CASE WHEN is_read = 1 THEN 1 ELSE 0 END) AS read
                FROM notifications
                WHERE company_id = ?
                  AND (recipient_user_id IS NULL OR recipient_user_id = ?)
                  AND notification_type NOT IN ('ai_reply', 'ai_replied')
                """,
                (company_id, user_id),
            ).fetchone()
        return {"total": int(row["total"] or 0), "unread": int(row["unread"] or 0), "read": int(row["read"] or 0)}

    def _visible_ids(self, conn, ids: list[int], company_id: int, user_id: int) -> list[int]:
        clean = sorted({int(item) for item in ids if int(item) > 0})
        if not clean:
            return []
        placeholders = ",".join("?" for _ in clean)
        rows = conn.execute(
            f"""
            SELECT id FROM notifications
            WHERE id IN ({placeholders}) AND company_id = ?
              AND (recipient_user_id IS NULL OR recipient_user_id = ?)
            """,
            (*clean, company_id, user_id),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def set_read_state(self, *, notification_ids: list[int], company_id: int, user_id: int, is_read: bool) -> int:
        with database_manager.tenant(company_id) as conn:
            ids = self._visible_ids(conn, notification_ids, company_id, user_id)
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            cursor = conn.execute(
                f"UPDATE notifications SET is_read = ?, read_at = ? WHERE id IN ({placeholders})",
                (1 if is_read else 0, utc_now_iso() if is_read else None, *ids),
            )
            conn.commit()
            return int(cursor.rowcount)

    def mark_read(self, *, notification_id: int, company_id: int, user_id: int, group_ids: list[int] | None = None) -> bool:
        ids = group_ids or [notification_id]
        return self.set_read_state(notification_ids=ids, company_id=company_id, user_id=user_id, is_read=True) > 0

    def mark_unread(self, *, notification_id: int, company_id: int, user_id: int, group_ids: list[int] | None = None) -> bool:
        ids = group_ids or [notification_id]
        return self.set_read_state(notification_ids=ids, company_id=company_id, user_id=user_id, is_read=False) > 0

    def mark_all_read(self, *, company_id: int, user_id: int) -> int:
        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                UPDATE notifications SET is_read = 1, read_at = ?
                WHERE company_id = ? AND is_read = 0
                  AND (recipient_user_id IS NULL OR recipient_user_id = ?)
                  AND notification_type NOT IN ('ai_reply', 'ai_replied')
                """,
                (utc_now_iso(), company_id, user_id),
            )
            conn.commit()
            return int(cursor.rowcount)


    def clear_visible(
        self,
        *,
        notification_ids: list[int],
        company_id: int,
        user_id: int,
    ) -> int:
        with database_manager.tenant(company_id) as conn:
            ids = self._visible_ids(conn, notification_ids, company_id, user_id)
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            cursor = conn.execute(
                f"DELETE FROM notifications WHERE id IN ({placeholders})",
                tuple(ids),
            )
            conn.commit()
            return int(cursor.rowcount)


notification_service = NotificationService()
