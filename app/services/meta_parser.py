def parse_meta_webhook(payload: dict) -> list[dict]:
    parsed_messages = []

    object_type = payload.get("object")

    if object_type == "page":
        platform = "messenger"
    elif object_type == "instagram":
        platform = "instagram"
    else:
        return parsed_messages

    entries = payload.get("entry", [])

    for entry in entries:
        messaging_events = entry.get("messaging", [])

        for event in messaging_events:
            sender_id = event.get("sender", {}).get("id")
            recipient_id = event.get("recipient", {}).get("id")

            text = None

            if "message" in event:
                message = event["message"]
                text = message.get("text")

            elif "postback" in event:
                postback = event["postback"]
                text = postback.get("payload") or postback.get("title")

            if not sender_id or not recipient_id or not text:
                continue

            parsed_messages.append({
                "platform": platform,
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "text": text,
                "raw_event": event,
            })

    return parsed_messages