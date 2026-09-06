"""Sending a message through one company's own Telegram bot.

Telegram's send API is a plain HTTPS POST to
``https://api.telegram.org/bot<token>/sendMessage``, so this needs no library —
`httpx` is already a dependency and `python-telegram-bot` is only needed by the
polling script.

The token comes from the company's connected account, never from the
environment. `TELEGRAM_BOT_TOKEN` in `.env` is what made this single-company: a
platform serving a thousand businesses cannot answer them all from one bot, and
a shared token would reply to one company's customer from another company's bot.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve


logger = logging.getLogger(__name__)


API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 15


def _keyboard(buttons: list[str] | None) -> dict[str, Any] | None:
    """Telegram's reply keyboard, or nothing.

    One button per row: the labels a company writes are its own department
    names, which are far longer than the two or three words that fit side by
    side on a phone.
    """
    if not buttons:
        return None

    return {
        "keyboard": [[{"text": str(button)}] for button in buttons],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


# The Bot API method and the field each kind of file travels in. Taken from the
# design branch's sender so a file sends exactly as it did there.
_TELEGRAM_MEDIA_METHOD = {
    "image": "sendPhoto",
    "video": "sendVideo",
    "audio": "sendAudio",
    "document": "sendDocument",
}
_TELEGRAM_MEDIA_FIELD = {
    "image": "photo",
    "video": "video",
    "audio": "audio",
    "document": "document",
}


def send_telegram_media(
    *,
    recipient_id: str,
    media_url: str,
    media_type: str,
    company_id: int,
    caption: str | None = None,
) -> dict[str, Any]:
    """Send a stored file by URL.

    Telegram fetches the URL itself, so it has to be publicly reachable.

    The bot token comes from the company's own connected account rather than a
    platform-wide setting: on this branch each company connects its own bot, so
    a shared token would send one company's file from another company's bot.
    """
    method = _TELEGRAM_MEDIA_METHOD.get(media_type)
    field = _TELEGRAM_MEDIA_FIELD.get(media_type)

    if not method:
        return {
            "ok": False,
            "skipped": False,
            "error": f"Telegram cannot carry a {media_type or 'file'} attachment.",
        }

    if not str(media_url or "").lower().startswith(("http://", "https://")):
        return {
            "ok": False,
            "skipped": False,
            "error": (
                "An attachment needs a URL Telegram can fetch. Set "
                "APP_PUBLIC_URL to this platform's public address."
            ),
        }

    try:
        account = resolve(int(company_id), "telegram")
    except MissingChannelCredentials as exc:
        logger.warning(
            "Cannot send media to Telegram for company %s: %s", company_id, exc
        )
        return {"ok": False, "skipped": False, "error": str(exc)}

    token = account.get("access_token")

    if not token:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected Telegram account has no bot token.",
        }

    payload: dict[str, Any] = {"chat_id": str(recipient_id), field: media_url}

    if caption:
        payload["caption"] = caption

    try:
        response = httpx.post(
            f"{API_BASE}/bot{token}/{method}",
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Telegram media send failed for company %s: %s", company_id, exc
        )
        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400 or not body.get("ok"):
        # The description, never the token -- it is in the URL this just called.
        logger.warning(
            "Telegram rejected an attachment for company %s: %s %s",
            company_id,
            response.status_code,
            body.get("description"),
        )

        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": body.get("description") or "Telegram rejected the attachment.",
        }

    return {"ok": True, "skipped": False, "response": body}


def send_telegram_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    """Send one message and return the same shape every other sender returns.

    Never raises. The dispatcher's contract is a result dict, and a sender that
    threw would take down the batch a customer is waiting in.
    """
    try:
        account = resolve(int(company_id), "telegram")
    except MissingChannelCredentials as exc:
        logger.warning("Cannot send to Telegram for company %s: %s", company_id, exc)

        return {"ok": False, "skipped": False, "error": str(exc)}

    token = account.get("access_token")

    if not token:
        return {
            "ok": False,
            "skipped": False,
            "error": "The connected Telegram account has no bot token.",
        }

    payload: dict[str, Any] = {"chat_id": str(recipient_id), "text": text}
    keyboard = _keyboard(buttons)

    if keyboard:
        payload["reply_markup"] = keyboard

    try:
        response = httpx.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Telegram send failed for company %s: %s", company_id, exc)

        return {"ok": False, "skipped": False, "error": type(exc).__name__}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400 or not body.get("ok"):
        # The description, never the token — it is in the URL this just called.
        logger.warning(
            "Telegram rejected a message for company %s: %s %s",
            company_id,
            response.status_code,
            body.get("description"),
        )

        return {
            "ok": False,
            "skipped": False,
            "status_code": response.status_code,
            "error": body.get("description") or "Telegram rejected the message.",
        }

    result = body.get("result") or {}

    return {
        "ok": True,
        "skipped": False,
        "status_code": response.status_code,
        "response": {"message_id": str(result.get("message_id") or "") or None},
    }
