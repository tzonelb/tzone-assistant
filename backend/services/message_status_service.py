import sqlite3
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Ordered so we never downgrade a status (e.g. a late "sent" webhook
# arriving after we already recorded "read" shouldn't roll it back).
_STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3}


class MessageStatusService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_delivery_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER,
                    channel TEXT NOT NULL,
                    provider_message_id TEXT NOT NULL,
                    recipient_id TEXT,
                    status TEXT NOT NULL DEFAULT 'sent',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel, provider_message_id)
                )
                """
            )
            conn.commit()

    def record_sent(
        self, *, channel: str, provider_message_id: str, company_id: int | None = None,
        recipient_id: str | None = None,
    ) -> None:
        if not provider_message_id:
            return
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO message_delivery_status (company_id, channel, provider_message_id, recipient_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'sent', ?, ?)
                ON CONFLICT(channel, provider_message_id) DO NOTHING
                """,
                (company_id, channel, provider_message_id, recipient_id, now, now),
            )
            conn.commit()

    def mark_read_by_watermark(self, *, channel: str, recipient_id: str, watermark) -> None:
        """Messenger's read event gives a timestamp watermark, not
        specific message ids — every message sent to this recipient
        at/before that watermark is now read."""
        try:
            watermark_ms = int(watermark)
        except (TypeError, ValueError):
            return
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE message_delivery_status
                SET status = 'read', updated_at = ?
                WHERE channel = ? AND recipient_id = ? AND status != 'read'
                """,
                (now, channel, recipient_id),
            )
            conn.commit()

    def update_status(self, *, channel: str, provider_message_id: str, status: str) -> None:
        """Called from webhook delivery/read events. Never downgrades an
        already-more-advanced status.

        Two delivery-status webhooks for the same message arriving close
        together (plausible — e.g. "delivered" then "read" firing back to
        back) could both miss the SELECT below and both attempt the INSERT,
        so the loser hit the table's UNIQUE(channel, provider_message_id)
        constraint with an unhandled IntegrityError, 500-ing the webhook.
        Retry once: the retry's SELECT finds the winner's committed row and
        takes the UPDATE/rank-check path instead of raising."""
        if status not in _STATUS_RANK or not provider_message_id:
            return
        try:
            self._update_status_once(channel=channel, provider_message_id=provider_message_id, status=status)
        except sqlite3.IntegrityError:
            self._update_status_once(channel=channel, provider_message_id=provider_message_id, status=status)

    def _update_status_once(self, *, channel: str, provider_message_id: str, status: str) -> None:
        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT status FROM message_delivery_status WHERE channel = ? AND provider_message_id = ?",
                (channel, provider_message_id),
            ).fetchone()
            if existing:
                if _STATUS_RANK[status] <= _STATUS_RANK.get(existing["status"], 0):
                    return
                conn.execute(
                    "UPDATE message_delivery_status SET status = ?, updated_at = ? "
                    "WHERE channel = ? AND provider_message_id = ?",
                    (status, now, channel, provider_message_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO message_delivery_status (channel, provider_message_id, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (channel, provider_message_id, status, now, now),
                )
            conn.commit()

    def get_statuses(self, *, channel: str, provider_message_ids: list[str]) -> dict[str, str]:
        if not provider_message_ids:
            return {}
        placeholders = ",".join("?" for _ in provider_message_ids)
        with db.connect() as conn:
            rows = conn.execute(
                f"SELECT provider_message_id, status FROM message_delivery_status "
                f"WHERE channel = ? AND provider_message_id IN ({placeholders})",
                (channel, *provider_message_ids),
            ).fetchall()
        return {row["provider_message_id"]: row["status"] for row in rows}


message_status_service = MessageStatusService()
