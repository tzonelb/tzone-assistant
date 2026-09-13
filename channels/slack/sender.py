"""Sending a message through one company's own Slack app.

Slack's send API is a plain HTTPS POST to `chat.postMessage` with the app's
Bot User OAuth Token, so this needs no library beyond `httpx`, already a
dependency. The token comes from the company's connected account, the same
discipline `channels/telegram/sender.py` documents: a shared, platform-wide
token would answer one company's customer from another company's workspace.

Buttons and attachments are deliberately not sent here yet. Slack's real
equivalent of a quick-reply is an interactive Block Kit button, which needs
its own signed callback endpoint to receive the click -- building that without
it would put a button on screen that does nothing when pressed, the same
"looks connected, isn't" defect the channel catalogue was rebuilt to stop
making. A plain-text message is sent either way rather than failing the whole
reply over an unsupported extra.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)


API_BASE = "https://slack.com/api"
TIMEOUT_SECONDS = 15


def send_slack_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender returns.

    Never raises. ``buttons``, when given, are appended to the message body as
    plain text rather than dropped silently -- a customer still sees the
    department names a flow offered, even though they cannot tap one yet.
    """
    try:
        account = resolve(int(company_id), "slack")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send to Slack for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    token = account.get("access_token")

    if not token:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected Slack account has no bot token.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"• {button}" for button in buttons)

    try:
        response = httpx.post(
            f"{API_BASE}/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": str(recipient_id), "text": body_text},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Slack send failed for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400 or not body.get("ok"):
        # The error code, never the token -- it is in the header this just sent.
        logger.warning(
            "Slack rejected a message for company %s: %s %s",
            company_id,
            response.status_code,
            body.get("error"),
        )

        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": body.get("error") or "Slack rejected the message.",
        }

    sent = body.get("message") or {}

    return {
        "ok": True,
        "skipped": False,
        "status_code": response.status_code,
        "response": {"message_id": str(sent.get("ts") or body.get("ts") or "") or None},
    }
