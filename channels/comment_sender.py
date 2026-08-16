"""Publishing a reply under a post comment.

Separate from message sending: a comment reply goes to the Graph API's
``/{comment-id}/replies`` endpoint, not to ``/me/messages``, and it is public.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve
from config.settings import config


logger = logging.getLogger(__name__)

REPLY_TIMEOUT_SECONDS = 15


def publish_comment_reply(
    *,
    company_id: int,
    channel: str,
    provider_comment_id: str,
    message: str,
) -> dict[str, Any]:
    """Publish a reply and return a normalised result.

    Never raises: the caller records the outcome either way, and a failure must
    leave the comment open rather than losing the employee's text.
    """
    normalized_channel = str(channel or "messenger").strip().lower()

    try:
        credentials = resolve(company_id, normalized_channel)
    except MissingChannelCredentials as exc:
        logger.error(
            "Cannot reply to a %s comment for company %s: %s",
            normalized_channel,
            company_id,
            exc,
        )
        return {"ok": False, "reason": "missing_credentials", "error": str(exc)}

    url = (
        f"https://graph.facebook.com/{config.META_API_VERSION}/"
        f"{provider_comment_id}/replies"
    )

    try:
        response = httpx.post(
            url,
            params={"access_token": credentials["access_token"]},
            data={"message": message},
            timeout=REPLY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Comment reply failed for company %s: %s", company_id, type(exc).__name__
        )
        return {"ok": False, "reason": "network_error", "error": str(exc)}

    payload = response.json() if response.content else {}

    if not response.is_success:
        logger.warning(
            "Provider rejected a comment reply for company %s with status %s",
            company_id,
            response.status_code,
        )

    return {
        "ok": response.is_success,
        "status_code": response.status_code,
        "provider_reply_id": payload.get("id"),
        "response": payload,
    }
