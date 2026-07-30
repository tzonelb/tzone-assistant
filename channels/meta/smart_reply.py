from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from threading import Lock, Timer
from typing import Any

from backend.services.company_settings_service import company_settings_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.message_status_service import message_status_service
from channels.meta.logger import log_meta_event
from channels.meta.sender import send_meta_buttons
from channels.telegram.sender import send_telegram_buttons
from channels.whatsapp.sender import send_whatsapp_text
from core.conversation_store import save_conversation_message
from database.database import db
from gateway.message_gateway import message_gateway


DEFAULT_DELAY_SECONDS = max(
    5,
    min(60, int(os.getenv("SMART_AI_DELAY_SECONDS", "20"))),
)
HUMAN_MODE_POLL_SECONDS = 10


@dataclass
class PendingReply:
    channel: str
    user_id: str
    company_id: int
    messages: list[str] = field(default_factory=list)
    timer: Timer | None = None
    generation: int = 0
    created_at_monotonic: float = field(default_factory=time.monotonic)


_PENDING: dict[str, PendingReply] = {}
_LOCK = Lock()


def cancel_all_pending() -> None:
    """Cancels every scheduled AI-reply timer immediately. Production
    code never needs this — it exists so tests can clean up background
    threading.Timer instances between tests. Without this, a timer
    scheduled by one test (default 20s delay) can fire minutes later
    during a completely different test, against a database that no
    longer matches its schema expectations, causing flaky failures."""
    with _LOCK:
        for pending in _PENDING.values():
            if pending.timer is not None:
                pending.timer.cancel()
        _PENDING.clear()


def _record_sent_status(*, channel: str, send_result: dict, company_id: int, recipient_id: str) -> str | None:
    """Extracts the provider's message id from whatever shape that
    channel's sender returns, and records a 'sent' status for the
    ticks feature. Best-effort — a missing/unexpected shape just means
    no ticks show for that message, never an error. Returns the id so
    the caller can also save it into the message's own metadata."""
    try:
        response = send_result.get("response") if isinstance(send_result, dict) else None
        if not isinstance(response, dict):
            return None
        if channel == "whatsapp":
            messages = response.get("messages") or []
            provider_message_id = messages[0].get("id") if messages else None
        elif channel == "telegram":
            provider_message_id = response.get("result", {}).get("message_id")
        else:
            provider_message_id = response.get("message_id")
        if provider_message_id:
            message_status_service.record_sent(
                channel=channel, provider_message_id=str(provider_message_id),
                company_id=company_id, recipient_id=recipient_id,
            )
            return str(provider_message_id)
    except Exception:
        pass
    return None


def _key(company_id: int, channel: str, user_id: str) -> str:
    return f"{company_id}:{channel.strip().lower()}:{user_id.strip()}"


def _start_timer(pending: PendingReply, delay_seconds: int) -> None:
    pending.generation += 1
    generation = pending.generation
    timer = Timer(
        delay_seconds,
        _finish_pending,
        args=(pending.company_id, pending.channel, pending.user_id, generation),
    )
    timer.daemon = True
    pending.timer = timer
    timer.start()


def _reschedule_while_human(pending: PendingReply) -> None:
    if pending.timer is not None:
        pending.timer.cancel()
    remaining = conversation_control_service.seconds_until_ai_return(
        company_id=pending.company_id,
        channel=pending.channel,
        external_user_id=pending.user_id,
    )
    delay = HUMAN_MODE_POLL_SECONDS
    if remaining is not None and remaining > 0:
        delay = max(2, min(HUMAN_MODE_POLL_SECONDS, int(remaining) + 1))
    _start_timer(pending, delay)


def _finish_pending(company_id: int, channel: str, user_id: str, generation: int) -> None:
    key = _key(company_id, channel, user_id)

    with _LOCK:
        pending = _PENDING.get(key)
        if pending is None or pending.generation != generation:
            return
        messages = [value.strip() for value in pending.messages if value.strip()]

    if not messages:
        with _LOCK:
            _PENDING.pop(key, None)
        return

    settings = company_settings_service.get_section(company_id, "ai_behavior")["values"]
    if not bool(settings.get("enabled", True)):
        with _LOCK:
            current = _PENDING.pop(key, None)
            if current and current.timer is not None:
                current.timer.cancel()
        diagnostics_service.record(
            event_type="ai_buffer_cancelled",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="cancelled",
            data={"reason": "ai_disabled", "message_count": len(messages)},
        )
        return

    # Messages received while a human owns the chat must never be lost. Keep the
    # same buffer alive and poll until the takeover expires or the employee
    # explicitly returns the conversation to AI.
    if not conversation_control_service.is_ai_handling(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        with _LOCK:
            current = _PENDING.get(key)
            if current is None or current.generation != generation:
                return
            _reschedule_while_human(current)
        diagnostics_service.record(
            event_type="ai_buffer_waiting_for_human_timeout",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="waiting",
            data={"message_count": len(messages)},
        )
        return

    # Claim and remove the buffer only after AI is truly allowed to answer.
    with _LOCK:
        pending = _PENDING.get(key)
        if pending is None or pending.generation != generation:
            return
        messages = [value.strip() for value in pending.messages if value.strip()]
        _PENDING.pop(key, None)

    if not messages:
        return

    configured_delay = max(
        5,
        min(60, int(settings.get("collect_message_delay_seconds", DEFAULT_DELAY_SECONDS))),
    )
    combined_message = "\n".join(messages)

    try:
        conversation_control_service.mark_ai_processing(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
        )
        started_at = time.perf_counter()
        response = message_gateway.handle_text(
            channel=channel,
            user_id=user_id,
            message=combined_message,
            company_id=company_id,
        )

        # A human may take over while the model is generating. Do not send an AI
        # reply in that case; retain the conversation under human control.
        if not conversation_control_service.is_ai_handling(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
        ):
            diagnostics_service.record(
                event_type="ai_reply_cancelled_after_generation",
                company_id=company_id,
                channel=channel,
                external_user_id=user_id,
                status="cancelled",
                data={"reason": "human_took_over", "message_count": len(messages)},
            )
            return

        buttons = getattr(response, "buttons", None)
        if channel == "telegram":
            send_result = send_telegram_buttons(
                recipient_id=user_id,
                text=response.text,
                buttons=buttons,
                channel=channel,
            )
        elif channel == "whatsapp":
            send_result = send_whatsapp_text(
                to=user_id,
                text=response.text,
                buttons=buttons,
                company_id=company_id,
            )
        else:
            send_result = send_meta_buttons(
                recipient_id=user_id,
                text=response.text,
                buttons=buttons,
                channel=channel,
                company_id=company_id,
            )
        duration_ms = int((time.perf_counter() - started_at) * 1000)

        sent_provider_message_id = _record_sent_status(channel=channel, send_result=send_result, company_id=company_id, recipient_id=user_id)

        diagnostics_service.record(
            event_type="ai_reply_sent",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="sent",
            duration_ms=duration_ms,
            data={
                "message_count": len(messages),
                "batched": len(messages) > 1,
                "delay_seconds": configured_delay,
            },
        )
        diagnostics_service.record(
            event_type="outgoing_message",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="sent",
            duration_ms=duration_ms,
            data={"sender_type": "ai", "text_length": len(response.text or "")},
        )

        outgoing = save_conversation_message(
            channel=channel,
            user_id=user_id,
            direction="out",
            text=response.text,
            metadata={
                "buttons": buttons,
                "send_result": send_result,
                "sender_type": "ai",
                "source": "meta_ai_smart_delay",
                "batched_message_count": len(messages),
                "smart_delay_seconds": configured_delay,
                "provider_message_id": sent_provider_message_id,
            },
        )

        state = conversation_control_service.record_ai_reply(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            message_count=len(messages),
            delay_seconds=configured_delay,
        )

        log_meta_event(
            "smart_reply_sent",
            {
                "channel": channel,
                "user_id": user_id,
                "message_count": len(messages),
                "delay_seconds": configured_delay,
                "sent": send_result,
                "saved": outgoing,
                "conversation_id": state.get("id"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        conversation_control_service.mark_ai_ready_after_error(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
        )
        diagnostics_service.record(
            event_type="ai_reply_error",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            severity="error",
            status="failed",
            data={"error": str(exc), "message_count": len(messages)},
        )
        log_meta_event(
            "smart_reply_error",
            {
                "channel": channel,
                "user_id": user_id,
                "message_count": len(messages),
                "error": str(exc),
            },
        )


def schedule_smart_reply(
    *,
    channel: str,
    user_id: str,
    company_id: int,
    message: str,
    delay_seconds: int | None = None,
) -> dict[str, Any]:
    settings = company_settings_service.get_section(company_id, "ai_behavior")["values"]
    if not bool(settings.get("enabled", True)):
        diagnostics_service.record(
            event_type="ai_buffer_cancelled",
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            status="cancelled",
            data={"reason": "ai_disabled"},
        )
        return {"queued": False, "reason": "ai_disabled", "delay_seconds": 0, "message_count": 0}

    configured_delay = settings.get(
        "collect_message_delay_seconds",
        delay_seconds or DEFAULT_DELAY_SECONDS,
    )
    delay = max(5, min(60, int(configured_delay)))
    key = _key(company_id, channel, user_id)

    with _LOCK:
        pending = _PENDING.get(key)
        if pending is None:
            pending = PendingReply(channel=channel, user_id=user_id, company_id=company_id)
            _PENDING[key] = pending
        if pending.timer is not None:
            pending.timer.cancel()
        pending.messages.append(str(message or ""))
        pending.company_id = company_id
        _start_timer(pending, delay)
        message_count = len(pending.messages)

    human_mode = not conversation_control_service.is_ai_handling(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )
    log_meta_event(
        "smart_reply_scheduled",
        {
            "channel": channel,
            "user_id": user_id,
            "message_count": message_count,
            "delay_seconds": delay,
            "waiting_for_human_timeout": human_mode,
        },
    )
    return {
        "queued": True,
        "delay_seconds": delay,
        "message_count": message_count,
        "waiting_for_human_timeout": human_mode,
    }


def pending_snapshot() -> list[dict[str, Any]]:
    """Diagnostics-safe view of the in-memory queues."""
    with _LOCK:
        return [
            {
                "company_id": item.company_id,
                "channel": item.channel,
                "user_id": item.user_id,
                "message_count": len(item.messages),
                "age_seconds": int(time.monotonic() - item.created_at_monotonic),
            }
            for item in _PENDING.values()
        ]
