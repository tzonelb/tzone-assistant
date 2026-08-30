"""One place to send an outbound message, whatever the channel.

Messenger, Instagram and WhatsApp each have their own API shape. Without a
dispatcher the dashboard ends up supporting whichever channels someone
remembered to wire up — which is how WhatsApp customers ended up unable to
receive a human reply at all, despite the sending code existing.
"""

from __future__ import annotations

import logging
from typing import Any

from channels.meta.sender import send_meta_buttons, send_meta_media, send_meta_text
from channels.telegram.sender import send_telegram_media, send_telegram_text
from channels.whatsapp.sender import send_whatsapp_media, send_whatsapp_text


logger = logging.getLogger(__name__)

META_CHANNELS = frozenset({"messenger", "instagram"})
WHATSAPP_CHANNELS = frozenset({"whatsapp"})
TELEGRAM_CHANNELS = frozenset({"telegram"})

SUPPORTED_CHANNELS = META_CHANNELS | WHATSAPP_CHANNELS | TELEGRAM_CHANNELS


class UnsupportedChannel(ValueError):
    """The channel has no configured way to send messages."""


def normalize_channel(channel: str) -> str:
    return str(channel or "").strip().lower()


def send_text(
    *,
    channel: str,
    recipient_id: str,
    company_id: int,
    text: str,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return a normalised result.

    Every backend returns ``ok`` plus provider detail, so callers do not need to
    know which API answered.
    """
    normalized = normalize_channel(channel)

    if normalized in META_CHANNELS:
        if buttons:
            return send_meta_buttons(
                recipient_id=recipient_id,
                text=text,
                company_id=company_id,
                buttons=buttons,
                channel=normalized,
            )

        return send_meta_text(
            recipient_id=recipient_id,
            text=text,
            company_id=company_id,
            channel=normalized,
        )

    if normalized in WHATSAPP_CHANNELS:
        result = send_whatsapp_text(
            to=recipient_id,
            text=text,
            company_id=company_id,
            buttons=buttons,
        )
        # WhatsApp's helper reports `sent`; normalise it so callers can rely on
        # one field across channels.
        return {
            "ok": bool(result.get("sent")),
            "channel": normalized,
            "recipient_id": recipient_id,
            **result,
        }

    if normalized in TELEGRAM_CHANNELS:
        # Telegram was reachable only through a standalone polling script that
        # spoke to the engine directly and had no dispatcher entry at all — so
        # an employee's manual reply, a scheduled message and the takeover
        # handback all had nowhere to go on this channel.
        return {
            "channel": normalized,
            "recipient_id": recipient_id,
            **send_telegram_text(
                recipient_id=recipient_id,
                text=text,
                company_id=company_id,
                buttons=buttons,
            ),
        }

    raise UnsupportedChannel(
        f"Channel '{channel}' cannot send messages. "
        f"Supported: {', '.join(sorted(SUPPORTED_CHANNELS))}."
    )


def send_media(
    *,
    channel: str,
    recipient_id: str,
    company_id: int,
    media_url: str,
    media_type: str,
    caption: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Send one attachment and return the same normalised result as `send_text`.

    Every provider fetches the URL itself rather than accepting bytes, so
    `media_url` has to be publicly reachable -- which is why the route that
    serves a stored file carries no session guard.

    Where the caption goes differs by provider and is left to each sender:
    Messenger has no text field on an attachment and sends it as a second
    message, WhatsApp carries it inside the media object for the kinds that can
    show one, and Telegram takes it as a parameter.
    """
    normalized = normalize_channel(channel)

    if normalized in META_CHANNELS:
        return send_meta_media(
            recipient_id=recipient_id,
            media_url=media_url,
            media_type=media_type,
            company_id=company_id,
            channel=normalized,
            caption=caption,
        )

    if normalized in WHATSAPP_CHANNELS:
        result = send_whatsapp_media(
            to=recipient_id,
            media_url=media_url,
            media_type=media_type,
            company_id=company_id,
            caption=caption,
            filename=filename,
        )
        # Same normalisation as send_text: WhatsApp's helper reports `sent`.
        return {
            "ok": bool(result.get("sent")),
            "channel": normalized,
            "recipient_id": recipient_id,
            **result,
        }

    if normalized in TELEGRAM_CHANNELS:
        return {
            "channel": normalized,
            "recipient_id": recipient_id,
            **send_telegram_media(
                recipient_id=recipient_id,
                media_url=media_url,
                media_type=media_type,
                company_id=company_id,
                caption=caption,
            ),
        }

    raise UnsupportedChannel(
        f"Channel '{channel}' cannot send attachments. "
        f"Supported: {', '.join(sorted(SUPPORTED_CHANNELS))}."
    )


def extract_error(send_result: dict[str, Any]) -> str:
    """Pull a human-readable reason out of a provider failure."""
    response_data = send_result.get("response")

    if isinstance(response_data, dict):
        provider_error = response_data.get("error")

        if isinstance(provider_error, dict) and provider_error.get("message"):
            return str(provider_error["message"])

    return str(
        send_result.get("error")
        or send_result.get("reason")
        or send_result.get("status_code")
        or "The messaging provider rejected the message."
    )
