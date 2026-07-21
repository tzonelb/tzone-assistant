from datetime import datetime
from app.database import database


async def save_conversation_message(
    platform: str,
    sender_id: str,
    recipient_id: str,
    message_text: str,
    direction: str,
    raw_payload: dict | None = None,
):
    query = """
        INSERT INTO conversation_messages
        (
            platform,
            sender_id,
            recipient_id,
            message_text,
            direction,
            raw_payload,
            created_at
        )
        VALUES
        (
            :platform,
            :sender_id,
            :recipient_id,
            :message_text,
            :direction,
            :raw_payload,
            :created_at
        )
        RETURNING id
    """

    values = {
        "platform": platform,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "message_text": message_text,
        "direction": direction,
        "raw_payload": raw_payload,
        "created_at": datetime.utcnow(),
    }

    row = await database.fetch_one(query=query, values=values)

    return {"id": row["id"] if row else None}