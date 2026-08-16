"""Sending on WhatsApp Cloud API.

Both the access token and the phone number id come from the company's own
connected account, so two companies on this server send from their own numbers.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve
from config.settings import config


logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 20


def format_whatsapp_message(text: str, buttons: list | None = None) -> str:
    """Render quick-reply options as a numbered list.

    WhatsApp interactive replies need a template approval the platform does not
    assume, so options are offered as numbers the customer can type back.
    """
    if not buttons:
        return text

    lines = [text, "", "اختر رقم من الخيارات:"]
    lines.extend(f"{index}. {button}" for index, button in enumerate(buttons, start=1))
    return "\n".join(lines)


def send_whatsapp_text(
    to: str,
    text: str,
    company_id: int,
    buttons: list | None = None,
) -> dict[str, Any]:
    try:
        credentials = resolve(company_id, "whatsapp")
    except MissingChannelCredentials as exc:
        logger.error("Cannot send on WhatsApp: %s", exc)
        return {
            "sent": False,
            "reason": "missing_credentials",
            "error": str(exc),
        }

    phone_number_id = credentials.get("phone_number_id")

    if not phone_number_id:
        logger.error(
            "WhatsApp account for company %s has no phone number id", company_id
        )
        return {"sent": False, "reason": "missing_phone_number_id"}

    url = (
        f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "text",
        "text": {"body": format_whatsapp_message(text, buttons)},
    }

    headers = {
        "Authorization": f"Bearer {credentials['access_token']}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.post(
            url, json=payload, headers=headers, timeout=SEND_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp send failed for company %s: %s", company_id, exc)
        return {"sent": False, "reason": "network_error", "error": str(exc)}

    if not response.is_success:
        # Status and provider payload only; the message body is customer content.
        logger.warning(
            "WhatsApp rejected a message for company %s with status %s",
            company_id,
            response.status_code,
        )

    return {
        "sent": response.is_success,
        "status_code": response.status_code,
        "response": response.json() if response.content else {},
    }
