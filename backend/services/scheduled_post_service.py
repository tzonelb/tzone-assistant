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


# Buffer schedules to public-content channels (Facebook Page feed,
# Instagram feed, etc) — NOT to private messaging channels like
# WhatsApp/Telegram, which is what the rest of T-ZONE's "channels"
# concept means. This module only ever targets channel_accounts rows
# with channel in POST_CHANNELS.
POST_CHANNELS = {"messenger", "instagram"}

STATUSES = ["draft", "scheduled", "sent", "failed"]
DEFAULT_STATUS = "draft"

# Post type per channel — mirrors Buffer's Post/Reel/Story radio. Facebook
# Stories publishing needs the `pages_manage_posts` + Stories-specific Meta
# App Review approval that most apps never get — real API errors surface
# to the user instead of pretending it silently worked.
POST_TYPES = ["feed", "reels", "story"]
DEFAULT_POST_TYPE = "feed"


class ScheduledPostService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    text TEXT,
                    media_urls_json TEXT NOT NULL DEFAULT '[]',
                    media_type TEXT,
                    channel_account_ids_json TEXT NOT NULL DEFAULT '[]',
                    content_overrides_json TEXT NOT NULL DEFAULT '{}',
                    channel_post_types_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'draft',
                    scheduled_at TEXT,
                    published_at TEXT,
                    results_json TEXT NOT NULL DEFAULT '{}',
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
                """
            )
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(scheduled_posts)")
            }
            if "content_overrides_json" not in existing_columns:
                conn.execute(
                    "ALTER TABLE scheduled_posts ADD COLUMN content_overrides_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "channel_post_types_json" not in existing_columns:
                conn.execute(
                    "ALTER TABLE scheduled_posts ADD COLUMN channel_post_types_json TEXT NOT NULL DEFAULT '{}'"
                )
            conn.commit()

    def _validate_channel_accounts(
        self, *, company_id: int, channel_account_ids: list[int]
    ) -> list[dict[str, Any]]:
        if not channel_account_ids:
            raise ValueError("Select at least one channel to post to.")
        accounts = channel_account_service.list_for_company(company_id=company_id)
        by_id = {account["id"]: account for account in accounts}
        selected = []
        for account_id in channel_account_ids:
            account = by_id.get(account_id)
            if not account:
                raise KeyError(f"Channel account {account_id} not found")
            if account["channel"] not in POST_CHANNELS:
                raise ValueError(
                    f'"{account["channel"]}" is a messaging channel, not a post-able one. '
                    f"Scheduler only posts to: {', '.join(sorted(POST_CHANNELS))}."
                )
            selected.append(account)
        return selected

    def _clean_content_overrides(self, content_overrides: dict[str, Any] | None) -> dict[str, str]:
        if not content_overrides:
            return {}
        cleaned = {}
        for account_id, text in content_overrides.items():
            text = (text or "").strip()
            if text:
                cleaned[str(account_id)] = text
        return cleaned

    def _clean_channel_post_types(
        self, *, channel_post_types: dict[str, Any] | None, channel_account_ids: list[int]
    ) -> dict[str, str]:
        if not channel_post_types:
            return {}
        cleaned = {}
        for account_id in channel_account_ids:
            post_type = channel_post_types.get(str(account_id)) or channel_post_types.get(account_id)
            if not post_type:
                continue
            post_type = str(post_type).strip().lower()
            if post_type not in POST_TYPES:
                raise ValueError(f'"{post_type}" is not a valid post type. Choose one of: {", ".join(POST_TYPES)}.')
            if post_type != DEFAULT_POST_TYPE:
                cleaned[str(account_id)] = post_type
        return cleaned

    def create_post(
        self,
        *,
        company_id: int,
        text: str | None,
        channel_account_ids: list[int],
        media_urls: list[str] | None = None,
        media_type: str | None = None,
        content_overrides: dict[str, Any] | None = None,
        channel_post_types: dict[str, Any] | None = None,
        status: str = DEFAULT_STATUS,
        scheduled_at: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        text = (text or "").strip() or None
        media_urls = media_urls or []
        if not text and not media_urls:
            raise ValueError("Add text or media before saving a post.")

        status = (status or DEFAULT_STATUS).strip().lower()
        if status not in STATUSES:
            raise ValueError(f'"{status}" is not a valid status. Choose one of: {", ".join(STATUSES)}.')
        if status == "scheduled" and not scheduled_at:
            raise ValueError("A scheduled post needs a scheduled_at date/time (or post it now instead).")

        self._validate_channel_accounts(company_id=company_id, channel_account_ids=channel_account_ids)
        cleaned_overrides = self._clean_content_overrides(content_overrides)
        cleaned_post_types = self._clean_channel_post_types(
            channel_post_types=channel_post_types, channel_account_ids=channel_account_ids
        )

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scheduled_posts (
                    company_id, text, media_urls_json, media_type, channel_account_ids_json,
                    content_overrides_json, channel_post_types_json,
                    status, scheduled_at, created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, text, json.dumps(media_urls), media_type,
                    json.dumps(channel_account_ids), json.dumps(cleaned_overrides), json.dumps(cleaned_post_types),
                    status, scheduled_at, actor_user_id, now, now,
                ),
            )
            post_id = int(cursor.lastrowid)
            conn.commit()

        post = self.get_post(company_id=company_id, post_id=post_id)
        if status == "scheduled" and scheduled_at and scheduled_at <= now:
            post = self.publish_post(company_id=company_id, post_id=post_id)
        return post

    def _row_to_dict(self, row) -> dict[str, Any]:
        item = dict(row)
        item["media_urls"] = json.loads(item.pop("media_urls_json") or "[]")
        item["channel_account_ids"] = json.loads(item.pop("channel_account_ids_json") or "[]")
        item["content_overrides"] = json.loads(item.pop("content_overrides_json", None) or "{}")
        item["channel_post_types"] = json.loads(item.pop("channel_post_types_json", None) or "{}")
        item["results"] = json.loads(item.pop("results_json") or "{}")
        return item

    def get_post(self, *, company_id: int, post_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_posts WHERE id = ? AND company_id = ?",
                (post_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Post not found")
        return self._row_to_dict(row)

    def list_posts(self, *, company_id: int, status: str | None = None) -> dict[str, Any]:
        where = ["company_id = ?"]
        params: list[Any] = [company_id]
        if status:
            where.append("status = ?")
            params.append(status)
        clause = " AND ".join(where)

        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM scheduled_posts WHERE {clause}
                ORDER BY
                    CASE WHEN status = 'scheduled' THEN 0 ELSE 1 END,
                    COALESCE(scheduled_at, created_at) ASC
                """,
                params,
            ).fetchall()
        items = [self._row_to_dict(row) for row in rows]
        return {"items": items, "total": len(items)}

    def update_post(self, *, company_id: int, post_id: int, values: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_post(company_id=company_id, post_id=post_id)
        if existing["status"] not in ("draft", "scheduled"):
            raise ValueError("Only draft or scheduled posts can be edited.")

        cleaned: dict[str, Any] = {}
        if "text" in values:
            cleaned["text"] = (values["text"] or "").strip() or None
        if "media_urls" in values:
            cleaned["media_urls_json"] = json.dumps(values["media_urls"] or [])
        if "media_type" in values:
            cleaned["media_type"] = values["media_type"]
        if "channel_account_ids" in values and values["channel_account_ids"] is not None:
            self._validate_channel_accounts(company_id=company_id, channel_account_ids=values["channel_account_ids"])
            cleaned["channel_account_ids_json"] = json.dumps(values["channel_account_ids"])
        if "content_overrides" in values:
            cleaned["content_overrides_json"] = json.dumps(self._clean_content_overrides(values["content_overrides"]))
        if "channel_post_types" in values:
            account_ids = values.get("channel_account_ids") or existing["channel_account_ids"]
            cleaned["channel_post_types_json"] = json.dumps(
                self._clean_channel_post_types(channel_post_types=values["channel_post_types"], channel_account_ids=account_ids)
            )
        if "scheduled_at" in values:
            cleaned["scheduled_at"] = values["scheduled_at"]
        if "status" in values and values["status"] is not None:
            new_status = str(values["status"]).strip().lower()
            if new_status not in STATUSES:
                raise ValueError(f'"{new_status}" is not a valid status. Choose one of: {", ".join(STATUSES)}.')
            cleaned["status"] = new_status

        if not cleaned:
            return existing

        now = utc_now_iso()
        with db.connect() as conn:
            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE scheduled_posts SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, post_id, company_id],
            )
            conn.commit()

        updated = self.get_post(company_id=company_id, post_id=post_id)
        if updated["status"] == "scheduled" and updated["scheduled_at"] and updated["scheduled_at"] <= now:
            updated = self.publish_post(company_id=company_id, post_id=post_id)
        return updated

    def delete_post(self, *, company_id: int, post_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM scheduled_posts WHERE id = ? AND company_id = ?",
                (post_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Post not found")

    # -----------------------------------------------------------------
    # Publishing — real Graph API calls, one per selected channel
    # account. A partial failure (one channel succeeds, another fails)
    # still marks the post 'sent' if at least one channel succeeded,
    # matching Buffer's per-network status model — the per-channel
    # results dict is what the UI should actually render, not just the
    # top-level status.
    # -----------------------------------------------------------------
    def _publish_to_messenger_page(
        self, *, page_id: str, access_token: str, text: str | None, media_urls: list[str],
        media_type: str | None, post_type: str = DEFAULT_POST_TYPE,
    ) -> dict[str, Any]:
        base = f"https://graph.facebook.com/{config.META_API_VERSION}/{page_id}"
        try:
            if post_type == "story" and media_urls:
                endpoint = "video_stories" if media_type == "video" else "photo_stories"
                file_field = "video_url" if media_type == "video" else "url"
                response = requests.post(
                    f"{base}/{endpoint}",
                    data={file_field: media_urls[0], "access_token": access_token},
                    timeout=60,
                )
            elif post_type == "reels" and media_type == "video" and media_urls:
                response = requests.post(
                    f"{base}/video_reels",
                    data={"file_url": media_urls[0], "description": text or "", "access_token": access_token},
                    timeout=60,
                )
            elif media_type == "video" and media_urls:
                response = requests.post(
                    f"{base}/videos",
                    data={"file_url": media_urls[0], "description": text or "", "access_token": access_token},
                    timeout=60,
                )
            elif media_urls:
                response = requests.post(
                    f"{base}/photos",
                    data={"url": media_urls[0], "caption": text or "", "access_token": access_token},
                    timeout=30,
                )
            else:
                response = requests.post(
                    f"{base}/feed",
                    data={"message": text or "", "access_token": access_token},
                    timeout=30,
                )
            data = response.json() if response.content else {}
            if response.ok and (data.get("id") or data.get("post_id")):
                return {"ok": True, "provider_post_id": data.get("post_id") or data.get("id")}
            return {"ok": False, "error": (data.get("error", {}) or {}).get("message", "Facebook rejected this post.")}
        except requests.RequestException as exc:
            return {"ok": False, "error": str(exc)}

    def _publish_to_instagram(
        self, *, ig_user_id: str, access_token: str, text: str | None, media_urls: list[str],
        media_type: str | None, post_type: str = DEFAULT_POST_TYPE,
    ) -> dict[str, Any]:
        if not media_urls:
            return {"ok": False, "error": "Instagram posts require at least one image or video — text-only posts aren't supported by Instagram's API."}
        base = f"https://graph.facebook.com/{config.META_API_VERSION}/{ig_user_id}"
        try:
            container_payload = {"caption": text or "", "access_token": access_token}
            if post_type == "story":
                container_payload["media_type"] = "STORIES"
                if media_type == "video":
                    container_payload["video_url"] = media_urls[0]
                else:
                    container_payload["image_url"] = media_urls[0]
            elif post_type == "reels" or media_type == "video":
                container_payload["media_type"] = "REELS"
                container_payload["video_url"] = media_urls[0]
            else:
                container_payload["image_url"] = media_urls[0]

            container_response = requests.post(f"{base}/media", data=container_payload, timeout=60)
            container_data = container_response.json() if container_response.content else {}
            creation_id = container_data.get("id")
            if not creation_id:
                return {"ok": False, "error": (container_data.get("error", {}) or {}).get("message", "Instagram rejected this media.")}

            publish_response = requests.post(
                f"{base}/media_publish",
                data={"creation_id": creation_id, "access_token": access_token},
                timeout=30,
            )
            publish_data = publish_response.json() if publish_response.content else {}
            if publish_response.ok and publish_data.get("id"):
                return {"ok": True, "provider_post_id": publish_data.get("id")}
            return {"ok": False, "error": (publish_data.get("error", {}) or {}).get("message", "Instagram rejected publishing this post.")}
        except requests.RequestException as exc:
            return {"ok": False, "error": str(exc)}

    def publish_post(self, *, company_id: int, post_id: int) -> dict[str, Any]:
        post = self.get_post(company_id=company_id, post_id=post_id)
        accounts = channel_account_service.list_for_company(company_id=company_id)
        by_id = {account["id"]: account for account in accounts}

        results: dict[str, Any] = {}
        any_success = False
        for account_id in post["channel_account_ids"]:
            account = by_id.get(account_id)
            if not account:
                results[str(account_id)] = {"ok": False, "error": "Channel account no longer exists."}
                continue
            try:
                access_token = channel_account_service.get_decrypted_token(account_id=account["id"])
            except (KeyError, ValueError) as exc:
                results[str(account_id)] = {"ok": False, "error": str(exc)}
                continue

            channel_text = post["content_overrides"].get(str(account_id)) or post["text"]
            channel_post_type = post["channel_post_types"].get(str(account_id), DEFAULT_POST_TYPE)
            if account["channel"] == "messenger":
                result = self._publish_to_messenger_page(
                    page_id=account["page_id"], access_token=access_token,
                    text=channel_text, media_urls=post["media_urls"], media_type=post["media_type"],
                    post_type=channel_post_type,
                )
            else:
                result = self._publish_to_instagram(
                    ig_user_id=account["instagram_business_id"], access_token=access_token,
                    text=channel_text, media_urls=post["media_urls"], media_type=post["media_type"],
                    post_type=channel_post_type,
                )
            results[str(account_id)] = result
            any_success = any_success or result["ok"]

        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_posts
                SET status = ?, results_json = ?, published_at = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                ("sent" if any_success else "failed", json.dumps(results), now, now, post_id, company_id),
            )
            conn.commit()

        # Bell notification on the publish outcome (both worker auto-publish
        # and manual publish-now flow through here). Deduped per post+status
        # so a single post never notifies the same outcome twice.
        self._notify_publish_outcome(company_id=company_id, post=post, succeeded=any_success)

        return self.get_post(company_id=company_id, post_id=post_id)

    @staticmethod
    def _notify_publish_outcome(*, company_id: int, post: dict[str, Any], succeeded: bool) -> None:
        try:
            from backend.services.notification_service import notification_service
            excerpt = (post.get("text") or "").strip()
            excerpt = (excerpt[:80] + "…") if len(excerpt) > 80 else excerpt
            notification_service.create(
                company_id=company_id,
                notification_type="post_published" if succeeded else "post_publish_failed",
                title="Post published" if succeeded else "Post failed to publish",
                body=excerpt or None,
                severity="info" if succeeded else "warning",
                data={"scheduled_post_id": post.get("id")},
                dedupe_key=f"post_publish:{post.get('id')}:{'ok' if succeeded else 'fail'}",
            )
        except Exception:
            pass

    def publish_due_posts(self) -> None:
        """Called by the background worker — publishes every scheduled
        post whose time has arrived, across all companies."""
        now = utc_now_iso()
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, company_id FROM scheduled_posts WHERE status = 'scheduled' AND scheduled_at <= ?",
                (now,),
            ).fetchall()
        for row in rows:
            try:
                self.publish_post(company_id=row["company_id"], post_id=row["id"])
            except Exception:
                continue


scheduled_post_service = ScheduledPostService()
