from typing import Any

from backend.services.channel_account_service import channel_account_service
from backend.services.company_settings_service import company_settings_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.customer_service import customer_service
from backend.services.notification_service import notification_service
from channels.meta.smart_reply import schedule_smart_reply
from config.settings import config
from core.conversation_store import save_conversation_message


def process_whatsapp_message(
    user_id: str,
    text: str,
    recipient_phone_number_id: str,
    customer_name: str | None = None,
    source_type: str = "text",
) -> dict[str, Any]:
    """Handle one incoming WhatsApp message.

    Mirrors channels/telegram/processor.py's sequence (save message ->
    update conversation state -> notify -> queue an AI reply via the
    same batched pipeline as every other channel) so WhatsApp
    conversations show up in the unified inbox with the same
    ownership/take-over/AI-batching rules.

    Resolves which company owns this by the phone_number_id that
    received the message (multi-tenant, per company_id's connected
    WhatsApp number) — falls back to the platform default company if
    no company has connected its own WhatsApp yet (keeps the old
    single-tenant .env setup working unchanged).
    """
    account_match = channel_account_service.resolve_meta_account(
        recipient_id=recipient_phone_number_id, channel="whatsapp",
    )
    resolved_company_id = (
        account_match["company_id"] if account_match
        else conversation_control_service.resolve_default_company_id()
    )

    incoming = save_conversation_message(
        channel="whatsapp",
        user_id=user_id,
        direction="in",
        text=text,
        metadata={
            "source": "whatsapp",
            "sender_type": "customer",
            "customer_name": customer_name,
            "source_type": source_type,
        },
    )

    customer_service.upsert_from_channel(
        company_id=resolved_company_id,
        channel="whatsapp",
        external_user_id=user_id,
        display_name=customer_name,
    )

    state = conversation_control_service.record_customer_message(
        company_id=resolved_company_id,
        channel="whatsapp",
        external_user_id=user_id,
        official_customer_name=customer_name,
    )

    notification_service.create(
        company_id=resolved_company_id,
        notification_type="customer_message",
        title="New WhatsApp message",
        body=text,
        channel="whatsapp",
        external_user_id=user_id,
        conversation_id=state.get("id"),
        severity="info",
        data={
            "customer_name": customer_name,
            "workflow_state": state.get("workflow_state"),
        },
    )

    ai_settings = company_settings_service.get_section(resolved_company_id, "ai_behavior")["values"]
    queue_result = schedule_smart_reply(
        channel="whatsapp",
        user_id=user_id,
        company_id=resolved_company_id,
        message=text,
        delay_seconds=ai_settings.get("collect_message_delay_seconds", 20),
    )

    return {
        "incoming_message": incoming,
        "state": state,
        "queue_result": queue_result,
        "company_id": resolved_company_id,
    }
