import json
import logging

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse

from config.settings import config
from channels.whatsapp.media import download_whatsapp_media
from channels.whatsapp.processor import process_whatsapp_message
from channels.whatsapp.sender import send_whatsapp_text
from backend.services.channel_account_service import channel_account_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.diagnostics_service import diagnostics_service
from core.stt_service import stt_service
from core.vision_service import vision_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp"])

ATTACHMENT_FALLBACK_TEXT = "Sorry, I couldn't understand that — could you type your message instead?"


def _resolve_media_company_id(phone_number_id: str) -> int | None:
    account_match = channel_account_service.resolve_meta_account(recipient_id=phone_number_id, channel="whatsapp")
    return account_match["company_id"] if account_match else None


def _notify_attachment_failure(user_id: str, msg_type: str, phone_number_id: str) -> None:
    """A voice note/image we couldn't transcribe or describe would
    otherwise vanish silently (customer sends it, nothing ever comes
    back). Records it for monitoring and lets the customer know to
    retry as text instead of being left hanging."""
    account_match = channel_account_service.resolve_meta_account(recipient_id=phone_number_id, channel="whatsapp")
    company_id = account_match["company_id"] if account_match else conversation_control_service.resolve_default_company_id()
    diagnostics_service.record(
        event_type="attachment_processing_failed",
        company_id=company_id,
        channel="whatsapp",
        external_user_id=user_id,
        severity="warning",
        status="failed",
        data={"attachment_type": msg_type},
    )
    if user_id:
        send_whatsapp_text(user_id, ATTACHMENT_FALLBACK_TEXT, company_id=company_id)


def _transcribe_whatsapp_voice_note(media_id: str, phone_number_id: str) -> str | None:
    """Downloads a customer's voice note and turns it into text so it
    flows through the exact same reply pipeline as a typed message.
    Returns None (never raises) on any failure — an unreadable voice
    note is skipped rather than crashing the webhook."""
    downloaded = download_whatsapp_media(media_id, _resolve_media_company_id(phone_number_id))
    if not downloaded:
        return None
    audio_bytes, _mime_type = downloaded
    try:
        return stt_service.transcribe(audio_bytes, filename="voice.ogg")
    except Exception:
        logger.exception("WhatsApp voice note transcription failed")
        return None


def _describe_whatsapp_image(media_id: str, phone_number_id: str, caption: str) -> str | None:
    """Downloads a customer's image and turns it into a text description
    so the AI can 'read' it through the same reply pipeline as a typed
    message. Returns None (never raises) on any failure."""
    downloaded = download_whatsapp_media(media_id, _resolve_media_company_id(phone_number_id))
    if not downloaded:
        return None
    image_bytes, mime_type = downloaded
    try:
        description = vision_service.describe_image(image_bytes, mime_type=mime_type or "image/jpeg")
    except Exception:
        logger.exception("WhatsApp image description failed")
        return None
    text = f"[Customer sent an image — what's in it: {description}]"
    return f"{text}\nCustomer's caption: {caption}" if caption else text


@router.get("/")
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "")

    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/")
async def receive_message(request: Request):
    from channels.common.rate_limiter import (
        get_client_ip,
        whatsapp_webhook_rate_limiter,
    )
    from channels.meta.webhook import enforce_webhook_security

    # Defense-in-depth rate limit on the real socket peer; the primary
    # defense is the HMAC signature check right after (WhatsApp Cloud API
    # signs with the same Meta app secret / X-Hub-Signature-256 scheme).
    if not whatsapp_webhook_rate_limiter.allow(get_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.body()
    enforce_webhook_security(
        body, request.headers.get("x-hub-signature-256"), "whatsapp"
    )

    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {"status": "ignored", "reason": "invalid_json"}

    print("WHATSAPP POST RECEIVED")
    print(data)

    try:
        entry = data.get("entry", [])
        if not entry:
            return {"status": "ignored", "reason": "no_entry"}

        change = entry[0].get("changes", [{}])[0]
        value = change.get("value", {})

        metadata = value.get("metadata", {})
        incoming_phone_number_id = str(metadata.get("phone_number_id", ""))

        # Status updates (sent/delivered/read) arrive in their own
        # array, separate from actual messages — record them and move
        # on; this never touches the message-receiving flow below.
        statuses = value.get("statuses", [])
        if statuses:
            from backend.services.message_status_service import message_status_service
            for status_event in statuses:
                provider_message_id = status_event.get("id")
                status_value = status_event.get("status")
                if provider_message_id and status_value in ("sent", "delivered", "read"):
                    message_status_service.update_status(
                        channel="whatsapp", provider_message_id=provider_message_id, status=status_value,
                    )
            if not value.get("messages"):
                return {"status": "ok", "reason": "status_update_only"}

        # Multi-tenant: does this phone_number_id belong to a company
        # that connected its own WhatsApp? If not, fall back to the
        # legacy single .env-configured number, and ignore anything
        # that matches neither (e.g. Meta's sample payloads with fake
        # ids like 123456123).
        account_match = channel_account_service.resolve_meta_account(
            recipient_id=incoming_phone_number_id, channel="whatsapp",
        )
        is_legacy_number = (
            incoming_phone_number_id == str(config.WHATSAPP_PHONE_NUMBER_ID)
            and incoming_phone_number_id != ""
        )
        if not account_match and not is_legacy_number:
            print("IGNORED SAMPLE OR UNKNOWN PHONE NUMBER ID:", incoming_phone_number_id)
            return {
                "status": "ignored",
                "reason": "unknown_phone_number_id",
                "incoming_phone_number_id": incoming_phone_number_id,
            }

        messages = value.get("messages", [])
        if not messages:
            return {"status": "ignored", "reason": "no_messages"}

        msg = messages[0]
        msg_type = msg.get("type")
        user_id = msg.get("from")

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "").strip()
        elif msg_type == "audio":
            media_id = msg.get("audio", {}).get("id")
            text = _transcribe_whatsapp_voice_note(media_id, incoming_phone_number_id) if media_id else None
        elif msg_type == "image":
            media_id = msg.get("image", {}).get("id")
            caption = msg.get("image", {}).get("caption", "")
            text = _describe_whatsapp_image(media_id, incoming_phone_number_id, caption) if media_id else None
        else:
            return {"status": "ignored", "reason": "unsupported_message_type"}

        if not user_id or not text:
            if user_id and msg_type in ("audio", "image"):
                _notify_attachment_failure(user_id, msg_type, incoming_phone_number_id)
            return {"status": "unsupported"}

        contacts = value.get("contacts", [])
        customer_name = contacts[0].get("profile", {}).get("name") if contacts else None

        result = process_whatsapp_message(
            user_id=user_id,
            text=text,
            recipient_phone_number_id=incoming_phone_number_id,
            customer_name=customer_name,
            source_type=msg_type,
        )

        return {
            "status": "received",
            "from": user_id,
            "text": text,
            "company_id": result["company_id"],
            "queued": result["queue_result"].get("queued"),
        }

    except Exception as e:
        print("WHATSAPP WEBHOOK ERROR:", str(e))
        return {
            "status": "error",
            "detail": str(e),
        }
