"""Turns one verified inbound message into stored state for a company.

Shared by every channel. Called once per event with the company already
resolved from the receiving account, so everything written here lands in that
company's own encrypted database.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.company_settings_service import company_settings_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.customer_service import customer_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.message_service import message_service
from backend.services.notification_service import notification_service
from channels.meta.logger import log_meta_event
from channels.meta.profile import resolve_meta_profile
from channels.meta.smart_reply import schedule_smart_reply


logger = logging.getLogger(__name__)


def process_inbound_event(*, event: dict[str, Any], company_id: int) -> dict[str, Any]:
    channel = event["channel"]
    user_id = event["user_id"]
    text = event["text"]
    provider_message_id = event.get("message_id")

    # Checked before anything is written. Providers retry deliveries, and doing
    # this after recording the message would still bump the unread counter and
    # re-notify the team for a message they have already seen.
    if message_service.is_duplicate(company_id, provider_message_id):
        log_meta_event(
            "event_duplicate",
            {"channel": channel, "company_id": company_id},
        )
        return {
            "status": "ignored",
            "reason": "duplicate_message",
            "channel": channel,
        }

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

    state = conversation_control_service.record_customer_message(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        official_customer_name=effective_customer_name,
        customer_profile_picture=effective_profile_picture,
    )

    saved = message_service.save_message(
        company_id=company_id,
        conversation_id=state.get("id"),
        channel=channel,
        external_user_id=user_id,
        direction="in",
        text=text,
        sender_type="customer",
        provider_message_id=provider_message_id,
        source="meta",
        metadata={
            "customer_id": customer.get("id"),
            "customer_name": effective_customer_name,
            "customer_profile_picture": effective_profile_picture,
            **{
                key: value
                for key, value in official_profile.items()
                if key != "customer_name"
            },
        },
    )

    # Meta retries deliveries. Re-running the assistant for a message we already
    # answered would double-reply to the customer and double-bill the model.
    if saved.get("duplicate"):
        log_meta_event(
            "event_duplicate",
            {"channel": channel, "company_id": company_id},
        )
        return {
            "status": "ignored",
            "reason": "duplicate_message",
            "channel": channel,
        }

    diagnostics_service.record(
        event_type="incoming_message",
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        status="received",
        data={"text_length": len(text or "")},
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
        dedupe_key=(
            f"incoming:{channel}:{provider_message_id}"
            if provider_message_id
            else None
        ),
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
            "workflow_state": state.get("workflow_state"),
        },
    )

    was_queued = bool(queue_result.get("queued"))

    log_meta_event(
        "event_processed",
        {
            "channel": channel,
            "company_id": company_id,
            "queued": was_queued,
            "workflow_state": state.get("workflow_state"),
        },
    )

    return {
        "status": "received_ai_queued" if was_queued else "received_ai_disabled",
        "channel": channel,
        "company_id": company_id,
        "conversation_id": state.get("id"),
        "message_id": saved.get("id"),
        "workflow_state": state.get("workflow_state"),
    }
