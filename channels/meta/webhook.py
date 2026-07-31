from fastapi import APIRouter, Request, HTTPException
import json

from channels.meta.processor import process_meta_payload
from channels.meta.logger import log_meta_event
from config.settings import config


router = APIRouter(prefix="/webhook", tags=["Meta Webhook"])


@router.get("/meta")
async def verify_meta_webhook(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == config.META_VERIFY_TOKEN:
        log_meta_event("verified", {"status": "success"})
        return int(challenge)

    raise HTTPException(status_code=403, detail="Meta webhook verification failed")


@router.post("/meta")
async def receive_meta_webhook(request: Request):
    body = await request.body()

    if not body:
        log_meta_event("empty_post", {"reason": "empty_body"})
        return {
            "status": "ignored",
            "reason": "empty_body"
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        log_meta_event("invalid_json", {
            "body": body.decode("utf-8", errors="ignore")
        })
        return {
            "status": "ignored",
            "reason": "invalid_json"
        }

    log_meta_event("webhook_received", payload)

    _record_messenger_delivery_read_events(payload)
    _ingest_community_comments(payload)

    result = process_meta_payload(payload)

    return {
        "status": "ok",
        "result": result
    }


def _ingest_community_comments(payload: dict) -> None:
    """Facebook Page `feed` comment events and Instagram `comments` events
    arrive as `changes` on the same webhook. Route them into the unified
    comment inbox without disturbing the normal message flow. Best-effort:
    never raises."""
    try:
        from backend.services.comment_service import comment_service

        comment_service.ingest_webhook(payload)
    except Exception:
        pass


def _record_messenger_delivery_read_events(payload: dict) -> None:
    """Messenger sends delivery/read confirmations as their own
    'messaging' events (a 'delivery' or 'read' key instead of
    'message') — separate from the actual message content, and not
    something parse_meta_text_message handles. This only records
    ticks status; it never touches the normal incoming-message flow."""
    try:
        from backend.services.message_status_service import message_status_service

        for entry in payload.get("entry", []):
            for event in entry.get("messaging", []):
                delivery = event.get("delivery")
                if delivery and delivery.get("mids"):
                    for mid in delivery["mids"]:
                        message_status_service.update_status(
                            channel="messenger", provider_message_id=mid, status="delivered",
                        )
                read_event = event.get("read")
                if read_event:
                    # Messenger's "read" event only gives a watermark
                    # (a timestamp), not specific message ids — every
                    # message sent to this user before that watermark
                    # counts as read. We approximate by marking the
                    # most recent "delivered" messages for this
                    # recipient as read via the watermark timestamp.
                    recipient_id = (event.get("recipient") or {}).get("id")
                    watermark = read_event.get("watermark")
                    if recipient_id and watermark:
                        message_status_service.mark_read_by_watermark(
                            channel="messenger", recipient_id=recipient_id, watermark=watermark,
                        )
    except Exception:
        pass