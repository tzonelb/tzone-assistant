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

from backend.services.work_index_service import (
    KIND_PENDING_REPLY,
    work_index_service,
)
from config.settings import config
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


def _parse_iso(value: Any) -> datetime | None:
    """Read a stored timestamp, tolerating a naive one written by an older row."""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def _message_limit() -> int:
    return max(1, int(config.PENDING_REPLY_MAX_MESSAGES))


def _deferral_ceiling_seconds() -> int:
    return max(0, int(config.PENDING_REPLY_MAX_DEFERRAL_SECONDS))


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
        is still typing is not answered mid-sentence. Two ceilings stop that
        courtesy from being turned into a stall:

        * the batch holds at most ``PENDING_REPLY_MAX_MESSAGES`` messages, after
          which further arrivals are recorded in the log and not in the row —
          the stored JSON was otherwise free to grow without limit;
        * the wait is only ever pushed out to
          ``PENDING_REPLY_MAX_DEFERRAL_SECONDS`` after the batch was first
          created. Past that the batch is due immediately and goes out with what
          it has, however fast the messages keep coming. A sustained flood at
          one customer previously kept its reply deferred for ever.
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
                    SELECT id, messages_json, created_at FROM pending_replies
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

                    if not isinstance(messages, list):
                        messages = []

                    limit = _message_limit()

                    if len(messages) >= limit:
                        # Kept rather than replaced: the earliest messages are
                        # the ones the reply is being written about, and the
                        # batch is about to be answered anyway.
                        dropped = True
                        logger.warning(
                            "Pending reply batch for company %s %s/%s is at its "
                            "limit of %s messages; the new message was not added",
                            company_id,
                            channel,
                            external_user_id,
                            limit,
                        )
                    else:
                        dropped = False
                        messages.append(message)

                    deliver_after, capped = self._deliver_after(
                        created_at=row["created_at"],
                        delay=delay,
                    )

                    if capped:
                        logger.warning(
                            "Pending reply batch for company %s %s/%s has waited "
                            "its maximum of %ss; delivering rather than deferring "
                            "again",
                            company_id,
                            channel,
                            external_user_id,
                            _deferral_ceiling_seconds(),
                        )

                    # `locked_until` is left alone. It used to be set to NULL
                    # here, which released a batch a worker was holding at that
                    # moment: a message arriving mid-generation freed the lease,
                    # the next sweep claimed the same batch, and the customer
                    # got two replies for one conversation — billed twice.
                    #
                    # The lease belongs to whoever took it. It expires on its
                    # own after LEASE_SECONDS if that worker dies, which is the
                    # case clearing it was reaching for, and `complete` and
                    # `fail` release it deliberately when the work is done.
                    conn.execute(
                        """
                        UPDATE pending_replies
                        SET messages_json = ?,
                            deliver_after = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(messages, ensure_ascii=False),
                            deliver_after,
                            now,
                            row["id"],
                        ),
                    )
                    count = len(messages)
                    deadline = deliver_after
                else:
                    dropped = False
                    capped = False
                    deadline = _iso_in(delay)
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
                            deadline,
                            now,
                            now,
                        ),
                    )
                    count = 1

                # Registered in the control-plane index *before* this
                # transaction commits, and deliberately not wrapped in a
                # try/except. The sweep no longer opens every company on a
                # timer, so a batch nobody registered is a batch nobody
                # collects — the customer waits for a reply that never comes,
                # with nothing in the log to say so. Failing the enqueue is far
                # better: the webhook reports the failure and the provider
                # redelivers.
                #
                # The other order — commit first, register second — can strand
                # work. This order can only leave an entry for a batch that was
                # rolled back, which costs the next sweep one wasted database
                # open and is corrected the moment it looks.
                #
                # Opening the control database while holding this company's
                # write lock is safe: nothing in the platform holds a control
                # write open while waiting for a tenant lock, so there is no
                # cycle to deadlock on.
                work_index_service.note(company_id, KIND_PENDING_REPLY, deadline)

                conn.commit()

            except Exception:
                conn.rollback()
                raise

        return {
            "queued": True,
            "delay_seconds": delay,
            "message_count": count,
            "dropped": dropped,
            "deferral_capped": capped,
        }

    @staticmethod
    def _deliver_after(*, created_at: Any, delay: int) -> tuple[str, bool]:
        """When this batch may go out, and whether the ceiling decided it.

        The requested wait applies until the batch reaches
        ``PENDING_REPLY_MAX_DEFERRAL_SECONDS`` old. Past that the ceiling wins,
        which may put the delivery time in the past — that is the point: the
        next sweep takes the batch instead of deferring it once more.
        """
        requested = utc_now() + timedelta(seconds=delay)
        created = _parse_iso(created_at)

        if created is None:
            return requested.isoformat(), False

        ceiling = created + timedelta(seconds=_deferral_ceiling_seconds())

        if requested <= ceiling:
            return requested.isoformat(), False

        return ceiling.isoformat(), True

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
