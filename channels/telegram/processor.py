from typing import Any

from backend.services.company_settings_service import company_settings_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.customer_service import customer_service
from backend.services.notification_service import notification_service
from channels.meta.smart_reply import schedule_smart_reply
from config.settings import config
from core.conversation_store import save_conversation_message


def process_telegram_message(
    user_id: str,
    text: str,
    customer_name: str | None = None,
    username: str | None = None,
    phone: str | None = None,
    company_id: int | None = None,
) -> dict[str, Any]:
    """Handle one incoming Telegram message.

    Mirrors channels/meta/processor.py's incoming-message sequence
    (save message -> update conversation state -> notify -> queue an AI
    reply) so Telegram conversations show up in the same unified inbox,
    with the same ownership/take-over/AI-batching rules as Messenger.

    Also saves the customer's Telegram username (always available) and
    phone number (only when Telegram provides it, e.g. a contact share)
    into the same customer_service store Messenger/WhatsApp use.

    Telegram doesn't have a per-page company mapping the way Meta does
    (one bot token today, not one per company), so this defaults to the
    platform's default company unless a specific one is passed in.
    """
    resolved_company_id = company_id or config.DEFAULT_COMPANY_ID

    incoming = save_conversation_message(
        channel="telegram",
        user_id=user_id,
        direction="in",
        text=text,
        metadata={
            "source": "telegram",
            "sender_type": "customer",
            "customer_name": customer_name,
            "username": username,
        },
    )

    state = conversation_control_service.record_customer_message(
        company_id=resolved_company_id,
        channel="telegram",
        external_user_id=user_id,
        official_customer_name=customer_name,
    )

    customer = customer_service.upsert_from_channel(
        company_id=resolved_company_id,
        channel="telegram",
        external_user_id=user_id,
        display_name=customer_name,
        username=username,
    )
    if phone:
        customer_service.update_customer(
            company_id=resolved_company_id,
            customer_id=customer["id"],
            values={"phone": phone},
            actor_user_id=None,
        )

    notification_service.create(
        company_id=resolved_company_id,
        notification_type="customer_message",
        title="New Telegram message",
        body=text,
        channel="telegram",
        external_user_id=user_id,
        conversation_id=state.get("id"),
        severity="info",
        data={
            "customer_name": customer_name,
            "username": username,
            "workflow_state": state.get("workflow_state"),
        },
    )

    ai_settings = company_settings_service.get_section(resolved_company_id, "ai_behavior")["values"]
    queue_result = schedule_smart_reply(
        channel="telegram",
        user_id=user_id,
        company_id=resolved_company_id,
        message=text,
        delay_seconds=ai_settings.get("collect_message_delay_seconds", 20),
    )

    return {
        "incoming_message": incoming,
        "state": state,
        "customer": customer,
        "queue_result": queue_result,
    }
