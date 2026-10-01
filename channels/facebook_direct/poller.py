"""Polling a connected Facebook Page for new comments.

Unlike every messaging channel's poller or webhook, this does not feed
`channels/inbound.py`'s conversation pipeline at all -- see
`backend/api/routes/facebook_direct.py`'s own docstring for why a public
post's comments are not a customer conversation. What this feeds is
`backend/services/comment_service.py`'s ``post_comments`` table, the same
one the official "messenger"/"instagram" Graph API channels' webhooks
already write to, so a company's Comments queue reads the same regardless
of which channel a comment came in on.

Read-only, like the channel itself: this never calls
`channels/comment_sender.py`'s publisher and never will (see
`backend/api/routes/facebook_direct.py`'s docstring on why). A comment this
platform has already seen is simply not re-inserted --
`comment_service.record_incoming` already de-duplicates on
``provider_comment_id`` per company, the same guard against Meta's own
webhook re-deliveries, so this poller does not need a cursor of its own the
way `instagram_direct`'s does.

### Pacing

Reading a Page means loading its own listing plus every one of its recent
posts, each a real page load in a real browser -- far heavier per sweep
than a poll that is one API call. `FACEBOOK_POLL_SECONDS` is long, and
`backend/workers.py`'s worker still adds jitter around it, for the same
reason Instagram (direct login)'s poller does: a perfectly periodic request
pattern is itself one of the signals unofficial-channel research keeps
pointing at, and it costs nothing here to not be one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial
from backend.services.comment_service import comment_service
from channels.facebook_direct.browser import FacebookSessionError, read_page_comments
from database.manager import database_manager


logger = logging.getLogger(__name__)

# How many of a Page's most recent posts get checked for new comments, each
# sweep -- a hard cap for the same reason every other poller here has one:
# one company's very active Page must not make a sweep run long enough to
# starve every other company's turn in it.
MAX_POSTS_PER_POLL = 10


def poll_all_accounts() -> None:
    """One pass over every company's connected, active Facebook account."""
    with database_manager.control() as conn:
        rows = conn.execute(
            "SELECT id FROM channel_accounts WHERE channel = 'facebook_direct' AND status = 'active'"
        ).fetchall()

    for row in rows:
        account_id = int(row["id"])

        try:
            poll_account(account_id)
        except Exception:
            logger.exception("Polling Facebook account %s failed", account_id)


def _load_cookies(row: Any, *, company_id: int, account_id: int) -> list[dict[str, Any]] | None:
    if not row["access_token_sealed"]:
        return None

    try:
        cookies_json = keyring.unseal_secret(
            row["access_token_sealed"],
            database_manager.company_key(company_id),
            company_id,
            "access_token",
        )
    except CorruptedKeyMaterial:
        logger.error(
            "Facebook cookies for company %s account %s could not be "
            "unsealed; skipping this poll rather than guessing them",
            company_id,
            account_id,
        )
        return None

    try:
        cookies = json.loads(cookies_json)
    except (TypeError, ValueError):
        logger.error(
            "Facebook cookies for company %s account %s are not usable JSON",
            company_id,
            account_id,
        )
        return None

    if not isinstance(cookies, list):
        return None

    return cookies


def poll_account(account_id: int) -> None:
    """Check one Facebook Page for comments this platform has not stored yet."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id, external_account_id, access_token_sealed
            FROM channel_accounts
            WHERE id = ? AND channel = 'facebook_direct' AND status = 'active'
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        return

    company_id = int(row["company_id"])
    page_id = str(row["external_account_id"] or "")

    if not page_id:
        return

    cookies = _load_cookies(row, company_id=company_id, account_id=account_id)

    if not cookies:
        return

    try:
        comments = read_page_comments(cookies, page_id, max_posts=MAX_POSTS_PER_POLL)
    except FacebookSessionError:
        logger.warning(
            "Facebook session for company %s account %s has expired; it "
            "needs to be reconnected",
            company_id,
            account_id,
        )
        return

    for comment in comments:
        comment_service.record_incoming(
            company_id=company_id,
            channel="facebook_direct",
            provider_comment_id=comment["provider_comment_id"],
            message=comment["message"],
            post_id=comment.get("post_id"),
            author_external_id=comment.get("author_external_id"),
            author_name=comment.get("author_name"),
            permalink=(
                f"https://www.facebook.com/{page_id}/posts/{comment['post_id']}"
                if comment.get("post_id")
                else None
            ),
        )
