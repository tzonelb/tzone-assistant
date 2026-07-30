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


def _post_message_with_tag_fallback(url: str, params: dict, payload: dict, recipient_id: str) -> tuple:
    """Posts the message. If a HUMAN_AGENT tag was included and Meta
    rejects it specifically because this app isn't approved to use it
    yet (error code 100, 'without prior approval'), retries once
    without the tag instead of hard-failing — this can't recover a
    message genuinely outside the 24h window, but avoids a confusing
    double-error and still sends when the window is actually open.

    Checks the error content directly rather than relying on the HTTP
    status code — Meta has been observed returning this specific
    error with a 200 status, embedding the error only in the body."""
    response = requests.post(url, params=params, json=payload, timeout=15)
    response_data = response.json() if response.content else {}

    error_obj = response_data.get("error", {}) if isinstance(response_data, dict) else {}
    error_message = str(error_obj.get("message", ""))
    has_error = bool(error_obj)
    if has_error and "tag" in payload and "without prior approval" in error_message.lower():
        fallback_payload = {k: v for k, v in payload.items() if k not in ("messaging_type", "tag")}
        log_meta_event("human_agent_tag_not_approved_retrying_without_tag", {
            "recipient_id": recipient_id, "original_error": error_message,
        })
        response = requests.post(url, params=params, json=fallback_payload, timeout=15)
        response_data = response.json() if response.content else {}

    return response, response_data


def send_meta_text(
    recipient_id: str,
    text: str,
    channel: str = "messenger",
    company_id: int | None = None,
    is_human_agent: bool = False,
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
    # Meta's 24-hour messaging window only allows standard replies.
    # When a real human employee is actively handling this
    # conversation (not the AI), Messenger's HUMAN_AGENT tag extends
    # that to 7 days from the customer's last message — this is
    # exactly the scenario it's meant for. Only valid on Messenger,
    # not Instagram/WhatsApp (different windowing rules there).
    if is_human_agent and channel == "messenger":
        payload["messaging_type"] = "MESSAGE_TAG"
        payload["tag"] = "HUMAN_AGENT"

    params = {
        "access_token": access_token,
    }

    try:
        response, response_data = _post_message_with_tag_fallback(url, params, payload, recipient_id)

        result = {
            "ok": response.ok,
            "status_code": response.status_code,
            "channel": channel,
            "recipient_id": recipient_id,
            "response": response_data,
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
    is_human_agent: bool = False,
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
            is_human_agent=is_human_agent,
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
    if is_human_agent and channel == "messenger":
        payload["messaging_type"] = "MESSAGE_TAG"
        payload["tag"] = "HUMAN_AGENT"

    params = {
        "access_token": access_token,
    }

    try:
        response, response_data = _post_message_with_tag_fallback(url, params, payload, recipient_id)

        result = {
            "ok": response.ok,
            "status_code": response.status_code,
            "channel": channel,
            "recipient_id": recipient_id,
            "response": response_data,
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
