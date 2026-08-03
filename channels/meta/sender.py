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


def _resolve_access_token(channel: str, company_id: int | None) -> str | None:
    """Pick which Page/IG access token to send a reply through.

    When `company_id` is set, try the per-company token connected via the
    OAuth flow (channel_accounts.access_token_encrypted, decrypted through
    backend.services.token_crypto). Any failure — no company_id, no matching
    active channel account, no decrypt helper available yet, a bad token —
    falls straight back to the single, global META_PAGE_ACCESS_TOKEN so the
    current default-company flow is always preserved byte-for-byte.
    """
    if company_id is not None:
        try:
            from backend.services.channel_account_service import (
                channel_account_service,
            )
            from backend.services.token_crypto import decrypt_token

            account = channel_account_service.get_active_account(
                company_id=company_id,
                channel=channel,
            )
            encrypted_token = (account or {}).get("access_token_encrypted")

            if encrypted_token:
                decrypted = decrypt_token(encrypted_token)
                if decrypted:
                    return decrypted
        except ImportError:
            # token_crypto / channel_account_service not wired up yet.
            pass
        except Exception as exc:  # noqa: BLE001
            log_meta_event(
                "company_token_resolution_failed",
                {"company_id": company_id, "channel": channel, "error": str(exc)},
            )

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

    access_token = _resolve_access_token(channel, company_id)

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

    url = f"https://graph.facebook.com/{config.META_API_VERSION}/me/messages"

    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": text,
            "quick_replies": quick_replies,
        },
    }

    access_token = _resolve_access_token(channel, company_id)

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