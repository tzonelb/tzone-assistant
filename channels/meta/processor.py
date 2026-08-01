import logging

from backend.services.channel_account_service import channel_account_service
from backend.services.company_settings_service import company_settings_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.customer_service import customer_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.notification_service import notification_service
from channels.meta.logger import log_meta_event
from channels.meta.media import download_meta_attachment
from channels.meta.parser import parse_meta_text_message
from channels.meta.profile import resolve_meta_profile
from channels.meta.sender import send_meta_text
from channels.meta.smart_reply import schedule_smart_reply
from core.conversation_store import save_conversation_message
from core.stt_service import stt_service
from core.vision_service import vision_service

logger = logging.getLogger(__name__)

ATTACHMENT_FALLBACK_TEXT = "Sorry, I couldn't understand that — could you type your message instead?"


def _resolve_attachment_text(attachment_type: str, attachment_url: str) -> str | None:
    """Downloads a customer's voice note or image and turns it into text
    so it flows through the exact same reply pipeline as a typed message.
    Returns None (never raises) on any failure — an unreadable attachment
    is skipped rather than crashing the webhook."""
    content = download_meta_attachment(attachment_url)
    if not content:
        return None
    try:
        if attachment_type == "audio":
            return stt_service.transcribe(content, filename="voice.ogg")
        description = vision_service.describe_image(content, mime_type="image/jpeg")
        return f"[Customer sent an image — what's in it: {description}]"
    except Exception:
        logger.exception("Meta attachment transcription/description failed")
        return None


def process_meta_payload(payload: dict):
    parsed = parse_meta_text_message(payload)

    if not parsed:
        log_meta_event("ignored", {"reason": "invalid_payload", "payload": payload})
        return {"status": "ignored", "reason": "invalid_payload"}

    if parsed.get("ignored"):
        log_meta_event("ignored", parsed)
        return {
            "status": "ignored",
            "reason": parsed.get("reason"),
            "channel": parsed.get("channel"),
            "user_id": parsed.get("user_id"),
        }

    channel = parsed["channel"]
    user_id = parsed["user_id"]
    source_type = "text"
    text = parsed["text"]

    # Multi-tenant resolution: which company's connected page/IG account
    # received this? Falls back to the platform default company if no
    # company has connected one yet (keeps the old single-tenant .env
    # setup working unchanged).
    account_match = channel_account_service.resolve_meta_account(
        recipient_id=parsed.get("recipient_id"), channel=channel,
    )
    company_id = (
        account_match["company_id"] if account_match
        else conversation_control_service.resolve_default_company_id()
    )

    if not text and parsed.get("attachment_type"):
        source_type = parsed["attachment_type"]
        text = _resolve_attachment_text(parsed["attachment_type"], parsed["attachment_url"])
        if not text:
            log_meta_event("ignored", {"reason": "unreadable_attachment", "channel": channel, "user_id": user_id})
            diagnostics_service.record(
                event_type="attachment_processing_failed",
                company_id=company_id,
                channel=channel,
                external_user_id=user_id,
                severity="warning",
                status="failed",
                data={"attachment_type": source_type},
            )
            send_meta_text(user_id, ATTACHMENT_FALLBACK_TEXT, channel=channel, company_id=company_id)
            return {"status": "unsupported", "channel": channel, "user_id": user_id}
    official_profile = resolve_meta_profile(user_id=user_id, channel=channel)

    customer = customer_service.upsert_from_channel(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        display_name=official_profile.get("customer_name"),
        profile_picture=official_profile.get("customer_profile_picture"),
        username=official_profile.get("username"),
    )

    effective_customer_name = (
        official_profile.get("customer_name")
        or customer.get("display_name")
        or next(
            (
                identity.get("display_name")
                for identity in customer.get("identities", [])
                if identity.get("channel") == channel
                and identity.get("external_user_id") == user_id
            ),
            None,
        )
    )
    effective_profile_picture = (
        official_profile.get("customer_profile_picture")
        or customer.get("profile_picture")
    )

    diagnostics_service.record(
        event_type="incoming_message",
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        status="received",
        data={"text_length": len(text or "")},
    )

    incoming = save_conversation_message(
        channel=channel,
        user_id=user_id,
        direction="in",
        text=text,
        metadata={
            "source": "meta",
            "sender_type": "customer",
            **official_profile,
            "customer_name": effective_customer_name,
            "customer_profile_picture": effective_profile_picture,
            "customer_id": customer.get("id"),
            "source_type": source_type,
        },
    )

    state = conversation_control_service.record_customer_message(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        official_customer_name=effective_customer_name,
        customer_profile_picture=effective_profile_picture,
    )

    raw_event = parsed.get("raw_event") or {}
    raw_message = raw_event.get("message") or {}
    external_message_id = raw_message.get("mid")
    dedupe_key = (
        f"incoming:{channel}:{external_message_id}"
        if external_message_id
        else None
    )
    notification_service.create(
        company_id=company_id,
        notification_type="customer_message",
        title=f"New {channel.title()} message",
        body=text,
        channel=channel,
        external_user_id=user_id,
        conversation_id=state.get("id"),
        severity="info",
        dedupe_key=dedupe_key,
        data={
            "customer_id": customer.get("id"),
            "customer_name": effective_customer_name,
            "profile_picture": effective_profile_picture,
            "workflow_state": state.get("workflow_state"),
        },
    )

    ai_settings = company_settings_service.get_section(company_id, "ai_behavior")["values"]
    queue_result = schedule_smart_reply(
        channel=channel,
        user_id=user_id,
        company_id=company_id,
        message=text,
        delay_seconds=ai_settings.get("collect_message_delay_seconds", 20),
    )

    diagnostics_service.record(
        event_type="ai_buffer_scheduled",
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        status="queued" if queue_result.get("queued") else "skipped",
        data={
            "message_count": queue_result.get("message_count"),
            "delay_seconds": queue_result.get("delay_seconds"),
            "waiting_for_human_timeout": queue_result.get("waiting_for_human_timeout", False),
            "workflow_state": state.get("workflow_state"),
        },
    )

    was_queued = bool(queue_result.get("queued"))
    result = {
        "status": "received_ai_queued" if was_queued else "received_ai_disabled",
        "channel": channel,
        "from": user_id,
        "text": text,
        "reply": None,
        "sent": False,
        "smart_reply": queue_result,
        "saved": {"in": incoming},
        "workflow_state": state.get("workflow_state"),
    }
    log_meta_event("smart_reply_queued" if was_queued else "smart_reply_skipped", result)
    return result
