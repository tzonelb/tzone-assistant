"""Parsing for Meta webhook payloads (Messenger and Instagram).

Meta batches. One webhook delivery can carry several entries, and one entry can
carry several messaging events — routinely so when a customer sends three
messages quickly, or when delivery was retried. The previous parser read only
``entry[0].messaging[0]`` and silently dropped the rest, so the platform lost
customer messages under exactly the conditions where they matter most.

``parse_meta_events`` returns every event in the payload, in order.
"""

from __future__ import annotations

from typing import Any


def detect_meta_channel(payload: dict[str, Any]) -> str:
    """Map the payload's object type to our channel name."""
    obj = payload.get("object")

    if obj == "instagram":
        return "instagram"

    return "messenger"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _event_from_messaging(
    event: dict[str, Any],
    channel: str,
) -> dict[str, Any] | None:
    sender_id = (event.get("sender") or {}).get("id")

    if not sender_id:
        return None

    recipient_id = (event.get("recipient") or {}).get("id")
    message = event.get("message") or {}
    postback = event.get("postback") or {}

    # Echoes are our own outbound messages coming back. Treating them as
    # customer input would make the assistant reply to itself.
    if message.get("is_echo"):
        return {
            "ignored": True,
            "reason": "echo_message",
            "channel": channel,
            "user_id": sender_id,
            "recipient_id": recipient_id,
        }

    text = _clean_text(
        message.get("text")
        or postback.get("title")
        or postback.get("payload")
    )

    if not text:
        return {
            "ignored": True,
            "reason": "non_text_message",
            "channel": channel,
            "user_id": sender_id,
            "recipient_id": recipient_id,
        }

    return {
        "ignored": False,
        "channel": channel,
        "user_id": str(sender_id),
        "recipient_id": str(recipient_id) if recipient_id else None,
        "text": text,
        "message_id": message.get("mid"),
        "timestamp": event.get("timestamp"),
        "raw_event": event,
    }


def _event_from_change(
    change: dict[str, Any],
    channel: str,
) -> dict[str, Any] | None:
    field = change.get("field")
    value = change.get("value") or {}

    if field not in ("messages", "messaging_postbacks"):
        return {
            "ignored": True,
            "reason": f"unsupported_field_{field}",
            "channel": channel,
            "user_id": "unknown",
            "recipient_id": None,
        }

    sender_id = (value.get("sender") or {}).get("id")

    if not sender_id:
        return None

    recipient_id = (value.get("recipient") or {}).get("id")
    message = value.get("message") or {}
    postback = value.get("postback") or {}

    text = _clean_text(
        message.get("text")
        or postback.get("title")
        or postback.get("payload")
    )

    if not text:
        return {
            "ignored": True,
            "reason": "non_text_change",
            "channel": channel,
            "user_id": sender_id,
            "recipient_id": recipient_id,
        }

    return {
        "ignored": False,
        "channel": channel,
        "user_id": str(sender_id),
        "recipient_id": str(recipient_id) if recipient_id else None,
        "text": text,
        "message_id": message.get("mid"),
        "timestamp": value.get("timestamp"),
        "raw_event": change,
    }


def parse_meta_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every event in the payload, including ignored ones.

    Ignored events are kept rather than filtered so the caller can log why a
    delivery produced no conversation instead of guessing.
    """
    if not isinstance(payload, dict):
        return []

    channel = detect_meta_channel(payload)
    events: list[dict[str, Any]] = []

    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue

        # `page_id` identifies which of our connected accounts received the
        # message, which is how the message is routed to the right company.
        page_id = entry.get("id")

        for messaging_event in entry.get("messaging") or []:
            if not isinstance(messaging_event, dict):
                continue

            parsed = _event_from_messaging(messaging_event, channel)

            if parsed is not None:
                parsed.setdefault("page_id", page_id)
                events.append(parsed)

        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue

            parsed = _event_from_change(change, channel)

            if parsed is not None:
                parsed.setdefault("page_id", page_id)
                events.append(parsed)

    return events


def parse_meta_text_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first event in a payload.

    Retained for callers and tests that only need a single event. Anything
    handling real traffic should use :func:`parse_meta_events` so batched
    deliveries are not truncated.
    """
    events = parse_meta_events(payload)

    if not events:
        return None

    for event in events:
        if not event.get("ignored"):
            return event

    return events[0]
