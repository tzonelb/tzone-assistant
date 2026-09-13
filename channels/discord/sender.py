"""Sending a message through one company's own Discord bot.

Sending is a plain REST call -- `POST /channels/{id}/messages` with the bot
token -- unlike receiving, which needs the persistent Gateway connection in
`channels/discord/gateway.py`. The token comes from the company's connected
account, the same discipline every other sender in this platform follows: a
shared, platform-wide bot would answer one company's customer from another
company's Discord application.

Buttons and attachments are deliberately not sent here yet, for the same
reason `channels/slack/sender.py` does not: Discord's real equivalent of a
quick-reply is a message component, which needs its own signed interactions
endpoint to receive the click. Sending one without that would put a button
on screen that does nothing when pressed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)


API_BASE = "https://discord.com/api/v10"
TIMEOUT_SECONDS = 15


def send_discord_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender returns.

    Never raises. ``buttons``, when given, are appended as plain text rather
    than dropped -- a customer still sees the department names a flow
    offered, even though they cannot tap one yet.
    """
    try:
        account = resolve(int(company_id), "discord")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send to Discord for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    token = account.get("access_token")

    if not token:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected Discord account has no bot token.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"• {button}" for button in buttons)

    try:
        response = httpx.post(
            f"{API_BASE}/channels/{recipient_id}/messages",
            headers={"Authorization": f"Bot {token}"},
            json={"content": body_text},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord send failed for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400:
        # The error message, never the token -- it is in the header this
        # just sent.
        logger.warning(
            "Discord rejected a message for company %s: %s %s",
            company_id,
            response.status_code,
            body.get("message"),
        )

        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": body.get("message") or "Discord rejected the message.",
        }

    return {
        "ok": True,
        "skipped": False,
        "status_code": response.status_code,
        "response": {"message_id": str(body.get("id") or "") or None},
    }
