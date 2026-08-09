"""Unified social comment inbox (Buffer-style "Community" tab).

Ingests Facebook Page feed-comment and Instagram comment webhook events,
stores them grouped by post, and lets an employee reply from one inbox via
the Graph API. The webhook itself only becomes live once the platform runs
on a public HTTPS domain and the Meta App subscribes the `feed`/`comments`
fields — but everything here is fully built and testable before that: the
ingestion function takes a raw Meta payload, and the reply path uses the same
requests-based Graph API pattern as scheduled_post_service (mockable in tests).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import requests

from backend.services.channel_account_service import channel_account_service
from config.settings import config
from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommentService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS social_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    channel_account_id INTEGER,
                    channel TEXT NOT NULL,
                    post_external_id TEXT NOT NULL,
                    caption TEXT,
                    media_url TEXT,
                    permalink TEXT,
                    platform_created_at TEXT,
                    last_activity_at TEXT,
                    UNIQUE(company_id, post_external_id),
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS social_post_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    channel_account_id INTEGER,
                    channel TEXT NOT NULL,
                    post_external_id TEXT NOT NULL,
                    comment_external_id TEXT NOT NULL,
                    parent_comment_external_id TEXT,
                    author_name TEXT,
                    author_external_id TEXT,
                    text TEXT,
                    is_from_business INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'unanswered',
                    platform_created_at TEXT,
                    received_at TEXT NOT NULL,
                    replied_by_user_id INTEGER,
                    UNIQUE(company_id, comment_external_id),
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_comments_company_post ON social_post_comments(company_id, post_external_id)"
            )
            conn.commit()

    # -----------------------------------------------------------------
    # Ingestion — from a raw Meta webhook payload
    # -----------------------------------------------------------------
    def _match_account(self, *, company_hint: int | None, channel: str, target_id: str) -> dict[str, Any] | None:
        """Find the channel_account this comment belongs to by the Page id
        (Facebook) or Instagram business id in the webhook entry."""
        with db.connect() as conn:
            if channel == "instagram":
                row = conn.execute(
                    "SELECT * FROM channel_accounts WHERE instagram_business_id = ? LIMIT 1",
                    (target_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM channel_accounts WHERE page_id = ? LIMIT 1",
                    (target_id,),
                ).fetchone()
        return dict(row) if row else None

    def ingest_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse a Meta webhook payload and store any feed/comment events.

        Handles Facebook Page `feed` changes with item == 'comment' and
        Instagram `comments` changes. Non-comment events are ignored. Never
        raises on a malformed payload — returns a summary dict instead, so the
        webhook endpoint stays resilient."""
        stored = 0
        skipped = 0
        try:
            for entry in payload.get("entry", []):
                entry_id = str(entry.get("id") or "")  # Page id or IG business id
                for change in entry.get("changes", []):
                    field = change.get("field")
                    value = change.get("value", {}) or {}
                    if field == "feed" and value.get("item") == "comment":
                        channel = "messenger"
                    elif field == "comments":
                        channel = "instagram"
                    else:
                        skipped += 1
                        continue

                    # Facebook uses verb add/edit/remove; skip removals.
                    if value.get("verb") == "remove":
                        skipped += 1
                        continue

                    comment_id = value.get("comment_id") or value.get("id")
                    if not comment_id:
                        skipped += 1
                        continue

                    account = self._match_account(company_hint=None, channel=channel, target_id=entry_id)
                    if not account:
                        skipped += 1
                        continue

                    post_id = (
                        value.get("post_id")
                        or (value.get("media") or {}).get("id")
                        or value.get("parent_id")
                        or comment_id
                    )
                    author = value.get("from") or {}
                    self._upsert_post(
                        company_id=account["company_id"],
                        channel_account_id=account["id"],
                        channel=channel,
                        post_external_id=str(post_id),
                        caption=(value.get("post") or {}).get("message") if isinstance(value.get("post"), dict) else None,
                        media_url=(value.get("media") or {}).get("media_url") if isinstance(value.get("media"), dict) else None,
                        permalink=value.get("permalink_url"),
                    )
                    self._upsert_comment(
                        company_id=account["company_id"],
                        channel_account_id=account["id"],
                        channel=channel,
                        post_external_id=str(post_id),
                        comment_external_id=str(comment_id),
                        parent_comment_external_id=str(value["parent_id"]) if value.get("parent_id") else None,
                        author_name=author.get("name") or author.get("username"),
                        author_external_id=author.get("id"),
                        text=value.get("message") or value.get("text"),
                        platform_created_at=self._coerce_time(value.get("created_time")),
                    )
                    stored += 1
        except Exception as exc:  # never let webhook parsing crash the endpoint
            return {"stored": stored, "skipped": skipped, "error": str(exc)}
        return {"stored": stored, "skipped": skipped}

    def _coerce_time(self, value: Any) -> str | None:
        if value is None:
            return None
        # Meta sends either an ISO string or a unix timestamp.
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        return str(value)

    def _upsert_post(self, *, company_id, channel_account_id, channel, post_external_id, caption, media_url, permalink):
        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM social_posts WHERE company_id = ? AND post_external_id = ?",
                (company_id, post_external_id),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE social_posts SET
                        caption = COALESCE(?, caption),
                        media_url = COALESCE(?, media_url),
                        permalink = COALESCE(?, permalink),
                        last_activity_at = ?
                    WHERE id = ?
                    """,
                    (caption, media_url, permalink, now, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO social_posts (
                        company_id, channel_account_id, channel, post_external_id,
                        caption, media_url, permalink, platform_created_at, last_activity_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (company_id, channel_account_id, channel, post_external_id, caption, media_url, permalink, now, now),
                )
            conn.commit()

    def _upsert_comment(self, *, company_id, channel_account_id, channel, post_external_id, comment_external_id,
                        parent_comment_external_id, author_name, author_external_id, text, platform_created_at,
                        is_from_business=0):
        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM social_post_comments WHERE company_id = ? AND comment_external_id = ?",
                (company_id, comment_external_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE social_post_comments SET text = COALESCE(?, text) WHERE id = ?",
                    (text, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO social_post_comments (
                        company_id, channel_account_id, channel, post_external_id, comment_external_id,
                        parent_comment_external_id, author_name, author_external_id, text,
                        is_from_business, status, platform_created_at, received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (company_id, channel_account_id, channel, post_external_id, comment_external_id,
                     parent_comment_external_id, author_name, author_external_id, text,
                     is_from_business, "unanswered" if not is_from_business else "answered",
                     platform_created_at, now),
                )
                # An incoming (non-business) reply re-opens the parent as unanswered.
                if not is_from_business and parent_comment_external_id:
                    conn.execute(
                        "UPDATE social_post_comments SET status = 'unanswered' WHERE company_id = ? AND comment_external_id = ?",
                        (company_id, parent_comment_external_id),
                    )
            conn.commit()
            newly_inserted = existing is None

        # A brand-new comment from a customer (not our own reply) raises a
        # bell notification for the team, deduped by the platform comment id
        # so a re-sync of the same comment never notifies twice.
        if newly_inserted and not is_from_business:
            self._notify_new_comment(
                company_id=company_id, channel=channel, comment_external_id=comment_external_id,
                post_external_id=post_external_id, author_name=author_name, text=text,
            )

    @staticmethod
    def _notify_new_comment(*, company_id, channel, comment_external_id, post_external_id, author_name, text):
        try:
            from backend.services.notification_service import notification_service
            who = author_name or "Someone"
            notification_service.create(
                company_id=company_id,
                notification_type="new_comment",
                title=f"New comment from {who}",
                body=text,
                channel=channel,
                severity="info",
                data={"post_external_id": post_external_id, "author_name": author_name},
                dedupe_key=f"comment:{comment_external_id}",
            )
        except Exception:
            # A notification failure must never lose the ingested comment.
            pass

    # -----------------------------------------------------------------
    # Reading — grouped by post (Buffer "By post" view)
    # -----------------------------------------------------------------
    def list_posts(self, *, company_id: int, channel_account_id: int | None = None) -> list[dict[str, Any]]:
        where = ["p.company_id = ?"]
        params: list[Any] = [company_id]
        if channel_account_id:
            where.append("p.channel_account_id = ?")
            params.append(channel_account_id)
        clause = " AND ".join(where)
        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.*,
                    (SELECT COUNT(*) FROM social_post_comments c
                        WHERE c.company_id = p.company_id AND c.post_external_id = p.post_external_id
                        AND c.is_from_business = 0) AS comment_count,
                    (SELECT COUNT(*) FROM social_post_comments c
                        WHERE c.company_id = p.company_id AND c.post_external_id = p.post_external_id
                        AND c.status = 'unanswered' AND c.is_from_business = 0) AS unanswered_count
                FROM social_posts p
                WHERE {clause}
                ORDER BY COALESCE(p.last_activity_at, p.platform_created_at) DESC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def list_comments(self, *, company_id: int, post_external_id: str) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM social_post_comments
                WHERE company_id = ? AND post_external_id = ?
                ORDER BY COALESCE(platform_created_at, received_at) ASC
                """,
                (company_id, post_external_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def unanswered_total(self, *, company_id: int) -> int:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM social_post_comments WHERE company_id = ? AND status = 'unanswered' AND is_from_business = 0",
                (company_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    # -----------------------------------------------------------------
    # Replying — real Graph API call, stored as a business comment
    # -----------------------------------------------------------------
    def reply_to_comment(self, *, company_id: int, comment_id: int, text: str, actor_user_id: int | None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("A reply message is required.")

        with db.connect() as conn:
            comment = conn.execute(
                "SELECT * FROM social_post_comments WHERE id = ? AND company_id = ?",
                (comment_id, company_id),
            ).fetchone()
        if not comment:
            raise KeyError("Comment not found")
        comment = dict(comment)

        account_id = comment["channel_account_id"]

        # Direct-session channels don't go through the Graph API at all.
        if comment["channel"] == "instagram_direct":
            from backend.services.social_session_service import SocialSessionError, social_session_service
            try:
                result = social_session_service.reply_instagram(
                    account_id=account_id,
                    post_external_id=comment["post_external_id"],
                    comment_external_id=comment["comment_external_id"],
                    text=text,
                )
            except SocialSessionError as exc:
                raise ValueError(str(exc))
            data = {"id": result["comment_external_id"]}
            return self._store_reply(
                comment=comment, account_id=account_id, text=text,
                reply_external_id=str(data["id"]), actor_user_id=actor_user_id,
                comment_id=comment_id,
            )
        if comment["channel"] == "facebook_direct":
            raise ValueError(
                "Facebook direct download is read-only — replying needs the official "
                "Facebook connection or the Facebook app on your phone."
            )

        access_token = channel_account_service.get_decrypted_token(account_id=account_id)
        base = f"https://graph.facebook.com/{config.META_API_VERSION}"
        # Facebook: POST /{comment-id}/comments ; Instagram: POST /{ig-comment-id}/replies
        endpoint = "replies" if comment["channel"] == "instagram" else "comments"
        try:
            response = requests.post(
                f"{base}/{comment['comment_external_id']}/{endpoint}",
                data={"message": text, "access_token": access_token},
                timeout=30,
            )
            data = response.json() if response.content else {}
            if not (response.ok and data.get("id")):
                error = (data.get("error", {}) or {}).get("message", "The platform rejected this reply.")
                raise ValueError(error)
        except requests.RequestException as exc:
            raise ValueError(str(exc))

        return self._store_reply(
            comment=comment, account_id=account_id, text=text,
            reply_external_id=str(data["id"]), actor_user_id=actor_user_id,
            comment_id=comment_id,
        )

    def _store_reply(
        self, *, comment: dict[str, Any], account_id: int, text: str,
        reply_external_id: str, actor_user_id: int | None, comment_id: int,
    ) -> dict[str, Any]:
        """Store our reply as a business comment and mark the parent
        answered — shared by the Graph API and direct-session paths."""
        self._upsert_comment(
            company_id=comment["company_id"],
            channel_account_id=account_id,
            channel=comment["channel"],
            post_external_id=comment["post_external_id"],
            comment_external_id=reply_external_id,
            parent_comment_external_id=comment["comment_external_id"],
            author_name="You",
            author_external_id=None,
            text=text,
            platform_created_at=utc_now_iso(),
            is_from_business=1,
        )
        with db.connect() as conn:
            conn.execute(
                "UPDATE social_post_comments SET status = 'answered', replied_by_user_id = ? WHERE id = ?",
                (actor_user_id, comment_id),
            )
            conn.commit()
        return {"reply_external_id": reply_external_id, "status": "answered"}


comment_service = CommentService()
