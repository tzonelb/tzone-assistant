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
from channels.sender import (
    SUPPORTED_CHANNELS,
    UnsupportedChannel,
    extract_error,
    normalize_channel,
    send_text,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversation Messages"])


class ManualReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ManualReplyResponse(BaseModel):
    status: Literal["sent"]
    channel: str
    user_id: str
    message: dict[str, Any]
    provider_result: dict[str, Any]


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
        owner_id = exc.owner_user_id
        names = auth_service.user_display_names(
            company_id, [owner_id] if owner_id else []
        )
        owner_name = names.get(int(owner_id)) if owner_id else None

        raise HTTPException(
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
        ) from exc

    try:
        send_result = send_text(
            channel=normalized_channel,
            recipient_id=normalized_user_id,
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
