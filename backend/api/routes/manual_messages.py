from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel, Field

from backend.services.auth_service import (
    auth_service,
    get_current_user,
)
from backend.services.conversation_control_service import (
    ConversationOwnershipConflict,
    conversation_control_service,
)
from backend.services.message_status_service import message_status_service
from channels.meta.sender import send_meta_media, send_meta_text
from channels.telegram.sender import send_telegram_media, send_telegram_text
from channels.whatsapp.sender import send_whatsapp_media, send_whatsapp_text
from core.conversation_store import (
    save_conversation_message,
)


router = APIRouter(
    prefix="/conversations",
    tags=["Conversation Messages"],
)


SUPPORTED_META_CHANNELS = {
    "messenger",
    "instagram",
}

SUPPORTED_CHANNELS = SUPPORTED_META_CHANNELS | {"telegram", "whatsapp"}

MEDIA_TYPES = {"image", "video", "audio", "document"}


class ManualReplyRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=2000,
    )


class ManualMediaReplyRequest(BaseModel):
    media_url: str = Field(min_length=1, max_length=2000)
    media_type: Literal["image", "video", "audio", "document"]
    caption: str | None = Field(default=None, max_length=2000)
    filename: str | None = Field(default=None, max_length=255)


class ManualReplyResponse(BaseModel):
    status: Literal["sent"]
    channel: str
    user_id: str
    message: dict[str, Any]
    provider_result: dict[str, Any]


def _validate_channel(
    channel: str,
) -> str:
    normalized_channel = (
        channel.strip().lower()
    )

    if (
        normalized_channel
        not in SUPPORTED_CHANNELS
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "Manual sending currently "
                "supports only Messenger, "
                "Instagram, WhatsApp, and "
                "Telegram."
            ),
        )

    return normalized_channel


def _conversation_owner_name(
    company_id: int,
    user_id: int | None,
) -> str | None:
    if user_id is None:
        return None

    from database.database import db

    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT
                users.full_name,
                users.email
            FROM users
            JOIN company_users
                ON company_users.user_id = users.id
            WHERE users.id = ?
              AND company_users.company_id = ?
              AND users.status = 'active'
              AND company_users.status = 'active'
            LIMIT 1
            """,
            (user_id, company_id),
        ).fetchone()

    if not row:
        return None

    return (
        row["full_name"]
        or row["email"]
        or f"User {user_id}"
    )


def _extract_meta_error(
    send_result: dict[str, Any],
) -> str:
    response_data = (
        send_result.get("response")
    )

    if isinstance(response_data, dict):
        meta_error = response_data.get(
            "error"
        )

        if isinstance(meta_error, dict):
            error_message = (
                meta_error.get("message")
            )

            if error_message:
                return str(error_message)

    return str(
        send_result.get("error")
        or send_result.get("reason")
        or "Meta rejected the message."
    )


def _prepare_reply(
    *, channel: str, user_id: str, current_user: dict[str, Any],
) -> tuple[str, str, int, dict[str, Any]]:
    """Shared setup for both the text and media manual-reply endpoints:
    validates the channel/user id, checks the conversations.reply
    permission, takes/renews the reply lease (raising 409 if another
    employee owns it), and blocks sending while the AI is still handling
    the conversation. Returns (normalized_channel, normalized_user_id,
    company_id, conversation_state)."""
    normalized_channel = _validate_channel(channel)
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer ID is required.",
        )

    company_id = auth_service.resolve_company_id(current_user)
    auth_service.require_permission(current_user, company_id, "conversations.reply")

    conversation = conversation_control_service.get_state(
        company_id=company_id,
        channel=normalized_channel,
        external_user_id=normalized_user_id,
    )

    try:
        conversation_control_service.renew_reply_lease(
            company_id=company_id,
            channel=normalized_channel,
            external_user_id=normalized_user_id,
            actor_user_id=current_user["id"],
        )
    except ConversationOwnershipConflict as exc:
        owner_name = _conversation_owner_name(company_id, exc.owner_user_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "conversation_owned",
                "message": (
                    "Take over this conversation before replying."
                    if exc.owner_user_id is None
                    else (
                        f"Conversation is assigned to {owner_name}."
                        if owner_name
                        else "This conversation is assigned to another employee."
                    )
                ),
                "owner_user_id": exc.owner_user_id,
                "owner_user_name": owner_name,
            },
        ) from exc

    handled_by_ai = bool(conversation.get("handled_by_ai", 1))
    ai_enabled = bool(conversation.get("ai_enabled", 1))
    if handled_by_ai or ai_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Take over the conversation before sending a manual reply.",
        )

    return normalized_channel, normalized_user_id, company_id, conversation


def _normalize_whatsapp_result(whatsapp_result: dict[str, Any]) -> dict[str, Any]:
    """WhatsApp's senders return {"sent": ..., "response": ...} while
    Messenger/Instagram/Telegram all return {"ok": ..., ...} — this
    normalizes so the rest of the handler (including _extract_meta_error,
    which understands the Graph API error shape WhatsApp Cloud shares
    with Messenger/Instagram) can treat every channel the same way."""
    return {
        "ok": whatsapp_result.get("sent", False),
        "response": whatsapp_result.get("response", {}),
        "reason": whatsapp_result.get("reason"),
    }


def _extract_provider_info(
    normalized_channel: str, send_result: dict[str, Any],
) -> tuple[str, str | None]:
    response_payload = send_result.get("response", {})
    if not isinstance(response_payload, dict):
        response_payload = {}

    if normalized_channel == "telegram":
        return "telegram", response_payload.get("result", {}).get("message_id")
    if normalized_channel == "whatsapp":
        return "whatsapp", (response_payload.get("messages") or [{}])[0].get("id")
    return "meta", response_payload.get("message_id")


def _finish_reply(
    *, normalized_channel: str, normalized_user_id: str, company_id: int,
    current_user: dict[str, Any], send_result: dict[str, Any], text: str,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared tail for both endpoints once the provider call has
    succeeded: records delivery status, saves the outbound message, and
    advances conversation ownership bookkeeping."""
    employee_name = current_user.get("full_name") or current_user.get("email") or "Employee"
    provider_name, provider_message_id = _extract_provider_info(normalized_channel, send_result)

    if provider_message_id:
        message_status_service.record_sent(
            channel=normalized_channel, provider_message_id=str(provider_message_id),
            company_id=company_id, recipient_id=normalized_user_id,
        )

    saved_message = save_conversation_message(
        company_id=company_id,
        channel=normalized_channel,
        user_id=normalized_user_id,
        direction="out",
        text=text,
        metadata={
            "source": "dashboard",
            "sender_type": "employee",
            "employee_id": current_user.get("id"),
            "employee_name": employee_name,
            "provider": provider_name,
            "provider_message_id": provider_message_id,
            **(extra_metadata or {}),
        },
    )

    try:
        conversation_control_service.record_employee_reply(
            company_id=company_id,
            channel=normalized_channel,
            external_user_id=normalized_user_id,
            actor_user_id=int(current_user["id"]),
            message_preview=text or "[media]",
        )
    except ConversationOwnershipConflict:
        # The provider already accepted the message, so never convert this
        # bookkeeping race into an HTTP 500. The next refresh exposes the
        # current owner and the following reply will be blocked with 409.
        pass

    return {
        "status": "sent",
        "channel": normalized_channel,
        "user_id": normalized_user_id,
        "message": saved_message,
        "provider_result": send_result,
    }


@router.post(
    "/{channel}/{user_id}/reply",
    response_model=ManualReplyResponse,
)
def send_manual_conversation_reply(
    channel: str,
    user_id: str,
    payload: ManualReplyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    normalized_channel, normalized_user_id, company_id, _conversation = _prepare_reply(
        channel=channel, user_id=user_id, current_user=current_user,
    )

    message_text = payload.text.strip()
    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message cannot be empty.",
        )

    if normalized_channel == "telegram":
        send_result = send_telegram_text(
            recipient_id=normalized_user_id,
            text=message_text,
        )
    elif normalized_channel == "whatsapp":
        send_result = _normalize_whatsapp_result(
            send_whatsapp_text(normalized_user_id, message_text, company_id=company_id)
        )
    else:
        send_result = send_meta_text(
            recipient_id=normalized_user_id,
            text=message_text,
            channel=normalized_channel,
            company_id=company_id,
            is_human_agent=True,
        )

    if not send_result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Message was not sent: {_extract_meta_error(send_result)}",
        )

    return _finish_reply(
        normalized_channel=normalized_channel, normalized_user_id=normalized_user_id,
        company_id=company_id, current_user=current_user, send_result=send_result,
        text=message_text,
    )


@router.post(
    "/{channel}/{user_id}/reply-media",
    response_model=ManualReplyResponse,
)
def send_manual_conversation_media_reply(
    channel: str,
    user_id: str,
    payload: ManualMediaReplyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    normalized_channel, normalized_user_id, company_id, _conversation = _prepare_reply(
        channel=channel, user_id=user_id, current_user=current_user,
    )
    caption = (payload.caption or "").strip() or None

    if normalized_channel == "telegram":
        send_result = send_telegram_media(
            recipient_id=normalized_user_id, media_url=payload.media_url,
            media_type=payload.media_type, caption=caption,
        )
    elif normalized_channel == "whatsapp":
        send_result = _normalize_whatsapp_result(
            send_whatsapp_media(
                normalized_user_id, payload.media_url, payload.media_type,
                caption=caption, company_id=company_id, filename=payload.filename,
            )
        )
    else:
        send_result = send_meta_media(
            recipient_id=normalized_user_id, media_url=payload.media_url,
            media_type=payload.media_type, caption=caption,
            channel=normalized_channel, company_id=company_id,
        )

    if not send_result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Message was not sent: {_extract_meta_error(send_result)}",
        )

    return _finish_reply(
        normalized_channel=normalized_channel, normalized_user_id=normalized_user_id,
        company_id=company_id, current_user=current_user, send_result=send_result,
        text=caption or "",
        extra_metadata={
            "media_url": payload.media_url,
            "media_type": payload.media_type,
            "media_filename": payload.filename,
        },
    )
