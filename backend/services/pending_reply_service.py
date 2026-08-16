"""Durable queue for assistant replies.

Customers often send three short messages instead of one. The platform waits a
few seconds so the assistant answers the whole thought rather than the first
fragment. That wait used to live in a process-memory dictionary driven by
``threading.Timer``, which meant every restart or deploy silently discarded
every message still waiting — the customer simply never got an answer.

The queue is now a table in the company's own database:

* a restart resumes exactly where it left off,
* work is claimed with a lease, so two workers cannot answer the same customer,
* a failure is recorded and retried instead of vanishing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


LEASE_SECONDS = 120
MAX_ATTEMPTS = 5
MIN_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 60


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _iso_in(seconds: float) -> str:
    return (utc_now() + timedelta(seconds=seconds)).isoformat()


class PendingReplyService:
    def enqueue(
        self,
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        message: str,
        delay_seconds: int,
    ) -> dict[str, Any]:
        """Add a message to this customer's pending batch and restart the wait.

        Each new message pushes the delivery time out again, so a customer who
        is still typing is not answered mid-sentence.
        """
        company_id = int(company_id)
        channel = str(channel).strip().lower()
        external_user_id = str(external_user_id).strip()
        delay = max(MIN_DELAY_SECONDS, min(MAX_DELAY_SECONDS, int(delay_seconds)))
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                row = conn.execute(
                    """
                    SELECT id, messages_json FROM pending_replies
                    WHERE company_id = ? AND channel = ? AND external_user_id = ?
                    LIMIT 1
                    """,
                    (company_id, channel, external_user_id),
                ).fetchone()

                if row:
                    try:
                        messages = json.loads(row["messages_json"])
                    except (TypeError, ValueError):
                        messages = []

                    messages.append(message)

                    conn.execute(
                        """
                        UPDATE pending_replies
                        SET messages_json = ?,
                            deliver_after = ?,
                            locked_until = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(messages, ensure_ascii=False),
                            _iso_in(delay),
                            now,
                            row["id"],
                        ),
                    )
                    count = len(messages)
                else:
                    conn.execute(
                        """
                        INSERT INTO pending_replies (
                            company_id, channel, external_user_id, messages_json,
                            deliver_after, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            company_id,
                            channel,
                            external_user_id,
                            json.dumps([message], ensure_ascii=False),
                            _iso_in(delay),
                            now,
                            now,
                        ),
                    )
                    count = 1

                conn.commit()

            except Exception:
                conn.rollback()
                raise

        return {"queued": True, "delay_seconds": delay, "message_count": count}

    def claim_due(self, company_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Take ownership of batches whose wait has elapsed.

        Claiming sets a short lease. A worker that dies mid-reply loses the
        lease and the batch is retried rather than stranded.
        """
        company_id = int(company_id)
        now = utc_now_iso()
        claimed: list[dict[str, Any]] = []

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                rows = conn.execute(
                    """
                    SELECT * FROM pending_replies
                    WHERE deliver_after <= ?
                      AND (locked_until IS NULL OR locked_until <= ?)
                    ORDER BY deliver_after
                    LIMIT ?
                    """,
                    (now, now, limit),
                ).fetchall()

                for row in rows:
                    conn.execute(
                        "UPDATE pending_replies SET locked_until = ?, updated_at = ? WHERE id = ?",
                        (_iso_in(LEASE_SECONDS), now, row["id"]),
                    )

                    try:
                        messages = json.loads(row["messages_json"])
                    except (TypeError, ValueError):
                        messages = []

                    claimed.append(
                        {
                            "id": int(row["id"]),
                            "company_id": company_id,
                            "channel": row["channel"],
                            "external_user_id": row["external_user_id"],
                            "messages": [
                                str(item).strip() for item in messages if str(item).strip()
                            ],
                            "attempts": int(row["attempts"] or 0),
                        }
                    )

                conn.commit()

            except Exception:
                conn.rollback()
                raise

        return claimed

    def complete(self, company_id: int, pending_id: int) -> None:
        with database_manager.tenant(int(company_id)) as conn:
            conn.execute("DELETE FROM pending_replies WHERE id = ?", (pending_id,))
            conn.commit()

    def defer(self, company_id: int, pending_id: int, seconds: int) -> None:
        """Push a batch back without consuming an attempt.

        Used when a human currently owns the conversation: the messages must
        keep waiting until the takeover lapses, not be dropped.
        """
        with database_manager.tenant(int(company_id)) as conn:
            conn.execute(
                """
                UPDATE pending_replies
                SET deliver_after = ?, locked_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_iso_in(seconds), utc_now_iso(), pending_id),
            )
            conn.commit()

    def fail(
        self,
        company_id: int,
        pending_id: int,
        error: str,
        retry_in_seconds: int = 60,
    ) -> bool:
        """Record a failure. Returns whether the batch will be retried."""
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT attempts FROM pending_replies WHERE id = ?", (pending_id,)
            ).fetchone()

            if not row:
                return False

            attempts = int(row["attempts"] or 0) + 1

            if attempts >= MAX_ATTEMPTS:
                logger.error(
                    "Giving up on pending reply id=%s after %s attempts: %s",
                    pending_id,
                    attempts,
                    error,
                )
                conn.execute("DELETE FROM pending_replies WHERE id = ?", (pending_id,))
                conn.commit()
                return False

            conn.execute(
                """
                UPDATE pending_replies
                SET attempts = ?,
                    last_error = ?,
                    deliver_after = ?,
                    locked_until = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    attempts,
                    error[:500],
                    _iso_in(retry_in_seconds * attempts),
                    utc_now_iso(),
                    pending_id,
                ),
            )
            conn.commit()
            return True

    def drop_for_conversation(
        self, company_id: int, channel: str, external_user_id: str
    ) -> None:
        """Discard a pending batch, used when the assistant is switched off."""
        with database_manager.tenant(int(company_id)) as conn:
            conn.execute(
                """
                DELETE FROM pending_replies
                WHERE company_id = ? AND channel = ? AND external_user_id = ?
                """,
                (int(company_id), str(channel).lower(), str(external_user_id)),
            )
            conn.commit()

    def snapshot(self, company_id: int) -> list[dict[str, Any]]:
        """Diagnostics view of what is still waiting."""
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT channel, external_user_id, deliver_after, attempts, last_error
                FROM pending_replies
                ORDER BY deliver_after
                """
            ).fetchall()

        return [dict(row) for row in rows]


pending_reply_service = PendingReplyService()
