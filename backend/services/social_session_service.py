"""Direct Instagram / Facebook sessions for the Comments module.

These connect with the business's OWN account login — no Meta developer
app, no Graph API tokens:

  * Instagram: username + password (+ optional 2FA code) through the
    private mobile API (instagrapi). The logged-in session is stored
    encrypted; posts and comments sync into the same social_posts /
    social_post_comments tables the Graph webhook feeds, and replies are
    posted back through the same session.
  * Facebook: browser cookies (c_user + xs) pasted from a logged-in
    browser. Posts and comments of the business Page download through
    facebook-scraper (read-only in v1 — replying to Facebook comments
    still needs the official API or the phone app).

Both libraries are optional dependencies: everything degrades to a clean
HTTP 503-style error ("not installed") rather than an import crash.

Channel values (distinct from the Graph-API "instagram"/"messenger"
accounts so no Graph code path ever picks these up):
  instagram_direct — external_account_id = the IG numeric user pk
  facebook_direct  — external_account_id = "fb-cookies-<company_id>"
"""

import json
import logging
from typing import Any

from backend.services.channel_account_service import (
    ChannelAccountError,
    channel_account_service,
)
from backend.services.comment_service import comment_service
from backend.services.crypto_utils import decrypt_secret, encrypt_secret
from database.database import db

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class SocialSessionError(RuntimeError):
    """User-facing connection/sync failure (bad login, challenge, ...)."""


class DependencyMissingError(RuntimeError):
    """The optional library for this channel is not installed."""


def _require_instagrapi():
    try:
        from instagrapi import Client  # noqa: PLC0415
        return Client
    except ImportError as exc:
        raise DependencyMissingError(
            "Instagram direct login needs the 'instagrapi' package: pip install instagrapi"
        ) from exc


def _require_facebook_scraper():
    try:
        import facebook_scraper  # noqa: PLC0415
        return facebook_scraper
    except ImportError as exc:
        raise DependencyMissingError(
            "Facebook direct download needs the 'facebook-scraper' package: pip install facebook-scraper"
        ) from exc


class SocialSessionService:
    # ---- Instagram ---------------------------------------------------

    def connect_instagram(
        self, *, company_id: int, username: str, password: str,
        verification_code: str | None = None,
    ) -> dict[str, Any]:
        """Log in once with the credentials, keep only the resulting
        session (encrypted). The password itself is never stored."""
        Client = _require_instagrapi()
        username = (username or "").strip().lstrip("@")
        if not username or not password:
            raise SocialSessionError("Username and password are both required.")

        code = (verification_code or "").strip()
        client = Client()
        try:
            # instagrapi's 2FA path calls verification_code.strip() unguarded,
            # so it must be a string ("" when none), never None.
            client.login(username, password, verification_code=code)
        except Exception as exc:  # instagrapi raises many exception types
            # Turn instagrapi's raw exceptions into actionable messages the
            # owner can act on, instead of a leaked library stack string.
            name = type(exc).__name__
            text = str(exc)
            if "TwoFactorRequired" in name or "two_factor" in text.lower() or "two-factor" in text.lower():
                if code:
                    raise SocialSessionError(
                        "That two-factor code was wrong or expired. Open your authenticator "
                        "app (or check your SMS) and enter the current 6-digit code."
                    )
                raise SocialSessionError(
                    "This Instagram account has two-factor authentication. Enter the current "
                    "6-digit code from your authenticator app (or SMS) in the 2FA field and try again."
                )
            if "ChallengeRequired" in name or "challenge" in text.lower():
                raise SocialSessionError(
                    "Instagram wants to verify this login. Open the Instagram app, approve the "
                    "login request (or confirm the email/SMS it sent), then try connecting again."
                )
            if "BadPassword" in name or "bad_password" in text.lower() or "incorrect" in text.lower():
                raise SocialSessionError("Instagram rejected the username or password. Please check both and try again.")
            raise SocialSessionError(f"Instagram login failed: {text}")

        user_pk = str(client.user_id)
        settings_blob = json.dumps(client.get_settings())

        with db.connect() as conn:
            channel_account_service._assert_not_owned_by_another_company(
                conn, company_id=company_id, channel="instagram_direct",
                column="external_account_id", value=user_pk,
            )
            existing = conn.execute(
                "SELECT id FROM channel_accounts WHERE company_id = ? AND channel = 'instagram_direct' "
                "AND external_account_id = ?",
                (company_id, user_pk),
            ).fetchone()
            now = utc_now_iso()
            if existing:
                conn.execute(
                    "UPDATE channel_accounts SET access_token_encrypted = ?, status = 'active', updated_at = ? "
                    "WHERE id = ?",
                    (encrypt_secret(settings_blob), now, int(existing["id"])),
                )
                conn.commit()
                return channel_account_service.get_account(account_id=int(existing["id"]))

        channel_account_service._assert_within_channel_limit(company_id=company_id)
        with db.connect() as conn:
            now = utc_now_iso()
            cursor = conn.execute(
                """
                INSERT INTO channel_accounts (
                    company_id, channel, name, external_account_id, page_id,
                    access_token_encrypted, status, ai_enabled, created_at, updated_at
                ) VALUES (?, 'instagram_direct', ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (company_id, f"@{username}", user_pk, username, encrypt_secret(settings_blob), now, now),
            )
            account_id = int(cursor.lastrowid)
            conn.commit()
        return channel_account_service.get_account(account_id=account_id)

    def _instagram_client(self, *, account_id: int):
        Client = _require_instagrapi()
        settings_blob = channel_account_service.get_decrypted_token(account_id=account_id)
        client = Client()
        client.set_settings(json.loads(settings_blob))
        return client

    def sync_instagram(self, *, company_id: int, account_id: int, posts_limit: int = 12) -> dict[str, Any]:
        """Pull the account's recent posts + their comments into the
        Comments module tables."""
        account = channel_account_service.get_account(account_id=account_id)
        if int(account["company_id"]) != int(company_id):
            raise KeyError("Channel account not found")
        client = self._instagram_client(account_id=account_id)
        own_pk = str(account["external_account_id"])

        try:
            medias = client.user_medias(int(own_pk), amount=posts_limit)
        except Exception as exc:
            raise SocialSessionError(f"Could not fetch Instagram posts: {exc}")

        posts = 0
        comments = 0
        for media in medias:
            post_external_id = str(media.pk)
            permalink = f"https://www.instagram.com/p/{media.code}/" if getattr(media, "code", None) else None
            comment_service._upsert_post(
                company_id=company_id,
                channel_account_id=account_id,
                channel="instagram_direct",
                post_external_id=post_external_id,
                caption=(media.caption_text or "")[:2000] if getattr(media, "caption_text", None) else None,
                media_url=str(media.thumbnail_url) if getattr(media, "thumbnail_url", None) else None,
                permalink=permalink,
            )
            posts += 1
            try:
                media_comments = client.media_comments(media.id, amount=0)  # 0 = all
            except Exception as exc:
                logger.warning("Comment fetch failed for media %s: %s", media.pk, exc)
                continue
            for comment in media_comments:
                author_pk = str(comment.user.pk) if getattr(comment, "user", None) else None
                comment_service._upsert_comment(
                    company_id=company_id,
                    channel_account_id=account_id,
                    channel="instagram_direct",
                    post_external_id=post_external_id,
                    comment_external_id=str(comment.pk),
                    parent_comment_external_id=(
                        str(comment.replied_to_comment_id)
                        if getattr(comment, "replied_to_comment_id", None) else None
                    ),
                    author_name=getattr(comment.user, "username", None) if getattr(comment, "user", None) else None,
                    author_external_id=author_pk,
                    text=comment.text or "",
                    platform_created_at=(
                        comment.created_at_utc.isoformat()
                        if getattr(comment, "created_at_utc", None) else None
                    ),
                    is_from_business=1 if author_pk == own_pk else 0,
                )
                comments += 1

        return {"posts_synced": posts, "comments_synced": comments}

    def reply_instagram(self, *, account_id: int, post_external_id: str, comment_external_id: str, text: str) -> dict[str, Any]:
        client = self._instagram_client(account_id=account_id)
        try:
            result = client.media_comment(
                post_external_id, text, replied_to_comment_id=int(comment_external_id),
            )
        except Exception as exc:
            raise SocialSessionError(f"Instagram reply failed: {exc}")
        return {"comment_external_id": str(result.pk)}

    # ---- Facebook (cookie session, download-only v1) -----------------

    def connect_facebook(
        self, *, company_id: int, page: str, c_user: str, xs: str,
    ) -> dict[str, Any]:
        """Store the browser cookie pair (encrypted) + which Page to
        download. Replying via cookies is NOT supported (v1 is read-only)."""
        page = (page or "").strip().rstrip("/").split("/")[-1]
        c_user = (c_user or "").strip()
        xs = (xs or "").strip()
        if not page or not c_user or not xs:
            raise SocialSessionError("Page name and both cookies (c_user, xs) are required.")

        _require_facebook_scraper()
        external_id = f"fb-cookies-{company_id}"
        cookies_blob = json.dumps({"c_user": c_user, "xs": xs, "page": page})

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM channel_accounts WHERE company_id = ? AND channel = 'facebook_direct' LIMIT 1",
                (company_id,),
            ).fetchone()
            now = utc_now_iso()
            if existing:
                conn.execute(
                    "UPDATE channel_accounts SET access_token_encrypted = ?, page_id = ?, name = ?, "
                    "status = 'active', updated_at = ? WHERE id = ?",
                    (encrypt_secret(cookies_blob), page, f"Facebook: {page}", now, int(existing["id"])),
                )
                conn.commit()
                return channel_account_service.get_account(account_id=int(existing["id"]))

        channel_account_service._assert_within_channel_limit(company_id=company_id)
        with db.connect() as conn:
            now = utc_now_iso()
            cursor = conn.execute(
                """
                INSERT INTO channel_accounts (
                    company_id, channel, name, external_account_id, page_id,
                    access_token_encrypted, status, ai_enabled, created_at, updated_at
                ) VALUES (?, 'facebook_direct', ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (company_id, f"Facebook: {page}", external_id, page, encrypt_secret(cookies_blob), now, now),
            )
            account_id = int(cursor.lastrowid)
            conn.commit()
        return channel_account_service.get_account(account_id=account_id)

    def sync_facebook(self, *, company_id: int, account_id: int, posts_limit: int = 6) -> dict[str, Any]:
        account = channel_account_service.get_account(account_id=account_id)
        if int(account["company_id"]) != int(company_id):
            raise KeyError("Channel account not found")
        facebook_scraper = _require_facebook_scraper()

        blob = json.loads(channel_account_service.get_decrypted_token(account_id=account_id))
        cookies = {"c_user": blob["c_user"], "xs": blob["xs"]}
        page = blob["page"]

        posts = 0
        comments = 0
        try:
            for post in facebook_scraper.get_posts(
                page, cookies=cookies, pages=2,
                options={"comments": True, "allow_extra_requests": False},
            ):
                if posts >= posts_limit:
                    break
                post_external_id = str(post.get("post_id") or "")
                if not post_external_id:
                    continue
                comment_service._upsert_post(
                    company_id=company_id,
                    channel_account_id=account_id,
                    channel="facebook_direct",
                    post_external_id=post_external_id,
                    caption=(post.get("text") or "")[:2000] or None,
                    media_url=(post.get("images") or [None])[0],
                    permalink=post.get("post_url"),
                )
                posts += 1
                for comment in (post.get("comments_full") or []):
                    comment_id = str(comment.get("comment_id") or "")
                    if not comment_id:
                        continue
                    comment_service._upsert_comment(
                        company_id=company_id,
                        channel_account_id=account_id,
                        channel="facebook_direct",
                        post_external_id=post_external_id,
                        comment_external_id=comment_id,
                        parent_comment_external_id=None,
                        author_name=comment.get("commenter_name"),
                        author_external_id=str(comment.get("commenter_id") or "") or None,
                        text=comment.get("comment_text") or "",
                        platform_created_at=(
                            comment["comment_time"].isoformat()
                            if comment.get("comment_time") else None
                        ),
                        is_from_business=0,
                    )
                    comments += 1
        except Exception as exc:
            raise SocialSessionError(
                f"Facebook download failed (cookies may have expired — paste fresh ones): {exc}"
            )

        return {"posts_synced": posts, "comments_synced": comments}

    # ---- Shared ------------------------------------------------------

    def list_direct_accounts(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, channel, name, page_id, external_account_id FROM channel_accounts "
                "WHERE company_id = ? AND channel IN ('instagram_direct', 'facebook_direct') "
                "AND status = 'active'",
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def sync_all(self, *, company_id: int) -> dict[str, Any]:
        """Sync every connected direct account; per-account failures are
        reported, not fatal to the others."""
        results: list[dict[str, Any]] = []
        for account in self.list_direct_accounts(company_id=company_id):
            try:
                if account["channel"] == "instagram_direct":
                    outcome = self.sync_instagram(company_id=company_id, account_id=account["id"])
                else:
                    outcome = self.sync_facebook(company_id=company_id, account_id=account["id"])
                results.append({"account_id": account["id"], "channel": account["channel"], **outcome})
            except (SocialSessionError, DependencyMissingError) as exc:
                results.append({"account_id": account["id"], "channel": account["channel"], "error": str(exc)})
            except Exception as exc:
                # Any unexpected error (e.g. a corrupt stored session blob)
                # must not abort the other accounts' sync.
                logger.exception("Unexpected sync failure for account #%s", account["id"])
                results.append({"account_id": account["id"], "channel": account["channel"], "error": f"Unexpected error: {exc}"})
        return {"accounts": results}


social_session_service = SocialSessionService()
