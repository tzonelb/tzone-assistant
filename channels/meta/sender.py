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


def _resolve_token(channel: str, company_id: int | None) -> str | None:
    """Per-company token if this company has connected its own account
    for this channel, otherwise fall back to the platform-wide .env
    token (keeps existing single-tenant setups working unchanged)."""
    if company_id is not None:
        from backend.services.channel_account_service import channel_account_service
        token = channel_account_service.get_active_token(company_id=company_id, channel=channel)
        if token:
            return token
    return config.META_PAGE_ACCESS_TOKEN


def send_meta_text(
    recipient_id: str,
    text: str,
    channel: str = "messenger",
    company_id: int | None = None,
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

    access_token = _resolve_token(channel, company_id)
    if not access_token:
        result = {
            "ok": False,
            "error": "No access token available for this channel (not connected, and META_PAGE_ACCESS_TOKEN is missing)",
        }
        log_meta_event("send_failed", result)
        return result

    url = f"https://graph.facebook.com/{config.META_API_VERSION}/me/messages"

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }

    params = {
        "access_token": access_token,
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
    company_id: int | None = None,
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
            company_id=company_id,
        )

    access_token = _resolve_token(channel, company_id)
    if not access_token:
        result = {
            "ok": False,
            "error": "No access token available for this channel (not connected, and META_PAGE_ACCESS_TOKEN is missing)",
        }
        log_meta_event("send_failed", result)
        return result

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
        "access_token": access_token,
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
