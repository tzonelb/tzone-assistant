from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotificationService:
    def __init__(self) -> None:
        # Schema setup happens explicitly via main.py's lifespan (after
        # database.database.db.create_tables()), not here — see the
        # matching note in ConversationControlService.__init__.
        pass

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    recipient_user_id INTEGER,
                    notification_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT,
                    channel TEXT,
                    external_user_id TEXT,
                    conversation_id INTEGER,
                    actor_user_id INTEGER,
                    severity TEXT NOT NULL DEFAULT 'info',
                    data_json TEXT NOT NULL DEFAULT '{}',
                    dedupe_key TEXT,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    read_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(recipient_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe
                ON notifications(company_id, dedupe_key)
                WHERE dedupe_key IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notifications_inbox
                ON notifications(company_id, recipient_user_id, is_read, created_at DESC)
                """
            )
            conn.commit()

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
        created_at = utc_now_iso()
        payload = json.dumps(data or {}, ensure_ascii=False)

        # Per-user preference enforcement: when a notification targets a
        # specific recipient, honour that recipient's category preference.
        # Broadcast notifications (recipient_user_id is None) are left alone
        # here — there is no single recipient whose preference applies.
        if recipient_user_id is not None:
            try:
                from backend.services.notification_preference_service import (
                    notification_preference_service,
                )

                if not notification_preference_service.should_notify(
                    user_id=recipient_user_id,
                    company_id=company_id,
                    notification_type=notification_type,
                ):
                    return {"skipped": True, "notification_type": notification_type}
            except Exception:
                # Fail open — never let a preference lookup swallow a real
                # notification.
                pass

        with db.connect() as conn:
            if dedupe_key:
                existing = conn.execute(
                    "SELECT * FROM notifications WHERE company_id = ? AND dedupe_key = ? LIMIT 1",
                    (company_id, dedupe_key),
                ).fetchone()
                if existing:
                    return self._row_to_dict(existing)

            try:
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
            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                # A concurrent create() with the same dedupe_key won the race.
                # The unique index idx_notifications_dedupe rejects the loser's
                # INSERT — under WAL that surfaces as IntegrityError, or as a
                # SQLITE_BUSY_SNAPSHOT OperationalError from the loser's stale
                # read snapshot. Either way, if the winner's row now exists,
                # return it (the intended dedupe result). Only swallow when the
                # row is actually there — a genuine operational error with no
                # existing row still propagates. Makes create() self-healing so
                # callers don't each have to guard the dedupe race.
                if dedupe_key:
                    existing = conn.execute(
                        "SELECT * FROM notifications WHERE company_id = ? AND dedupe_key = ? LIMIT 1",
                        (company_id, dedupe_key),
                    ).fetchone()
                    if existing:
                        return self._row_to_dict(existing)
                raise
            row = conn.execute("SELECT * FROM notifications WHERE id = ?", (cursor.lastrowid,)).fetchone()
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

        with db.connect() as conn:
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
        with db.connect() as conn:
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
        with db.connect() as conn:
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
        with db.connect() as conn:
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
        """Clear the bell's visible unread list without deleting history.

        This used to hard-delete the rows, which also erased them from
        the Notification Center (same underlying table) — "Clear shown"
        is meant to clear the bell dropdown only, so this now marks them
        read instead of deleting them.
        """
        return self.set_read_state(
            notification_ids=notification_ids,
            company_id=company_id,
            user_id=user_id,
            is_read=True,
        )


notification_service = NotificationService()
