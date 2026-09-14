"""Sending a message through one company's own Google Chat app.

A Chat app authenticates its own outbound calls the same way it proves
itself at connect time (see `channel_account_service.google_chat_mint_
access_token`): a JWT self-signed with the service account's private key,
exchanged for a bearer access token at Google's OAuth endpoint (RFC 7523's
JWT-bearer grant), scoped to `chat.bot` -- which is enough to post into any
space the app is already a member of, no domain-wide delegation needed.

The access token is minted fresh on every send rather than cached: it is
valid for an hour, but nothing here runs often enough for that saving to be
worth the failure mode of an expired token cached past its own lifetime.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/chat.bot"
API_BASE = "https://chat.googleapis.com/v1"
TIMEOUT_SECONDS = 15


def _mint_access_token(*, client_email: str, private_key: str) -> str | None:
    now = int(datetime.now(timezone.utc).timestamp())

    try:
        assertion = jwt.encode(
            {
                "iss": client_email,
                "scope": SCOPE,
                "aud": TOKEN_URI,
                "iat": now,
                "exp": now + 3600,
            },
            private_key,
            algorithm="RS256",
        )
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        logger.warning("Could not sign a Google Chat access-token request: %s", exc)
        return None

    try:
        response = httpx.post(
            TOKEN_URI,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Could not reach Google to mint a Chat access token: %s", exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "Google refused to mint a Chat access token: %s", response.status_code
        )
        return None

    try:
        return response.json().get("access_token") or None
    except ValueError:
        return None


def send_google_chat_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender returns.

    ``recipient_id`` is a Chat space's own resource name (``spaces/AAAA...``),
    not a person -- the space *is* the conversation, the way Slack's channel
    id or LINE's ``userId`` addresses one. ``buttons``, when given, are
    appended as plain text: Chat's richer Cards feature is a bigger surface
    than a bullet list, matching the same fallback Slack/Viber/LINE's
    senders already use rather than build it for one channel first.
    """
    try:
        account = resolve(int(company_id), "google_chat")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send Google Chat for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    client_email = account.get("external_account_id")
    private_key = account.get("access_token")

    if not client_email or not private_key:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected Google Chat account is missing its service account key.",
        }

    access_token = _mint_access_token(client_email=client_email, private_key=private_key)

    if not access_token:
        return {
            "ok": False,
            "skipped": False,
            "error": "Could not authenticate with Google Chat.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"- {button}" for button in buttons)

    try:
        response = httpx.post(
            f"{API_BASE}/{recipient_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"text": body_text},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Google Chat send failed for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 300:
        error_message = (body.get("error") or {}).get("message")
        logger.warning(
            "Google Chat rejected a message for company %s: %s %s",
            company_id,
            response.status_code,
            error_message,
        )
        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": error_message or "Google Chat rejected the message.",
        }

    return {
        "ok": True,
        "skipped": False,
        "status_code": response.status_code,
        "response": {"message_id": body.get("name")},
    }
