"""Company-scoped CRUD + approval workflow for the Scheduler module:
create, approve and schedule social posts from one place. Mirrors the
layered service pattern in task_service.py -- the `scheduled_posts` table
lives in database/database.py's central schema init, so this module does
not own/create its own tables and has no ensure_schema() of its own to
call at startup or in tests.

NOTE: this module tracks and manages the scheduling/approval workflow for
posts. It does NOT publish to any external social platform (Facebook/
Instagram Graph API, etc.) -- there is no such integration wired into
this codebase yet. "Publishing" here is a manual, explicit action (mark
as published) taken by a company user, exactly like the Calls module's
call log is a manual record rather than a live dialer. Wiring a real
auto-publish integration is a distinct, separate follow-up requiring a
provider decision and credentials."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# draft -> scheduled (via approve, requires scheduled_at) -> published
# (manual "mark as published" action). "cancelled" is reachable from
# draft or scheduled. There is no path back out of published/cancelled.
ALLOWED_STATUSES = {"draft", "scheduled", "published", "cancelled"}

_TRANSITIONS = {
    "draft": {"scheduled", "cancelled"},
    "scheduled": {"published", "cancelled", "draft"},
    "published": set(),
    "cancelled": set(),
}


class SchedulerConflictError(Exception):
    """Raised when an update's optimistic-concurrency token is stale, i.e.
    the post was changed by someone else since the client loaded it."""


class SchedulerValidationError(ValueError):
    """Raised for invalid field values: a bad status code, a missing
    title/content/channel, an illegal status transition, or approving a
    post with no scheduled_at."""


_POST_SELECT = """
    SELECT
        p.*,
        creator.full_name AS created_by_name,
        approver.full_name AS approved_by_name
    FROM scheduled_posts p
    LEFT JOIN users creator ON creator.id = p.created_by
    LEFT JOIN users approver ON approver.id = p.approved_by
"""


class SchedulerService:
    EDITABLE_FIELDS = {
        "title",
        "content",
        "channel",
        "media_url",
        "scheduled_at",
    }

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _clean_values(self, values: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in values.items():
            if key not in self.EDITABLE_FIELDS:
                continue
            cleaned[key] = self._clean_text(value)
        return cleaned

    def list_posts(
        self,
        *,
        company_id: int,
        status: str | None = None,
        channel: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["p.company_id = ?"]
        params: list[Any] = [company_id]

        if status and status != "all":
            where.append("p.status = ?")
            params.append(status)

        if channel and channel != "all":
            where.append("p.channel = ?")
            params.append(channel)

        if search and search.strip():
            pattern = f"%{search.strip()}%"
            where.append("(p.title LIKE ? OR p.content LIKE ?)")
            params.extend([pattern, pattern])

        clause = " AND ".join(where)

        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM scheduled_posts p WHERE {clause}",
                params,
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                {_POST_SELECT}
                WHERE {clause}
                ORDER BY
                    CASE p.status
                        WHEN 'draft' THEN 0
                        WHEN 'scheduled' THEN 1
                        WHEN 'published' THEN 2
                        ELSE 3
                    END,
                    (p.scheduled_at IS NULL OR p.scheduled_at = ''),
                    p.scheduled_at ASC,
                    p.created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def get_post(self, *, company_id: int, post_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                f"{_POST_SELECT} WHERE p.id = ? AND p.company_id = ? LIMIT 1",
                (post_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Post not found")
        return dict(row)

    def create_post(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        title = cleaned.get("title")
        content = cleaned.get("content")
        channel = cleaned.get("channel")
        if not title:
            raise SchedulerValidationError("title is required")
        if not content:
            raise SchedulerValidationError("content is required")
        if not channel:
            raise SchedulerValidationError("channel is required")

        now = utc_now_iso()

        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scheduled_posts (
                    company_id, title, content, channel, media_url,
                    status, scheduled_at, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
                """,
                (
                    company_id,
                    title,
                    content,
                    channel,
                    cleaned.get("media_url"),
                    cleaned.get("scheduled_at"),
                    actor_user_id,
                    now,
                    now,
                ),
            )
            post_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_post(company_id=company_id, post_id=post_id)

    def update_post(
        self,
        *,
        company_id: int,
        post_id: int,
        values: dict[str, Any],
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id, status, updated_at FROM scheduled_posts "
                "WHERE id = ? AND company_id = ?",
                (post_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Post not found")

            if (
                expected_updated_at is not None
                and str(existing["updated_at"]) != str(expected_updated_at)
            ):
                raise SchedulerConflictError(
                    "This post was changed elsewhere. Reload to see the "
                    "latest details before editing."
                )

            if existing["status"] not in ("draft", "scheduled"):
                raise SchedulerValidationError(
                    "Published or cancelled posts cannot be edited."
                )

            if "title" in cleaned and not cleaned["title"]:
                raise SchedulerValidationError("title cannot be empty")
            if "content" in cleaned and not cleaned["content"]:
                raise SchedulerValidationError("content cannot be empty")
            if "channel" in cleaned and not cleaned["channel"]:
                raise SchedulerValidationError("channel cannot be empty")
            # An approved (scheduled) post must keep a scheduled time --
            # the same precondition transition_status enforces when
            # approving. Move it back to draft first to clear the time.
            if (
                existing["status"] == "scheduled"
                and "scheduled_at" in cleaned
                and not cleaned["scheduled_at"]
            ):
                raise SchedulerValidationError(
                    "A scheduled post must keep a scheduled_at time. "
                    "Move it back to draft to clear the schedule."
                )

            if not cleaned:
                return self.get_post(company_id=company_id, post_id=post_id)

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE scheduled_posts SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, post_id, company_id],
            )
            conn.commit()

        return self.get_post(company_id=company_id, post_id=post_id)

    def transition_status(
        self,
        *,
        company_id: int,
        post_id: int,
        new_status: str,
        actor_user_id: int | None,
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        """Move a post through the draft -> scheduled -> published /
        cancelled workflow, enforcing the legal-transitions map and any
        status-specific preconditions (approving requires scheduled_at)."""
        new_status = (new_status or "").strip().lower()
        if new_status not in ALLOWED_STATUSES:
            raise SchedulerValidationError(
                f"status must be one of {sorted(ALLOWED_STATUSES)}"
            )

        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM scheduled_posts WHERE id = ? AND company_id = ?",
                (post_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Post not found")

            if (
                expected_updated_at is not None
                and str(existing["updated_at"]) != str(expected_updated_at)
            ):
                raise SchedulerConflictError(
                    "This post was changed elsewhere. Reload to see the "
                    "latest details before editing."
                )

            current_status = existing["status"]
            if new_status not in _TRANSITIONS.get(current_status, set()):
                raise SchedulerValidationError(
                    f"Cannot move a post from '{current_status}' to '{new_status}'."
                )

            if new_status == "scheduled" and not existing["scheduled_at"]:
                raise SchedulerValidationError(
                    "A scheduled_at time is required before approving/scheduling a post."
                )

            fields = {"status": new_status}
            if new_status == "scheduled":
                fields["approved_by"] = actor_user_id
            elif new_status == "published":
                fields["published_at"] = now

            assignments = ", ".join(f"{key} = ?" for key in fields)
            conn.execute(
                f"UPDATE scheduled_posts SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*fields.values(), now, post_id, company_id],
            )
            conn.commit()

        return self.get_post(company_id=company_id, post_id=post_id)

    def delete_post(self, *, company_id: int, post_id: int) -> bool:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM scheduled_posts WHERE id = ? AND company_id = ?",
                (post_id, company_id),
            )
            conn.commit()
            return cursor.rowcount > 0


scheduler_service = SchedulerService()
