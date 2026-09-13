"""Sending a message through one company's own Viber public account.

Viber's send API is a plain HTTPS POST to `send_message` with the account's
Authentication Token, so this needs no library beyond `httpx`, already a
dependency -- the same shape `channels/slack/sender.py` and
`channels/discord/sender.py` use.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)


API_BASE = "https://chatapi.viber.com/pa"
TIMEOUT_SECONDS = 15

# Viber refuses a `sender.name` longer than this; truncated rather than
# rejected outright, since a company's own display name is not something a
# customer message should ever fail to send over.
MAX_SENDER_NAME = 28


def send_viber_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender returns.

    Never raises. ``buttons``, when given, are appended to the message body
    as plain text rather than dropped silently -- Viber's real equivalent
    (keyboards) needs its own callback handling this platform does not have
    yet, the same gap documented in the Slack and Discord senders.
    """
    try:
        account = resolve(int(company_id), "viber")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send to Viber for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    token = account.get("access_token")

    if not token:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected Viber account has no bot token.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"• {button}" for button in buttons)

    try:
        response = httpx.post(
            f"{API_BASE}/send_message",
            headers={"X-Viber-Auth-Token": token},
            json={
                "receiver": str(recipient_id),
                "min_api_version": 1,
                "sender": {"name": (account.get("name") or "Support")[:MAX_SENDER_NAME]},
                "type": "text",
                "text": body_text,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Viber send failed for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400 or body.get("status") != 0:
        logger.warning(
            "Viber rejected a message for company %s: %s %s",
            company_id,
            response.status_code,
            body.get("status_message"),
        )

        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": body.get("status_message") or "Viber rejected the message.",
        }

    return {
        "ok": True,
        "skipped": False,
        "status_code": response.status_code,
        "response": {"message_id": str(body.get("message_token") or "") or None},
    }
