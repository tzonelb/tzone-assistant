"""Sending a message through one company's own LINE Messaging API channel.

LINE has two ways to send: `reply`, keyed to a `replyToken` that expires
within roughly a minute of the webhook delivery it came from and can only be
used once, and `push`, keyed to the customer's persistent `userId` and usable
at any later time. Every reply this platform sends is composed after a
delay -- the assistant's own collection window, a human picking up a
conversation -- so the reply token from the original delivery has already
expired by the time there is anything to send. `push` is therefore the only
message this sender ever makes; there is no code path here that could use a
reply token even if one were kept.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)


API_BASE = "https://api.line.me/v2/bot"
TIMEOUT_SECONDS = 15

# LINE refuses a text message over this length.
MAX_MESSAGE_LENGTH = 5000


def send_line_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one push message and return the same shape every other sender
    returns.

    Never raises. ``buttons``, when given, are appended as plain text rather
    than dropped -- LINE's real equivalent (Quick Reply) needs its own
    callback handling this platform does not have yet, the same gap
    documented in the Slack, Discord and Viber senders.
    """
    try:
        account = resolve(int(company_id), "line")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send to LINE for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    token = account.get("access_token")

    if not token:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected LINE account has no Channel Access Token.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"• {button}" for button in buttons)

    body_text = body_text[:MAX_MESSAGE_LENGTH]

    try:
        response = httpx.post(
            f"{API_BASE}/message/push",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "to": str(recipient_id),
                "messages": [{"type": "text", "text": body_text}],
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("LINE send failed for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {}

        logger.warning(
            "LINE rejected a message for company %s: %s %s",
            company_id,
            response.status_code,
            body.get("message"),
        )

        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": body.get("message") or "LINE rejected the message.",
        }

    return {"ok": True, "skipped": False, "status_code": response.status_code}
