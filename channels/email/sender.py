"""Sending a reply through one company's own mailbox, over SMTP.

Every other channel's sender makes one call to one provider API. Email splits
across two protocols on two servers -- IMAP for reading, polled in
`channels/email/poller.py`, and SMTP for sending, here. Neither touches
`backend/services/mailer.py`: that module sends this platform's own system
mail (password resets, channel-verification codes) from one address it
controls. This sends a company's own support reply from that company's own
mailbox with that company's own SMTP credentials -- a different job, even
though both end in an SMTP call.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from backend.services.message_service import message_service
from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 15


def _reply_context(company_id: int, recipient_id: str) -> dict[str, Any]:
    """The most recent inbound message from this address, for threading.

    A reply sent with no `In-Reply-To` and a generic subject still arrives,
    but it starts a new thread in the customer's own inbox instead of landing
    in the one they wrote from -- worth the one extra read this costs.
    """
    messages = message_service.list_messages(
        company_id=company_id,
        channel="email",
        external_user_id=recipient_id,
        limit=20,
    )

    for message in reversed(messages):
        if message.get("direction") == "in":
            metadata = message.get("metadata") or {}
            return {
                "message_id": message.get("provider_message_id"),
                "subject": metadata.get("email_subject") or "",
            }

    return {"message_id": None, "subject": ""}


def send_email_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender returns.

    Never raises. ``buttons``, when given, are appended as plain text rather
    than dropped -- the same choice `send_slack_text` makes, for the same
    reason: there is no quick-reply equivalent to send them as here either.
    """
    try:
        account = resolve(int(company_id), "email")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send email for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    address = account.get("external_account_id")
    password = account.get("access_token")
    config = account.get("config") or {}

    if not address or not password or not config.get("smtp_host"):
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected email account has no SMTP settings.",
        }

    body_text = text

    if buttons:
        body_text = text + "\n\n" + "\n".join(f"- {button}" for button in buttons)

    reply_to = _reply_context(int(company_id), str(recipient_id))

    message = EmailMessage()
    message["From"] = address
    message["To"] = str(recipient_id)
    message["Subject"] = (
        f"Re: {reply_to['subject']}" if reply_to["subject"] else "Re: your message"
    )

    if reply_to["message_id"]:
        message["In-Reply-To"] = reply_to["message_id"]
        message["References"] = reply_to["message_id"]

    message.set_content(body_text)

    host = config.get("smtp_host")
    port = int(config.get("smtp_port") or 587)
    use_starttls = bool(config.get("smtp_use_starttls", True))

    try:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if use_starttls:
                smtp.starttls(context=ssl.create_default_context())

            smtp.login(address, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        logger.warning("Email send failed for company %s: %s", company_id, exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    return {
        "ok": True,
        "skipped": False,
        "response": {"message_id": message.get("Message-Id")},
    }
