"""Public, read-only links to one conversation's transcript.

"Share link" in the chat panel. The token is a bearer credential exactly like
a session token or a password-reset token: minted with `secrets.token_urlsafe`,
stored only as a SHA-256 hash, and looked up by that hash. It lives in the
control-plane database because the public endpoint that resolves it is reached
with nothing but the token -- before any company is known -- and a tenant
database cannot be opened without knowing which one to open first.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from database.manager import database_manager, utc_now_iso


DEFAULT_TTL_HOURS = 72
MAX_ACTIVE_LINKS_PER_CONVERSATION = 20


class ShareLinkError(Exception):
    """A link that cannot be created, with a reason a person may read."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_link(
    *,
    company_id: int,
    channel: str,
    external_user_id: str,
    scope: str = "chat",
    created_by_user_id: int | None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> dict[str, Any]:
    scope = scope if scope in ("chat", "full") else "chat"
    raw_token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    ).isoformat()
    now = utc_now_iso()

    with database_manager.control() as conn:
        active = conn.execute(
            """
            SELECT COUNT(*) AS n FROM conversation_share_links
            WHERE company_id = ? AND channel = ? AND external_user_id = ?
              AND revoked_at IS NULL AND expires_at > ?
            """,
            (int(company_id), channel, external_user_id, now),
        ).fetchone()["n"]
        if int(active) >= MAX_ACTIVE_LINKS_PER_CONVERSATION:
            raise ShareLinkError(
                "This conversation already has the maximum number of active "
                "share links. Revoke one before creating another."
            )

        conn.execute(
            """
            INSERT INTO conversation_share_links (
                company_id, channel, external_user_id, scope, token_hash,
                created_by_user_id, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(company_id),
                channel,
                external_user_id,
                scope,
                _hash(raw_token),
                int(created_by_user_id) if created_by_user_id else None,
                expires_at,
                now,
            ),
        )
        conn.commit()

    return {"token": raw_token, "scope": scope, "expires_at": expires_at}


def resolve(raw_token: str) -> dict[str, Any] | None:
    """The conversation this token points to, if the token is live.

    Live means: exists, not revoked, not expired. Anything else -- a wrong
    guess, a link that was revoked, one that aged out -- returns None rather
    than raising, so the endpoint answers "not found" either way and a stolen
    link cannot be told apart from a mistyped one.
    """
    now = utc_now_iso()
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT company_id, channel, external_user_id, scope
            FROM conversation_share_links
            WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
            """,
            (_hash(raw_token), now),
        ).fetchone()
    return dict(row) if row else None


def revoke(*, company_id: int, link_id: int) -> bool:
    with database_manager.control() as conn:
        cursor = conn.execute(
            """
            UPDATE conversation_share_links
            SET revoked_at = ?
            WHERE id = ? AND company_id = ? AND revoked_at IS NULL
            """,
            (utc_now_iso(), int(link_id), int(company_id)),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_links(
    *, company_id: int, channel: str, external_user_id: str
) -> list[dict[str, Any]]:
    """This conversation's links, never the token -- only its hash is stored."""
    now = utc_now_iso()
    with database_manager.control() as conn:
        rows = conn.execute(
            """
            SELECT id, scope, created_by_user_id, expires_at, created_at,
                   (expires_at > ?) AS is_live
            FROM conversation_share_links
            WHERE company_id = ? AND channel = ? AND external_user_id = ?
              AND revoked_at IS NULL
            ORDER BY created_at DESC
            """,
            (now, int(company_id), channel, external_user_id),
        ).fetchall()
    return [dict(row) for row in rows]
