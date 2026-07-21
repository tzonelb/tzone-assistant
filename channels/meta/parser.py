from typing import Any


def detect_meta_channel(payload: dict[str, Any]) -> str:
    obj = payload.get("object")

    if obj == "page":
        return "messenger"

    if obj == "instagram":
        return "instagram"

    return "messenger"


def parse_from_messaging(payload: dict[str, Any]) -> dict[str, Any] | None:
    entries = payload.get("entry", [])
    if not entries:
        return None

    messaging_events = entries[0].get("messaging", [])
    if not messaging_events:
        return None

    event = messaging_events[0]

    sender_id = event.get("sender", {}).get("id")
    recipient_id = event.get("recipient", {}).get("id")
    message = event.get("message", {})
    postback = event.get("postback", {})

    if not sender_id:
        return None

    if message.get("is_echo"):
        return {
            "ignored": True,
            "reason": "echo_message",
            "channel": "messenger",
            "user_id": sender_id,
        }

    text = message.get("text") or postback.get("title") or postback.get("payload")

    if not text:
        return {
            "ignored": True,
            "reason": "non_text_message",
            "channel": "messenger",
            "user_id": sender_id,
        }

    return {
        "ignored": False,
        "channel": "messenger",
        "user_id": sender_id,
        "recipient_id": recipient_id,
        "text": text.strip(),
        "raw_event": event,
    }


def parse_from_changes(payload: dict[str, Any]) -> dict[str, Any] | None:
    entries = payload.get("entry", [])
    if not entries:
        return None

    changes = entries[0].get("changes", [])
    if not changes:
        return None

    change = changes[0]
    field = change.get("field")
    value = change.get("value", {})

    if field not in ["messages", "messaging_postbacks"]:
        return {
            "ignored": True,
            "reason": f"unsupported_field_{field}",
            "channel": "messenger",
            "user_id": "unknown",
        }

    sender_id = value.get("sender", {}).get("id")
    recipient_id = value.get("recipient", {}).get("id")
    message = value.get("message", {})
    postback = value.get("postback", {})

    text = message.get("text") or postback.get("title") or postback.get("payload")

    if not sender_id:
        return None

    if not text:
        return {
            "ignored": True,
            "reason": "non_text_change",
            "channel": "messenger",
            "user_id": sender_id,
        }

    return {
        "ignored": False,
        "channel": "messenger",
        "user_id": sender_id,
        "recipient_id": recipient_id,
        "text": text.strip(),
        "raw_event": change,
    }


def parse_meta_text_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    parsed = parse_from_messaging(payload)
    if parsed:
        return parsed

    parsed = parse_from_changes(payload)
    if parsed:
        return parsed

    return None