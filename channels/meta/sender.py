import logging

import requests

from config.settings import config
from channels.meta.logger import log_meta_event
from backend.services.token_crypto import decrypt_token
from database.database import db


logger = logging.getLogger(__name__)


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


def _resolve_access_token(company_id: int | None, channel: str) -> str:
    """Resolve the Graph API access token to use for a send.

    When company_id is provided, looks up an active channel_accounts row
    for (company_id, channel) and decrypts its stored token. Falls back
    to the legacy single-tenant config.META_PAGE_ACCESS_TOKEN when
    company_id is None, no matching row exists, or decryption fails --
    preserving 100% backward compatibility for existing callers that
    don't pass company_id.
    """
    if company_id is None:
        return config.META_PAGE_ACCESS_TOKEN

    try:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT access_token_encrypted
                FROM channel_accounts
                WHERE company_id = ? AND channel = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (company_id, channel),
            ).fetchone()
    except Exception:
        logger.exception(
            "send_meta: failed to look up channel_accounts token for company_id=%s channel=%s",
            company_id,
            channel,
        )
        return config.META_PAGE_ACCESS_TOKEN

    if not row or not row["access_token_encrypted"]:
        return config.META_PAGE_ACCESS_TOKEN

    decrypted = decrypt_token(row["access_token_encrypted"])
    return decrypted or config.META_PAGE_ACCESS_TOKEN


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

    access_token = _resolve_access_token(company_id, channel)

    if not access_token:
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

    quick_replies = []

    for button in buttons[:13]:
        title = str(button)
        quick_replies.append({
            "content_type": "text",
            "title": title[:20],
            "payload": title,
        })

    access_token = _resolve_access_token(company_id, channel)

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