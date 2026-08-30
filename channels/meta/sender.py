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


# Meta names four attachment kinds. Taken from the design branch's sender
# (`_META_ATTACHMENT_TYPE` there) so a file sends exactly as it did in the
# interface this design came from: anything this platform stores as a document
# goes as "file".
_META_ATTACHMENT_TYPE = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "document": "file",
}


def send_meta_media(
    recipient_id: str,
    media_url: str,
    media_type: str,
    company_id: int,
    channel: str = "messenger",
    caption: str | None = None,
) -> dict[str, Any]:
    """Send a stored file by URL.

    Meta fetches the URL itself rather than accepting bytes, which is why the
    media read route is served without a session. The URL has to be one the
    public internet can reach: a link to 127.0.0.1 is accepted by Meta and
    silently delivers nothing, so an absolute URL is required rather than
    guessed at here.

    The caption is a second message, not a field. Messenger's attachment payload
    carries no text, so folding a caption into it would drop it -- the design
    branch sends it afterwards for the same reason, and sending it after means a
    failed caption never costs the attachment.
    """
    if is_fake_meta_id(recipient_id):
        return _skip_fake(recipient_id, channel)

    attachment_type = _META_ATTACHMENT_TYPE.get(media_type)

    if not attachment_type:
        return {
            "ok": False,
            "channel": channel,
            "recipient_id": recipient_id,
            "error": f"Meta cannot carry a {media_type or 'file'} attachment.",
        }

    if not str(media_url or "").lower().startswith(("http://", "https://")):
        return {
            "ok": False,
            "channel": channel,
            "recipient_id": recipient_id,
            "error": (
                "An attachment needs a URL the channel can fetch. Set "
                "APP_PUBLIC_URL to this platform's public address."
            ),
        }

    try:
        credentials = resolve(company_id, channel)
    except MissingChannelCredentials as exc:
        return _no_credentials(exc, recipient_id, channel)

    result = _post(
        access_token=credentials["access_token"],
        payload={
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": attachment_type,
                    "payload": {"url": media_url, "is_reusable": True},
                }
            },
        },
        recipient_id=recipient_id,
        channel=channel,
    )

    if result.get("ok") and caption:
        send_meta_text(recipient_id, caption, company_id, channel=channel)

    return result


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
