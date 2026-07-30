import requests

from config.settings import config


def _reply_markup(buttons: list | None) -> dict | None:
    if not buttons:
        return None
    return {
        "keyboard": [[str(button)] for button in buttons],
        "resize_keyboard": True,
    }


def send_telegram_text(
    recipient_id: str,
    text: str,
    channel: str = "telegram",
    buttons: list | None = None,
) -> dict:
    """Send a text message (optionally with a reply keyboard) to a
    Telegram user via the Bot API.

    Mirrors channels.meta.sender.send_meta_text/send_meta_buttons's
    return shape ({"ok": bool, ...}) so callers can dispatch by channel
    without special-casing the response format.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        result = {
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN is missing",
            "channel": channel,
        }
        return result

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": recipient_id,
        "text": text,
    }
    reply_markup = _reply_markup(buttons)
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": str(exc),
            "channel": channel,
        }

    if not data.get("ok"):
        return {
            "ok": False,
            "error": data.get("description", "Telegram rejected the message."),
            "response": data,
            "channel": channel,
        }

    return {
        "ok": True,
        "response": data,
        "channel": channel,
    }


# Telegram's Bot API accepts a public URL directly in place of an
# uploaded file for sendPhoto/sendVideo/sendAudio — no multipart
# upload needed on our side. Method name and payload field both
# depend on media_type.
_TELEGRAM_MEDIA_METHOD = {"image": "sendPhoto", "video": "sendVideo", "audio": "sendAudio"}
_TELEGRAM_MEDIA_FIELD = {"image": "photo", "video": "video", "audio": "audio"}


def send_telegram_media(
    recipient_id: str,
    media_url: str,
    media_type: str,
    caption: str | None = None,
    channel: str = "telegram",
) -> dict:
    if not config.TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is missing", "channel": channel}

    method = _TELEGRAM_MEDIA_METHOD.get(media_type)
    field = _TELEGRAM_MEDIA_FIELD.get(media_type)
    if not method:
        return {"ok": False, "error": f"Unsupported media type: {media_type}", "channel": channel}

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    payload = {"chat_id": recipient_id, field: media_url}
    if caption:
        payload["caption"] = caption

    try:
        response = requests.post(url, json=payload, timeout=20)
        data = response.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "channel": channel}

    if not data.get("ok"):
        return {
            "ok": False,
            "error": data.get("description", "Telegram rejected the message."),
            "response": data,
            "channel": channel,
        }

    return {"ok": True, "response": data, "channel": channel}


def send_telegram_buttons(
    recipient_id: str,
    text: str,
    buttons: list | None = None,
    channel: str = "telegram",
) -> dict:
    """Alias matching channels.meta.sender.send_meta_buttons's call
    signature, so the smart_reply dispatch can call either sender the
    same way regardless of channel."""
    return send_telegram_text(
        recipient_id=recipient_id,
        text=text,
        channel=channel,
        buttons=buttons,
    )
