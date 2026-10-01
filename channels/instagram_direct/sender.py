"""Sending a message through one company's own Instagram (direct login) account.

Same session-only rule as the poller: this never attempts to log in, only
to resume the session `backend/api/routes/instagram_direct.py` established
at connect time. A session that has gone stale surfaces as a plain failure
here -- the account needs reconnecting, not a retry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from instagrapi import Client
from instagrapi.exceptions import ClientError

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)


def _build_client(account: dict[str, Any]):
    settings_json = account.get("access_token")

    if not settings_json:
        return None

    try:
        settings = json.loads(settings_json)
    except (TypeError, ValueError):
        return None

    client = Client()
    client.set_settings(settings)

    proxy = account.get("verify_token")

    if proxy:
        client.set_proxy(proxy)

    return client


def send_instagram_direct_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender returns.

    ``recipient_id`` is the Instagram DM thread's own id -- the thread is
    the conversation, the same way a Google Chat space or a Slack channel
    id is. ``buttons``, when given, are appended as plain text: Instagram
    DMs have no interactive-element concept this platform can drive through
    the private API, the same fallback every other channel without one uses.
    """
    try:
        account = resolve(int(company_id), "instagram_direct")
    except MissingChannelCredentials as exc:
        logger.warning(
            "Cannot send Instagram (direct) for company %s: %s", company_id, exc
        )
        return {"ok": False, "skipped": False, "error": str(exc)}

    client = _build_client(account)

    if not client:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected Instagram account is missing its session.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"- {button}" for button in buttons)

    try:
        message = client.direct_send(body_text, thread_ids=[int(recipient_id)])
    except (ClientError, ValueError) as exc:
        logger.warning(
            "Instagram (direct) send failed for company %s: %s", company_id, exc
        )
        return {"ok": False, "skipped": False, "error": str(exc) or type(exc).__name__}

    return {
        "ok": True,
        "skipped": False,
        "response": {"message_id": getattr(message, "id", None)},
    }
