"""Batching and delivery of assistant replies.

A customer's rapid-fire messages are collected for a few seconds and answered
once. The waiting batch lives in the company's database (see
``pending_reply_service``), so nothing is lost across restarts.

``process_due_replies`` is the only place a reply is generated and sent. It is
driven by the background worker in ``main.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend.services.company_settings_service import company_settings_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.message_service import message_service
from backend.services.pending_reply_service import pending_reply_service
from channels.meta.logger import log_meta_event
from channels.sender import send_text
from gateway.message_gateway import message_gateway


logger = logging.getLogger(__name__)

DEFAULT_DELAY_SECONDS = 20
HUMAN_MODE_POLL_SECONDS = 10


def _ai_settings(company_id: int) -> dict[str, Any]:
    try:
        return company_settings_service.get_section(company_id, "ai_behavior")["values"]
    except Exception:  # noqa: BLE001
        logger.exception("Could not read assistant settings for company %s", company_id)
        return {}


def schedule_smart_reply(
    *,
    channel: str,
    user_id: str,
    company_id: int,
    message: str,
    delay_seconds: int | None = None,
) -> dict[str, Any]:
    """Queue a customer message for the assistant to answer."""
    settings = _ai_settings(company_id)

    if not bool(settings.get("enabled", True)):
        diagnostics_service.record(
            event_type="ai_buffer_cancelled",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="cancelled",
            data={"reason": "ai_disabled"},
        )
        return {
            "queued": False,
            "reason": "ai_disabled",
            "delay_seconds": 0,
            "message_count": 0,
        }

    delay = settings.get(
        "collect_message_delay_seconds",
        delay_seconds or DEFAULT_DELAY_SECONDS,
    )

    return pending_reply_service.enqueue(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        message=message,
        delay_seconds=int(delay),
    )


def process_due_replies(company_id: int) -> int:
    """Answer every batch whose wait has elapsed. Returns how many were sent."""
    sent = 0

    for batch in pending_reply_service.claim_due(company_id):
        try:
            if _process_batch(batch):
                sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Assistant reply failed for company %s conversation %s",
                company_id,
                batch["channel"],
            )
            conversation_control_service.mark_ai_ready_after_error(
                company_id=company_id,
                channel=batch["channel"],
                external_user_id=batch["external_user_id"],
            )
            diagnostics_service.record(
                event_type="ai_reply_error",
                company_id=company_id,
                channel=batch["channel"],
                external_user_id=batch["external_user_id"],
                severity="error",
                status="failed",
                data={"error": type(exc).__name__},
            )
            pending_reply_service.fail(company_id, batch["id"], str(exc))

    return sent


def _process_batch(batch: dict[str, Any]) -> bool:
    company_id = batch["company_id"]
    channel = batch["channel"]
    user_id = batch["external_user_id"]
    messages = batch["messages"]

    if not messages:
        pending_reply_service.complete(company_id, batch["id"])
        return False

    settings = _ai_settings(company_id)

    if not bool(settings.get("enabled", True)):
        pending_reply_service.complete(company_id, batch["id"])
        diagnostics_service.record(
            event_type="ai_buffer_cancelled",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="cancelled",
            data={"reason": "ai_disabled", "message_count": len(messages)},
        )
        return False

    # A human holding the conversation must not be talked over. The batch waits
    # rather than being discarded, so nothing the customer sent is lost.
    if not conversation_control_service.is_ai_handling(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        remaining = conversation_control_service.seconds_until_ai_return(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
        )
        wait = HUMAN_MODE_POLL_SECONDS

        if remaining is not None and remaining > 0:
            wait = max(2, min(HUMAN_MODE_POLL_SECONDS, int(remaining) + 1))

        pending_reply_service.defer(company_id, batch["id"], wait)
        diagnostics_service.record(
            event_type="ai_buffer_waiting_for_human_timeout",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="waiting",
            data={"message_count": len(messages)},
        )
        return False

    combined_message = "\n".join(messages)

    state = conversation_control_service.mark_ai_processing(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    # The account this conversation arrived on, recovered from the conversation
    # row rather than carried on the queued batch. A pending batch is keyed by
    # exactly (company, channel, customer) — the same key as the conversation —
    # so the row is where that fact already lives durably; storing it twice
    # would only create somewhere for the two to disagree. Either way it
    # survives a restart, which the in-process buffer this queue replaced did
    # not.
    started_at = time.perf_counter()
    response = message_gateway.handle_text(
        channel=channel,
        user_id=user_id,
        company_id=company_id,
        message=combined_message,
        channel_account_id=(state or {}).get("channel_account_id"),
    )

    # Generating can take many seconds; an employee may have taken over in the
    # meantime. Sending now would put the assistant and a human in the same
    # conversation at once.
    if not conversation_control_service.is_ai_handling(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        pending_reply_service.defer(company_id, batch["id"], HUMAN_MODE_POLL_SECONDS)
        diagnostics_service.record(
            event_type="ai_reply_cancelled_after_generation",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="cancelled",
            data={"reason": "human_took_over"},
        )
        return False

    buttons = getattr(response, "buttons", None)
    send_result = send_text(
        channel=channel,
        recipient_id=user_id,
        company_id=company_id,
        text=response.text,
        buttons=buttons,
    )

    if not send_result.get("ok") and not send_result.get("skipped"):
        # Keep the batch so the retry path can try again rather than dropping a
        # customer's unanswered messages on a transient provider error.
        raise RuntimeError(
            f"Provider rejected the reply: {send_result.get('error') or send_result.get('status_code')}"
        )

    duration_ms = int((time.perf_counter() - started_at) * 1000)

    message_service.save_message(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        direction="out",
        text=response.text,
        sender_type="ai",
        source="meta_ai_smart_delay",
        provider_message_id=(
            (send_result.get("response") or {}).get("message_id")
            if isinstance(send_result.get("response"), dict)
            else None
        ),
        metadata={
            "buttons": buttons,
            "batched_message_count": len(messages),
            "duration_ms": duration_ms,
        },
    )

    conversation_control_service.record_ai_reply(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        message_count=len(messages),
        delay_seconds=int(settings.get("collect_message_delay_seconds", DEFAULT_DELAY_SECONDS)),
    )

    diagnostics_service.record(
        event_type="ai_reply_sent",
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        status="sent",
        duration_ms=duration_ms,
        data={"message_count": len(messages), "batched": len(messages) > 1},
    )

    pending_reply_service.complete(company_id, batch["id"])

    log_meta_event(
        "assistant_reply_sent",
        {
            "channel": channel,
            "company_id": company_id,
            "message_count": len(messages),
            "duration_ms": duration_ms,
        },
    )

    return True


def pending_snapshot(company_id: int) -> list[dict[str, Any]]:
    return pending_reply_service.snapshot(company_id)
