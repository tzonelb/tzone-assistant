"""Scheduled publishing to a company's connected pages.

A scheduled post is a promise to publish at a time nobody will be watching, so
the queue is built like the assistant reply queue rather than like a form:
persisted, claimed with a lease, retried on failure, and never silently dropped.

Posts move draft -> approved -> published. Only an approved post is ever
published, so a half-written draft cannot go out because a clock ticked over.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


DRAFT = "draft"
APPROVED = "approved"
PUBLISHED = "published"
FAILED = "failed"
CANCELLED = "cancelled"

STATUSES = (DRAFT, APPROVED, PUBLISHED, FAILED, CANCELLED)

LEASE_SECONDS = 300
MAX_ATTEMPTS = 4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _iso_in(seconds: float) -> str:
    return (utc_now() + timedelta(seconds=seconds)).isoformat()


class SchedulerService:
    # ------------------------------------------------------------------
    # Authoring
    # ------------------------------------------------------------------

    def create_post(
        self,
        *,
        company_id: int,
        channel: str,
        body: str,
        scheduled_for: str,
        created_by_user_id: int,
        media_url: str | None = None,
        link_url: str | None = None,
        channel_account_id: int | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO scheduled_posts (
                    company_id, channel, channel_account_id, body, media_url,
                    link_url, scheduled_for, status, created_by_user_id,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    str(channel).lower(),
                    channel_account_id,
                    body,
                    media_url,
                    link_url,
                    scheduled_for,
                    DRAFT,
                    created_by_user_id,
                    now,
                    now,
                ),
            )
            conn.commit()

        return self.get_post(company_id=company_id, post_id=int(cursor.lastrowid))

    def update_post(
        self, *, company_id: int, post_id: int, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Edit a post that has not gone out yet.

        Published posts are immutable here: the copy on the platform is the real
        one, and letting the record drift from it would make the calendar lie.
        """
        company_id = int(company_id)
        editable = ("body", "media_url", "link_url", "scheduled_for", "channel")

        assignments: list[str] = []
        params: list[Any] = []

        for column in editable:
            if column in values:
                assignments.append(f"{column} = ?")
                params.append(values[column])

        if not assignments:
            return self.get_post(company_id=company_id, post_id=post_id)

        assignments.append("updated_at = ?")
        params.extend([utc_now_iso(), int(post_id), company_id])

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                f"""
                UPDATE scheduled_posts
                SET {', '.join(assignments)}
                WHERE id = ? AND company_id = ?
                  AND status IN ('{DRAFT}', '{APPROVED}', '{FAILED}')
                """,
                params,
            )
            conn.commit()

        if cursor.rowcount == 0:
            return None

        return self.get_post(company_id=company_id, post_id=post_id)

    def approve(
        self, *, company_id: int, post_id: int, approver_user_id: int
    ) -> bool:
        """Mark a draft ready to publish. Only approved posts are ever sent."""
        now = utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                UPDATE scheduled_posts
                SET status = ?, approved_by_user_id = ?, approved_at = ?,
                    attempts = 0, last_error = NULL, updated_at = ?
                WHERE id = ? AND company_id = ? AND status IN (?, ?)
                """,
                (
                    APPROVED,
                    approver_user_id,
                    now,
                    now,
                    int(post_id),
                    int(company_id),
                    DRAFT,
                    FAILED,
                ),
            )
            conn.commit()

        return cursor.rowcount > 0

    def cancel(self, *, company_id: int, post_id: int) -> bool:
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                UPDATE scheduled_posts
                SET status = ?, updated_at = ?
                WHERE id = ? AND company_id = ? AND status != ?
                """,
                (CANCELLED, utc_now_iso(), int(post_id), int(company_id), PUBLISHED),
            )
            conn.commit()

        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get_post(self, *, company_id: int, post_id: int) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_posts WHERE id = ? AND company_id = ? LIMIT 1",
                (int(post_id), int(company_id)),
            ).fetchone()

        return dict(row) if row else None

    def list_posts(
        self,
        *,
        company_id: int,
        status: str | None = None,
        channel: str | None = None,
        starts_after: str | None = None,
        ends_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), 300))

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if status and status in STATUSES:
            where.append("status = ?")
            params.append(status)

        if channel and channel != "all":
            where.append("channel = ?")
            params.append(str(channel).lower())

        if starts_after:
            where.append("scheduled_for >= ?")
            params.append(starts_after)

        if ends_before:
            where.append("scheduled_for <= ?")
            params.append(ends_before)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM scheduled_posts WHERE {clause}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM scheduled_posts
                WHERE {clause}
                ORDER BY scheduled_for ASC, id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, max(0, int(offset))],
            ).fetchall()

            counts = {
                str(row["status"]): int(row["n"])
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM scheduled_posts GROUP BY status"
                ).fetchall()
            }

        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "status_counts": {status: counts.get(status, 0) for status in STATUSES},
        }

    # ------------------------------------------------------------------
    # Publishing queue
    # ------------------------------------------------------------------

    def claim_due(self, company_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Take ownership of approved posts whose time has come.

        The lease is what stops the same post being published twice when a
        sweep overlaps the previous one — a duplicate here is public.
        """
        company_id = int(company_id)
        now = utc_now_iso()
        claimed: list[dict[str, Any]] = []

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduled_posts
                    WHERE status = ?
                      AND scheduled_for <= ?
                      AND (locked_until IS NULL OR locked_until <= ?)
                    ORDER BY scheduled_for
                    LIMIT ?
                    """,
                    (APPROVED, now, now, limit),
                ).fetchall()

                for row in rows:
                    conn.execute(
                        "UPDATE scheduled_posts SET locked_until = ?, updated_at = ? WHERE id = ?",
                        (_iso_in(LEASE_SECONDS), now, row["id"]),
                    )
                    claimed.append(dict(row))

                conn.commit()

            except Exception:
                conn.rollback()
                raise

        return claimed

    def mark_published(
        self, *, company_id: int, post_id: int, provider_post_id: str | None
    ) -> None:
        now = utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            conn.execute(
                """
                UPDATE scheduled_posts
                SET status = ?, published_at = ?, provider_post_id = ?,
                    locked_until = NULL, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (PUBLISHED, now, provider_post_id, now, int(post_id)),
            )
            conn.commit()

    def mark_failed(self, *, company_id: int, post_id: int, error: str) -> bool:
        """Record a failure. Returns whether it will be retried.

        After the last attempt the post stops retrying and stays visible as
        failed, so somebody notices rather than it vanishing from the calendar.
        """
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT attempts FROM scheduled_posts WHERE id = ?", (int(post_id),)
            ).fetchone()

            if not row:
                return False

            attempts = int(row["attempts"] or 0) + 1
            will_retry = attempts < MAX_ATTEMPTS

            conn.execute(
                """
                UPDATE scheduled_posts
                SET attempts = ?, last_error = ?, status = ?,
                    locked_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    attempts,
                    error[:500],
                    APPROVED if will_retry else FAILED,
                    utc_now_iso(),
                    int(post_id),
                ),
            )
            conn.commit()

        if not will_retry:
            logger.error(
                "Scheduled post %s for company %s failed permanently: %s",
                post_id,
                company_id,
                error,
            )

        return will_retry


scheduler_service = SchedulerService()
