"""Sending a reply from the dashboard.

Works for every channel the platform can send on, WhatsApp included. Previously
only Messenger and Instagram were accepted, so an employee simply could not
answer a WhatsApp customer even though the sending code was already there.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, require_permission
from backend.services.conversation_control_service import (
    ConversationOwnershipConflict,
    conversation_control_service,
)
from backend.services.message_service import message_service
from backend.services.media_upload_service import (
    ALLOWED_EXTENSIONS,
    MediaUploadError,
    media_upload_service,
)
from channels.sender import (
    SUPPORTED_CHANNELS,
    UnsupportedChannel,
    extract_error,
    normalize_channel,
    send_media,
    send_text,
)
from config.settings import config


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversation Messages"])


class ManualReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ManualMediaReplyRequest(BaseModel):
    """An attachment already uploaded through /api/media/upload.

    The client sends back the `url` that upload returned, not a new path: the
    file has to be one this platform stored, or the platform would become an
    open relay that fetches any URL an employee names and hands it to a
    customer over the company's own channel.
    """

    media_url: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=20)
    caption: str | None = Field(default=None, max_length=2000)
    filename: str | None = Field(default=None, max_length=255)


class ManualReplyResponse(BaseModel):
    status: Literal["sent"]
    channel: str
    user_id: str
    message: dict[str, Any]
    provider_result: dict[str, Any]


def _ownership_conflict(
    company_id: int, exc: ConversationOwnershipConflict
) -> HTTPException:
    """The 409 an employee gets when somebody else holds the conversation.

    Shared by the text and attachment replies so both answer identically: the
    screen shows one message and one "take over" affordance either way.
    """
    owner_id = exc.owner_user_id
    names = auth_service.user_display_names(
        company_id, [owner_id] if owner_id else []
    )
    owner_name = names.get(int(owner_id)) if owner_id else None

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "conversation_owned",
            "message": (
                "Take over this conversation before replying."
                if owner_id is None
                else (
                    f"Conversation is assigned to {owner_name}."
                    if owner_name
                    else "This conversation is assigned to another employee."
                )
            ),
            "owner_user_id": owner_id,
            "owner_user_name": owner_name,
        },
    )


@router.post("/{channel}/{user_id}/reply-media", response_model=ManualReplyResponse)
def send_manual_conversation_media_reply(
    channel: str,
    user_id: str,
    payload: ManualMediaReplyRequest,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    """Send a file the employee already uploaded.

    Deliberately the same shape as the text reply above -- same permission, same
    ownership lease, same recording -- because to the customer and to the audit
    trail this is a reply that happens to be a file.
    """
    normalized_channel = normalize_channel(channel)
    normalized_user_id = user_id.strip()

    if normalized_channel not in SUPPORTED_CHANNELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Replies are supported on: "
                f"{', '.join(sorted(SUPPORTED_CHANNELS))}."
            ),
        )

    if not normalized_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer ID is required.",
        )

    if payload.media_type not in set(ALLOWED_EXTENSIONS.values()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{payload.media_type} is not a kind of file any channel carries.",
        )

    company_id = auth_service.resolve_company_id(current_user)

    # The URL must name a file this platform stored for THIS company. Without
    # that check an employee could name any address and have the platform fetch
    # it and deliver it from the company's own channel -- and could name another
    # company's upload path.
    expected_prefix = f"/api/media/{company_id}/"

    if not payload.media_url.startswith(expected_prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attach a file uploaded to this workspace.",
        )

    try:
        media_upload_service.path_for(
            company_id=company_id,
            stored_name=payload.media_url[len(expected_prefix):],
        )
    except MediaUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    # The provider fetches the file itself, so it needs an address on the public
    # internet. Refusing here beats sending a link the provider cannot resolve
    # and reporting success for a message the customer never receives.
    public_base = str(config.APP_PUBLIC_URL or "").rstrip("/")

    if not public_base.lower().startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Attachments need APP_PUBLIC_URL set to this platform's public "
                "address, because the channel fetches the file itself."
            ),
        )

    try:
        conversation = conversation_control_service.renew_reply_lease(
            company_id=company_id,
            channel=normalized_channel,
            external_user_id=normalized_user_id,
            actor_user_id=int(current_user["id"]),
        )
    except ConversationOwnershipConflict as exc:
        raise _ownership_conflict(company_id, exc) from exc

    try:
        send_result = send_media(
            channel=normalized_channel,
            recipient_id=normalized_user_id,
            company_id=company_id,
            media_url=f"{public_base}{payload.media_url}",
            media_type=payload.media_type,
            caption=payload.caption,
            filename=payload.filename,
        )
    except UnsupportedChannel as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if not send_result.get("ok"):
        logger.warning(
            "Manual attachment rejected by provider on %s for company %s",
            normalized_channel,
            company_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The file was not sent: {extract_error(send_result)}",
        )

    employee_name = (
        current_user.get("full_name") or current_user.get("email") or "Employee"
    )

    saved_message = message_service.save_message(
        company_id=company_id,
        conversation_id=conversation.get("id"),
        channel=normalized_channel,
        external_user_id=normalized_user_id,
        direction="out",
        # The transcript needs something readable: a row with no text reads as
        # an empty message in every export and every timeline.
        text=payload.caption or f"[{payload.media_type}] {payload.filename or ''}".strip(),
        sender_type="employee",
        sender_user_id=int(current_user["id"]),
        source="dashboard",
        metadata={
            "employee_name": employee_name,
            "media_url": payload.media_url,
            "media_type": payload.media_type,
            "filename": payload.filename,
        },
    )

    try:
        conversation_control_service.record_employee_reply(
            company_id=company_id,
            channel=normalized_channel,
            external_user_id=normalized_user_id,
            actor_user_id=int(current_user["id"]),
            message_preview=payload.caption or f"[{payload.media_type}]",
        )
    except ConversationOwnershipConflict:
        logger.info(
            "Ownership changed while recording a sent attachment for company %s",
            company_id,
        )

    return {
        "status": "sent",
        "channel": normalized_channel,
        "user_id": normalized_user_id,
        "message": saved_message,
        "provider_result": send_result,
    }


@router.post("/{channel}/{user_id}/reply", response_model=ManualReplyResponse)
def send_manual_conversation_reply(
    channel: str,
    user_id: str,
    payload: ManualReplyRequest,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    normalized_channel = normalize_channel(channel)
    normalized_user_id = user_id.strip()
    message_text = payload.text.strip()

    if normalized_channel not in SUPPORTED_CHANNELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Replies are supported on: "
                f"{', '.join(sorted(SUPPORTED_CHANNELS))}."
            ),
        )

    if not normalized_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer ID is required.",
        )

    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message cannot be empty.",
        )

    company_id = auth_service.resolve_company_id(current_user)

    # renew_reply_lease re-checks ownership and the assistant state inside a
    # transaction, so it is the single authority on whether this employee may
    # reply. Checking a previously-read snapshot as well would only add a race.
    try:
        conversation = conversation_control_service.renew_reply_lease(
            company_id=company_id,
            channel=normalized_channel,
            external_user_id=normalized_user_id,
            actor_user_id=int(current_user["id"]),
        )
    except ConversationOwnershipConflict as exc:
        raise _ownership_conflict(company_id, exc) from exc

    try:
        send_result = send_text(
            channel=normalized_channel,
            recipient_id=normalized_user_id,
            company_id=company_id,
            text=message_text,
        )
    except UnsupportedChannel as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if not send_result.get("ok"):
        logger.warning(
            "Manual reply rejected by provider on %s for company %s",
            normalized_channel,
            company_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Message was not sent: {extract_error(send_result)}",
        )

    employee_name = (
        current_user.get("full_name") or current_user.get("email") or "Employee"
    )

    saved_message = message_service.save_message(
        company_id=company_id,
        conversation_id=conversation.get("id"),
        channel=normalized_channel,
        external_user_id=normalized_user_id,
        direction="out",
        text=message_text,
        sender_type="employee",
        sender_user_id=int(current_user["id"]),
        source="dashboard",
        provider_message_id=(
            (send_result.get("response") or {}).get("message_id")
            if isinstance(send_result.get("response"), dict)
            else None
        ),
        metadata={"employee_name": employee_name},
    )

    try:
        conversation_control_service.record_employee_reply(
            company_id=company_id,
            channel=normalized_channel,
            external_user_id=normalized_user_id,
            actor_user_id=int(current_user["id"]),
            message_preview=message_text,
        )
    except ConversationOwnershipConflict:
        # The customer already has the message. Turning a bookkeeping race into
        # a 500 here would tell the employee their reply failed when it did not.
        logger.info(
            "Ownership changed while recording a sent reply for company %s",
            company_id,
        )

    return {
        "status": "sent",
        "channel": normalized_channel,
        "user_id": normalized_user_id,
        "message": saved_message,
        "provider_result": send_result,
    }
