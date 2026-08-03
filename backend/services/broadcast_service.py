from datetime import datetime, timezone
from typing import Any

from channels.meta.sender import send_meta_text
from channels.whatsapp.sender import send_whatsapp_text
from core.conversation_store import save_conversation_message
from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Conversation statuses that should never receive a broadcast message.
DEAD_CONVERSATION_STATUSES = {
    "closed",
    "archived",
}


class BroadcastAlreadyRunning(Exception):
    """Raised when start_or_resume_broadcast is called on a broadcast that
    is already in the 'sending' state (or otherwise not startable)."""

    def __init__(self, status: str):
        self.status = status
        super().__init__(
            f"Broadcast is already in status '{status}'."
        )


class BroadcastNotFound(Exception):
    pass


class BroadcastService:
    SUPPORTED_CHANNELS = {"messenger", "instagram", "whatsapp"}

    # ------------------------------------------------------------------
    # Recipient resolution — the single source of truth for who gets a
    # broadcast. Used both for the create-time estimate and the
    # send-time actual list, so the two numbers are always computed
    # identically.
    # ------------------------------------------------------------------
    def resolve_recipients(
        self,
        company_id: int,
        channel: str,
        target_department: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id AS conversation_id, external_user_id
            FROM conversations
            WHERE company_id = ?
              AND channel = ?
              AND external_user_id IS NOT NULL
              AND TRIM(external_user_id) != ''
              AND status NOT IN ({dead_statuses})
        """.format(
            dead_statuses=",".join(
                "?" for _ in DEAD_CONVERSATION_STATUSES
            )
        )

        params: list[Any] = [company_id, channel]
        params.extend(DEAD_CONVERSATION_STATUSES)

        if target_department:
            query += " AND department = ?"
            params.append(target_department)

        with db.connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "conversation_id": row["conversation_id"],
                "external_user_id": row["external_user_id"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    def create_broadcast(
        self,
        company_id: int,
        channel: str,
        message_text: str,
        target_department: str | None,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        normalized_channel = channel.strip().lower()

        if normalized_channel not in self.SUPPORTED_CHANNELS:
            if normalized_channel == "telegram":
                raise ValueError(
                    "Telegram broadcast is not available yet."
                )
            raise ValueError(
                "Unsupported broadcast channel. Supported channels are: "
                + ", ".join(sorted(self.SUPPORTED_CHANNELS))
                + "."
            )

        normalized_department = (
            target_department.strip()
            if target_department and target_department.strip()
            else None
        )

        recipients = self.resolve_recipients(
            company_id=company_id,
            channel=normalized_channel,
            target_department=normalized_department,
        )

        now = utc_now_iso()

        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO broadcasts (
                    company_id, channel, message_text, target_department,
                    status, estimated_recipient_count, actual_recipient_count,
                    sent_count, failed_count, created_by_user_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, 0, 0, 0, ?, ?, ?)
                """,
                (
                    company_id,
                    normalized_channel,
                    message_text,
                    normalized_department,
                    len(recipients),
                    actor_user_id,
                    now,
                    now,
                ),
            )
            broadcast_id = cursor.lastrowid
            conn.commit()

        return self.get_broadcast(broadcast_id, company_id)

    # ------------------------------------------------------------------
    # Start / Resume — the idempotency guard.
    # ------------------------------------------------------------------
    def start_or_resume_broadcast(
        self,
        broadcast_id: int,
        company_id: int,
    ) -> dict[str, Any]:
        now = utc_now_iso()

        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE broadcasts
                SET status = 'sending',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                  AND status IN ('draft', 'paused')
                """,
                (now, now, broadcast_id, company_id),
            )
            conn.commit()

            if cursor.rowcount == 0:
                row = conn.execute(
                    """
                    SELECT status
                    FROM broadcasts
                    WHERE id = ? AND company_id = ?
                    """,
                    (broadcast_id, company_id),
                ).fetchone()

                if not row:
                    raise BroadcastNotFound(
                        f"Broadcast {broadcast_id} not found."
                    )

                raise BroadcastAlreadyRunning(row["status"])

        # We now hold the exclusive right to run this broadcast. Resolve
        # the fresh/actual recipient list and seed any missing recipient
        # rows (idempotent via INSERT OR IGNORE + the unique index).
        try:
            broadcast = self.get_broadcast(broadcast_id, company_id)
            channel = broadcast["channel"]
            target_department = broadcast["target_department"]

            recipients = self.resolve_recipients(
                company_id=company_id,
                channel=channel,
                target_department=target_department,
            )

            with db.connect() as conn:
                conn.execute(
                    """
                    UPDATE broadcasts
                    SET actual_recipient_count = ?, updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (len(recipients), utc_now_iso(), broadcast_id, company_id),
                )

                conn.executemany(
                    """
                    INSERT OR IGNORE INTO broadcast_recipients (
                        broadcast_id, conversation_id, external_user_id, status
                    ) VALUES (?, ?, ?, 'pending')
                    """,
                    [
                        (
                            broadcast_id,
                            recipient["conversation_id"],
                            recipient["external_user_id"],
                        )
                        for recipient in recipients
                    ],
                )

                conn.commit()
        except Exception:
            with db.connect() as conn:
                conn.execute(
                    """
                    UPDATE broadcasts
                    SET status = 'paused', updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (utc_now_iso(), broadcast_id, company_id),
                )
                conn.commit()
            raise

        return self.get_broadcast(broadcast_id, company_id)

    # ------------------------------------------------------------------
    # The actual per-recipient send loop. Intended to be run as a
    # background task after start_or_resume_broadcast has synchronously
    # flipped the broadcast to 'sending' and seeded recipient rows.
    # ------------------------------------------------------------------
    def run_send_loop(
        self,
        broadcast_id: int,
        company_id: int,
        actor_user_id: int | None,
    ) -> None:
        try:
            broadcast = self.get_broadcast(broadcast_id, company_id)
        except BroadcastNotFound:
            return

        if not broadcast:
            return

        channel = broadcast["channel"]
        message_text = broadcast["message_text"]

        with db.connect() as conn:
            pending_rows = conn.execute(
                """
                SELECT id, conversation_id, external_user_id
                FROM broadcast_recipients
                WHERE broadcast_id = ? AND status = 'pending'
                ORDER BY id ASC
                """,
                (broadcast_id,),
            ).fetchall()

        for recipient_row in pending_rows:
            recipient_id = recipient_row["id"]
            external_user_id = recipient_row["external_user_id"]

            try:
                if channel in ("messenger", "instagram"):
                    send_result = send_meta_text(
                        recipient_id=external_user_id,
                        text=message_text,
                        channel=channel,
                        company_id=company_id,
                    )
                    ok = bool(send_result.get("ok"))
                    error_message = self._extract_meta_error(send_result)
                elif channel == "whatsapp":
                    send_result = send_whatsapp_text(
                        to=external_user_id,
                        text=message_text,
                    )
                    ok = bool(send_result.get("sent"))
                    error_message = self._extract_whatsapp_error(send_result)
                else:
                    ok = False
                    error_message = f"Unsupported channel '{channel}'."

                if ok:
                    self._mark_recipient_sent(broadcast_id, recipient_id)

                    save_conversation_message(
                        channel=channel,
                        user_id=external_user_id,
                        direction="out",
                        text=message_text,
                        metadata={
                            "source": "broadcast",
                            "broadcast_id": broadcast_id,
                            "sender_type": "employee",
                            "employee_id": actor_user_id,
                        },
                    )
                else:
                    self._mark_recipient_failed(
                        broadcast_id, recipient_id, error_message
                    )

            except Exception as exc:  # noqa: BLE001 - one bad recipient must not abort the batch
                self._mark_recipient_failed(
                    broadcast_id, recipient_id, str(exc)
                )

        with db.connect() as conn:
            conn.execute(
                """
                UPDATE broadcasts
                SET status = 'completed', completed_at = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (utc_now_iso(), utc_now_iso(), broadcast_id, company_id),
            )
            conn.commit()

    @staticmethod
    def _extract_meta_error(send_result: dict[str, Any]) -> str:
        response_data = send_result.get("response")

        if isinstance(response_data, dict):
            meta_error = response_data.get("error")
            if isinstance(meta_error, dict):
                error_message = meta_error.get("message")
                if error_message:
                    return str(error_message)

        return str(
            send_result.get("error")
            or send_result.get("reason")
            or "Message provider rejected the message."
        )

    @staticmethod
    def _extract_whatsapp_error(send_result: dict[str, Any]) -> str:
        response_data = send_result.get("response")

        if isinstance(response_data, dict):
            wa_error = response_data.get("error")
            if isinstance(wa_error, dict):
                error_message = wa_error.get("message")
                if error_message:
                    return str(error_message)

        return str(
            send_result.get("reason")
            or f"WhatsApp send failed (status {send_result.get('status_code')})."
        )

    def _mark_recipient_sent(self, broadcast_id: int, recipient_id: int) -> None:
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE broadcast_recipients
                SET status = 'sent', sent_at = ?, error = NULL
                WHERE id = ?
                """,
                (now, recipient_id),
            )
            conn.execute(
                """
                UPDATE broadcasts
                SET sent_count = sent_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, broadcast_id),
            )
            conn.commit()

    def _mark_recipient_failed(
        self,
        broadcast_id: int,
        recipient_id: int,
        error_message: str,
    ) -> None:
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE broadcast_recipients
                SET status = 'failed', error = ?
                WHERE id = ?
                """,
                (error_message, recipient_id),
            )
            conn.execute(
                """
                UPDATE broadcasts
                SET failed_count = failed_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, broadcast_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_broadcast(
        self,
        broadcast_id: int,
        company_id: int,
    ) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM broadcasts
                WHERE id = ? AND company_id = ?
                """,
                (broadcast_id, company_id),
            ).fetchone()

        if not row:
            raise BroadcastNotFound(f"Broadcast {broadcast_id} not found.")

        return dict(row)

    def list_broadcasts(self, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM broadcasts
                WHERE company_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (company_id,),
            ).fetchall()

        return [dict(row) for row in rows]


broadcast_service = BroadcastService()
