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
from backend.services.notification_service import notification_service
from backend.services.pending_reply_service import pending_reply_service
from backend.services.plan_service import UNLIMITED, plan_service
from backend.services.subscription_gate import subscription_gate
from channels.meta.logger import log_meta_event
from channels.sender import send_text
from gateway.message_gateway import message_gateway


logger = logging.getLogger(__name__)

DEFAULT_DELAY_SECONDS = 20
HUMAN_MODE_POLL_SECONDS = 10

# One batch produces one reply, so the batch is the unit that is counted — not
# the customer's messages inside it, and not the two model calls the reply
# happens to make (`ai_knowledge_matcher` and `ai_router`). An owner reading
# "assistant replies this month" counts what their customers received, and any
# other unit makes the number on the screen unexplainable.
AI_REPLY_METRIC = "ai_replies"


def _ai_allowance_spent(company_id: int) -> bool:
    """Whether this company has used its monthly assistant allowance.

    Never raises. A billing lookup that can fail a reply costs a customer their
    answer over a number, so an unreadable control plane answers "not spent" —
    the same direction every other guard in this codebase fails.
    """
    try:
        allowance = plan_service.limit(company_id, "max_ai_messages")

        if allowance == UNLIMITED:
            return False

        used = plan_service.usage_total(
            company_id=company_id, metric=AI_REPLY_METRIC
        )

        return used >= allowance
    except Exception:
        logger.exception(
            "Could not check the assistant allowance for company %s; replying.",
            company_id,
        )

        return False


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
    """Answer every batch whose wait has elapsed. Returns how many were sent.

    A lapsed subscription stops here as well as at the door. `channels/inbound`
    already declines to queue anything for a paused company, but batches queued
    in the minutes *before* the lapse are still sitting due, and delivering
    them would mean the assistant answering customers after the workspace was
    paused — quietly undoing the decision for as long as the queue lasts.

    Nothing is claimed, so the queue survives intact and is answered the moment
    the company renews.
    """
    if subscription_gate.lapsed(company_id):
        return 0

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

            # A customer wrote and got nothing back. Until now the only trace
            # was a `diagnostic_events` row, which nobody watches and which is
            # cleared after fourteen days — so the team's first hint that the
            # assistant is failing was a customer asking why they were ignored.
            #
            # `notify_on_ai_error` has been stored in every company's database
            # since the settings shipped, offering to switch off a notification
            # that no code raised.
            _notify_failure(
                company_id=company_id,
                channel=batch["channel"],
                external_user_id=batch["external_user_id"],
                error=type(exc).__name__,
            )

    return sent



def _notify_failure(
    *, company_id: int, channel: str, external_user_id: str, error: str
) -> None:
    """Tell the team the assistant failed to answer somebody.

    The class of the exception, never its message: an exception from a provider
    or a database can carry a token, a customer's text, or a row of somebody's
    contact details, and this lands in a list the whole company can read.

    Never raises. The reply already failed; failing to say so must not turn one
    unanswered customer into a worker that stops draining the queue.
    """
    try:
        notification_service.create(
            company_id=company_id,
            notification_type="ai_error",
            title="The assistant could not answer a customer",
            body=f"The reply failed with {error}. The conversation is waiting.",
            channel=channel,
            external_user_id=external_user_id,
            severity="warning",
            # One bell per conversation per failure class. A provider outage
            # affects every conversation at once, and a hundred identical
            # entries is how a team learns to ignore the bell.
            dedupe_key=f"ai_error:{channel}:{external_user_id}:{error}",
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not raise an assistant-failure notification for company %s",
            company_id,
        )


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

    # The monthly assistant allowance, checked before the model is called
    # rather than after — an allowance that still pays for the reply it refused
    # is not an allowance.
    #
    # Running out switches the **assistant** off, not the platform. The
    # customer's messages are already stored and already in the inbox, so the
    # team answers by hand; nothing is said to the customer, because what this
    # company pays is not their customer's business. The batch is completed
    # rather than deferred: retrying it every few seconds for the rest of the
    # month would spend the sweep on work that cannot succeed.
    if _ai_allowance_spent(company_id):
        pending_reply_service.complete(company_id, batch["id"])
        diagnostics_service.record(
            event_type="ai_reply_over_plan_limit",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            severity="warning",
            status="cancelled",
            data={
                "reason": "max_ai_messages",
                "limit": plan_service.limit(company_id, "max_ai_messages"),
                "used": plan_service.usage_total(
                    company_id=company_id, metric=AI_REPLY_METRIC
                ),
                "message_count": len(messages),
            },
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

    # Counted after the send succeeded, so a provider rejection is not billed.
    # Numbers only — the channel and the department, never a word of what was
    # said. Recording never raises: a counter that can fail a reply would cost
    # the customer an answer that has already been delivered.
    plan_service.record_usage(
        company_id=company_id,
        metric=AI_REPLY_METRIC,
        channel=channel,
        department_id=(state or {}).get("department_id"),
    )

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
