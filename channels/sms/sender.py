"""Sending a message through one company's own Twilio phone number.

Twilio's Messages resource takes `application/x-www-form-urlencoded`, not
JSON -- the one sender here that does not post a JSON body -- and
authenticates with HTTP Basic Auth using the Account SID as the username and
the Auth Token as the password, rather than a bearer header.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)


API_BASE = "https://api.twilio.com/2010-04-01"
TIMEOUT_SECONDS = 15


def send_sms_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender
    returns.

    Never raises. ``buttons``, when given, are appended as plain text --
    SMS has no interactive-element concept at all, the same as it has no
    read receipt or typing indicator, so there is no richer form to fall
    short of here.
    """
    try:
        account = resolve(int(company_id), "sms")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send SMS for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    auth_token = account.get("access_token")
    from_number = account.get("external_account_id")
    account_sid = (account.get("config") or {}).get("account_sid")

    if not auth_token or not from_number or not account_sid:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected SMS account is missing its Twilio credentials.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"- {button}" for button in buttons)

    try:
        response = httpx.post(
            f"{API_BASE}/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "To": str(recipient_id),
                "From": from_number,
                "Body": body_text,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("SMS send failed for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 300:
        logger.warning(
            "Twilio rejected a message for company %s: %s %s",
            company_id,
            response.status_code,
            body.get("message"),
        )

        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": body.get("message") or "Twilio rejected the message.",
        }

    return {
        "ok": True,
        "skipped": False,
        "status_code": response.status_code,
        "response": {"message_id": body.get("sid")},
    }
