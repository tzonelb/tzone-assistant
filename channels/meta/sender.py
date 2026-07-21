import requests

from config.settings import config
from channels.meta.logger import log_meta_event


FAKE_TEST_IDS = {
    "123",
    "1234",
    "12345",
    "123456",
    "123456789",
    "987654321",
}


def is_fake_meta_id(recipient_id: str) -> bool:
    return str(recipient_id) in FAKE_TEST_IDS


def send_meta_text(
    recipient_id: str,
    text: str,
    channel: str = "messenger",
) -> dict:
    if is_fake_meta_id(recipient_id):
        result = {
            "ok": False,
            "skipped": True,
            "reason": "fake_test_id",
            "recipient_id": recipient_id,
            "channel": channel,
        }
        log_meta_event("send_skipped", result)
        return result

    if not config.META_PAGE_ACCESS_TOKEN:
        result = {
            "ok": False,
            "error": "META_PAGE_ACCESS_TOKEN is missing",
        }
        log_meta_event("send_failed", result)
        return result

    url = f"https://graph.facebook.com/{config.META_API_VERSION}/me/messages"

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }

    params = {
        "access_token": config.META_PAGE_ACCESS_TOKEN,
    }

    try:
        response = requests.post(url, params=params, json=payload, timeout=15)

        result = {
            "ok": response.ok,
            "status_code": response.status_code,
            "channel": channel,
            "recipient_id": recipient_id,
            "response": response.json() if response.content else {},
        }

        log_meta_event("send_result", result)
        return result

    except Exception as e:
        result = {
            "ok": False,
            "channel": channel,
            "recipient_id": recipient_id,
            "error": str(e),
        }
        log_meta_event("send_error", result)
        return result


def send_meta_buttons(
    recipient_id: str,
    text: str,
    buttons: list | None = None,
    channel: str = "messenger",
) -> dict:
    if is_fake_meta_id(recipient_id):
        result = {
            "ok": False,
            "skipped": True,
            "reason": "fake_test_id",
            "recipient_id": recipient_id,
            "channel": channel,
        }
        log_meta_event("send_skipped", result)
        return result

    if not buttons:
        return send_meta_text(
            recipient_id=recipient_id,
            text=text,
            channel=channel,
        )

    quick_replies = []

    for button in buttons[:13]:
        title = str(button)
        quick_replies.append({
            "content_type": "text",
            "title": title[:20],
            "payload": title,
        })

    url = f"https://graph.facebook.com/{config.META_API_VERSION}/me/messages"

    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": text,
            "quick_replies": quick_replies,
        },
    }

    params = {
        "access_token": config.META_PAGE_ACCESS_TOKEN,
    }

    try:
        response = requests.post(url, params=params, json=payload, timeout=15)

        result = {
            "ok": response.ok,
            "status_code": response.status_code,
            "channel": channel,
            "recipient_id": recipient_id,
            "response": response.json() if response.content else {},
        }

        log_meta_event("send_result", result)
        return result

    except Exception as e:
        result = {
            "ok": False,
            "channel": channel,
            "recipient_id": recipient_id,
            "error": str(e),
        }
        log_meta_event("send_error", result)
        return result