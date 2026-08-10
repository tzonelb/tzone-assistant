"""Inbound receiver for the WhatsApp Web bridge.

The bridge forwards every person-to-person message of a paired QR
session here. Messages enter the platform as channel "whatsapp" — the
same unified inbox, ownership, AI-batching and notification pipeline as
WhatsApp Cloud API messages (mirrors channels/whatsapp/processor.py).
Company routing is by bridge session key: each paired session is stored
as a channel_accounts row (channel "whatsapp_qr", external_account_id =
session key).
"""

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from channels.common.rate_limiter import get_client_ip, whatsapp_qr_bridge_rate_limiter
from backend.services.channel_account_service import channel_account_service
from backend.services.company_settings_service import company_settings_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.customer_service import customer_service
from backend.services.notification_service import notification_service
from channels.meta.smart_reply import schedule_smart_reply
from channels.whatsapp_qr import service as bridge_service
from core.conversation_store import save_conversation_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/whatsapp-qr", tags=["WhatsApp QR Webhook"])


def process_whatsapp_qr_message(
    *, company_id: int, user_id: str, text: str, customer_name: str | None = None,
) -> dict[str, Any]:
    """Same sequence as channels/whatsapp/processor.py, with the company
    already resolved (by session key instead of phone_number_id)."""
    incoming = save_conversation_message(
        company_id=company_id,
        channel="whatsapp",
        user_id=user_id,
        direction="in",
        text=text,
        metadata={
            "source": "whatsapp_qr",
            "sender_type": "customer",
            "customer_name": customer_name,
            "source_type": "text",
        },
    )

    customer_service.upsert_from_channel(
        company_id=company_id,
        channel="whatsapp",
        external_user_id=user_id,
        display_name=customer_name,
    )

    state = conversation_control_service.record_customer_message(
        company_id=company_id,
        channel="whatsapp",
        external_user_id=user_id,
        official_customer_name=customer_name,
    )

    notification_service.create(
        company_id=company_id,
        notification_type="customer_message",
        title="New WhatsApp message",
        body=text,
        channel="whatsapp",
        external_user_id=user_id,
        conversation_id=state.get("id"),
        severity="info",
        data={
            "customer_name": customer_name,
            "workflow_state": state.get("workflow_state"),
        },
    )

    ai_settings = company_settings_service.get_section(company_id, "ai_behavior")["values"]
    queue_result = schedule_smart_reply(
        channel="whatsapp",
        user_id=user_id,
        company_id=company_id,
        message=text,
        delay_seconds=ai_settings.get("collect_message_delay_seconds", 20),
    )

    return {"incoming_message": incoming, "state": state, "queue_result": queue_result}


# Registered with AND without the trailing slash: the bridge posts to
# ".../webhook/whatsapp-qr" (no slash), and relying on FastAPI's 307
# redirect would risk the POST body/headers not surviving the redirect in
# some HTTP clients. Both paths hit the same handler.
@router.post("/")
@router.post("")
async def receive_bridge_message(
    request: Request,
    x_bridge_secret: str | None = Header(default=None),
):
    if not whatsapp_qr_bridge_rate_limiter.allow(get_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests")

    if not bridge_service.verify_webhook_secret(x_bridge_secret):
        raise HTTPException(status_code=403, detail="Invalid bridge secret")

    payload = await request.json()
    session_key = str(payload.get("session") or "")
    sender = str(payload.get("from") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not session_key or not sender or not text:
        return {"status": "ignored"}

    account = channel_account_service.resolve_qr_session(session_key=session_key)
    if not account:
        logger.warning("WhatsApp QR message for unknown session %s ignored", session_key)
        return {"status": "unknown_session"}

    kwargs = dict(
        company_id=int(account["company_id"]),
        user_id=sender,
        text=text,
        customer_name=payload.get("name"),
    )
    # Burst absorption: enqueue and ack fast when the async queue is enabled and
    # has room; otherwise process inline (unchanged behaviour / backpressure).
    from core.ingest_queue import ingest_queue
    if ingest_queue.submit(process_whatsapp_qr_message, **kwargs):
        return {"status": "queued"}

    result = process_whatsapp_qr_message(**kwargs)
    return {"status": "ok", "conversation_id": result["state"].get("id")}
