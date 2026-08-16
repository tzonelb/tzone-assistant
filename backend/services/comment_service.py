"""Comments left on a company's Facebook and Instagram posts.

A comment is public. An unanswered one sits under the company's advertising for
everyone to read, which is why this is a working queue rather than a feed: every
comment has a status, and replying closes it.

Stored in the company's own encrypted database, like every other customer-facing
record.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


OPEN = "open"
ANSWERED = "answered"
IGNORED = "ignored"
STATUSES = (OPEN, ANSWERED, IGNORED)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommentService:
    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def record_incoming(
        self,
        *,
        company_id: int,
        channel: str,
        provider_comment_id: str,
        message: str,
        post_id: str | None = None,
        parent_comment_id: str | None = None,
        author_external_id: str | None = None,
        author_name: str | None = None,
        permalink: str | None = None,
        post_caption: str | None = None,
    ) -> dict[str, Any]:
        """Store one comment from a webhook.

        Returns ``{"duplicate": True, ...}`` when the provider id is already
        known. Meta re-delivers, and a duplicate would show the team the same
        unanswered comment twice.
        """
        company_id = int(company_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                existing = conn.execute(
                    """
                    SELECT id FROM post_comments
                    WHERE company_id = ? AND provider_comment_id = ?
                    LIMIT 1
                    """,
                    (company_id, str(provider_comment_id)),
                ).fetchone()

                if existing:
                    conn.rollback()
                    return {"duplicate": True, "id": int(existing["id"])}

                cursor = conn.execute(
                    """
                    INSERT INTO post_comments (
                        company_id, channel, provider_comment_id, parent_comment_id,
                        post_id, post_caption, author_external_id, author_name,
                        message, permalink, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        str(channel).lower(),
                        str(provider_comment_id),
                        parent_comment_id,
                        post_id,
                        post_caption,
                        author_external_id,
                        author_name,
                        message or "",
                        permalink,
                        OPEN,
                        now,
                        now,
                    ),
                )
                conn.commit()

            except Exception:
                conn.rollback()
                raise

            return {"duplicate": False, "id": int(cursor.lastrowid)}

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_comments(
        self,
        *,
        company_id: int,
        status: str | None = None,
        channel: str | None = None,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if status and status in STATUSES:
            where.append("status = ?")
            params.append(status)

        if channel and channel != "all":
            where.append("channel = ?")
            params.append(str(channel).lower())

        normalized_search = str(search or "").strip().lower()
        if normalized_search:
            where.append(
                "(LOWER(message) LIKE ? OR LOWER(COALESCE(author_name, '')) LIKE ?)"
            )
            params.extend([f"%{normalized_search}%"] * 2)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM post_comments WHERE {clause}", params
                ).fetchone()["n"]
            )

            rows = conn.execute(
                f"""
                SELECT c.*,
                       (
                           SELECT COUNT(*) FROM comment_replies r
                           WHERE r.comment_id = c.id
                       ) AS reply_count
                FROM post_comments c
                WHERE {clause}
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

            counts = {
                str(row["status"]): int(row["n"])
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM post_comments GROUP BY status"
                ).fetchall()
            }

        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "status_counts": {status: counts.get(status, 0) for status in STATUSES},
        }

    def get_comment(self, *, company_id: int, comment_id: int) -> dict[str, Any] | None:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT * FROM post_comments WHERE id = ? AND company_id = ? LIMIT 1",
                (int(comment_id), company_id),
            ).fetchone()

            if not row:
                return None

            replies = conn.execute(
                """
                SELECT * FROM comment_replies
                WHERE comment_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (int(comment_id),),
            ).fetchall()

        comment = dict(row)
        comment["replies"] = [dict(reply) for reply in replies]
        return comment

    # ------------------------------------------------------------------
    # Replying
    # ------------------------------------------------------------------

    def record_reply(
        self,
        *,
        company_id: int,
        comment_id: int,
        body: str,
        author_user_id: int | None,
        provider_reply_id: str | None = None,
        send_status: str = "sent",
        error: str | None = None,
    ) -> dict[str, Any]:
        """Store a reply and close the comment when it actually went out.

        A failed send leaves the comment open on purpose: it is still visible
        to the public and still needs an answer.
        """
        company_id = int(company_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO comment_replies (
                    company_id, comment_id, provider_reply_id, author_user_id,
                    sender_type, body, send_status, error, created_at
                )
                VALUES (?, ?, ?, ?, 'employee', ?, ?, ?, ?)
                """,
                (
                    company_id,
                    int(comment_id),
                    provider_reply_id,
                    author_user_id,
                    body,
                    send_status,
                    error,
                    now,
                ),
            )

            if send_status == "sent":
                conn.execute(
                    """
                    UPDATE post_comments
                    SET status = ?, replied_at = ?, updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (ANSWERED, now, now, int(comment_id), company_id),
                )

            conn.commit()

        return {"id": int(cursor.lastrowid), "created_at": now}

    def set_status(
        self, *, company_id: int, comment_id: int, status: str
    ) -> bool:
        if status not in STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(STATUSES)}.")

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                UPDATE post_comments
                SET status = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (status, utc_now_iso(), int(comment_id), int(company_id)),
            )
            conn.commit()

        return cursor.rowcount > 0

    def open_count(self, company_id: int) -> int:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM post_comments WHERE status = ?", (OPEN,)
            ).fetchone()

        return int(row["n"])


comment_service = CommentService()
