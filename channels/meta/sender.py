"""Sending on Messenger and Instagram.

Every send resolves the calling company's own page token first. There is no
module-level token, because a shared one would answer one company's customer
from another company's page.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve
from channels.meta.logger import log_meta_event
from config.settings import config


logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 15
MAX_QUICK_REPLIES = 13
QUICK_REPLY_TITLE_LIMIT = 20

# Meta's own documentation examples use these ids. A webhook test fires with
# them, and sending to them produces a confusing provider error rather than a
# delivery, so they are skipped deliberately.
FAKE_TEST_IDS = frozenset(
    {"123", "1234", "12345", "123456", "123456789", "987654321"}
)


def is_fake_meta_id(recipient_id: str) -> bool:
    return str(recipient_id) in FAKE_TEST_IDS


def _post(
    *,
    access_token: str,
    payload: dict[str, Any],
    recipient_id: str,
    channel: str,
) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{config.META_API_VERSION}/me/messages"

    try:
        response = httpx.post(
            url,
            params={"access_token": access_token},
            json=payload,
            timeout=SEND_TIMEOUT_SECONDS,
        )

        result = {
            "ok": response.is_success,
            "status_code": response.status_code,
            "channel": channel,
            "recipient_id": recipient_id,
            "response": response.json() if response.content else {},
        }

    except httpx.HTTPError as exc:
        result = {
            "ok": False,
            "channel": channel,
            "recipient_id": recipient_id,
            "error": str(exc),
        }

    log_meta_event("send_result", result)
    return result


def _skip_fake(recipient_id: str, channel: str) -> dict[str, Any]:
    result = {
        "ok": False,
        "skipped": True,
        "reason": "fake_test_id",
        "recipient_id": recipient_id,
        "channel": channel,
    }
    log_meta_event("send_skipped", result)
    return result


def _no_credentials(exc: Exception, recipient_id: str, channel: str) -> dict[str, Any]:
    result = {
        "ok": False,
        "channel": channel,
        "recipient_id": recipient_id,
        "error": str(exc),
        "reason": "missing_credentials",
    }
    log_meta_event("send_failed", {"channel": channel, "reason": "missing_credentials"})
    logger.error("Cannot send on %s: %s", channel, exc)
    return result


def send_meta_text(
    recipient_id: str,
    text: str,
    company_id: int,
    channel: str = "messenger",
) -> dict[str, Any]:
    if is_fake_meta_id(recipient_id):
        return _skip_fake(recipient_id, channel)

    try:
        credentials = resolve(company_id, channel)
    except MissingChannelCredentials as exc:
        return _no_credentials(exc, recipient_id, channel)

    return _post(
        access_token=credentials["access_token"],
        payload={"recipient": {"id": recipient_id}, "message": {"text": text}},
        recipient_id=recipient_id,
        channel=channel,
    )


def send_meta_buttons(
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list | None = None,
    channel: str = "messenger",
) -> dict[str, Any]:
    if is_fake_meta_id(recipient_id):
        return _skip_fake(recipient_id, channel)

    if not buttons:
        return send_meta_text(
            recipient_id=recipient_id,
            text=text,
            company_id=company_id,
            channel=channel,
        )

    try:
        credentials = resolve(company_id, channel)
    except MissingChannelCredentials as exc:
        return _no_credentials(exc, recipient_id, channel)

    quick_replies = [
        {
            "content_type": "text",
            "title": str(button)[:QUICK_REPLY_TITLE_LIMIT],
            "payload": str(button),
        }
        for button in buttons[:MAX_QUICK_REPLIES]
    ]

    return _post(
        access_token=credentials["access_token"],
        payload={
            "recipient": {"id": recipient_id},
            "message": {"text": text, "quick_replies": quick_replies},
        },
        recipient_id=recipient_id,
        channel=channel,
    )
