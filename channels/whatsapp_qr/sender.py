"""Sending a message through one company's own WhatsApp (QR scan) session.

Same session-only rule as every unofficial channel here: this never
attempts to log in, only to resume the session `backend/api/routes/
whatsapp_qr.py` captured at connect time. A session that has gone stale
surfaces as a plain failure -- the account needs reconnecting, not a retry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from channels.credentials import MissingChannelCredentials, resolve
from channels.whatsapp_qr.browser import (
    WhatsAppSessionError,
    open_authenticated_page,
    send_text_message,
)


logger = logging.getLogger(__name__)


def send_whatsapp_qr_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender here
    returns. ``recipient_id`` is the WhatsApp id the message routes to --
    the digits before ``@`` in WhatsApp's own id, the same one
    `channels/whatsapp_qr/poller.py` hands `process_inbound_event` as the
    routing user id, so a reply always lands in the thread it answers.

    ``buttons``, when given, are appended as plain text -- WhatsApp Web's
    own compose box has no interactive-element concept this platform can
    drive through the browser, the same fallback every channel without one
    uses.
    """
    try:
        account = resolve(int(company_id), "whatsapp_qr")
    except MissingChannelCredentials as exc:
        logger.warning(
            "Cannot send WhatsApp (QR) for company %s: %s", company_id, exc
        )
        return {"ok": False, "skipped": False, "error": str(exc)}

    settings_json = account.get("access_token")

    if not settings_json:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected WhatsApp account is missing its session.",
        }

    try:
        storage_state = json.loads(settings_json)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected WhatsApp account's session is unusable.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"- {button}" for button in buttons)

    try:
        session = open_authenticated_page(storage_state)
    except WhatsAppSessionError as exc:
        return {"ok": False, "skipped": False, "error": str(exc)}

    try:
        sent = send_text_message(session, recipient_id, body_text)
    finally:
        session.close()

    if not sent:
        return {
            "ok": False,
            "skipped": False,
            "error": "The message could not be sent through WhatsApp Web.",
        }

    return {"ok": True, "skipped": False, "response": {}}
