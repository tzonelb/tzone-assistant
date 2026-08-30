"""Internal team chat.

Every endpoint resolves the company from the caller's token and enforces
`team_chat.use`. Channel access itself is decided inside
`team_chat_service`: a private channel a caller is not a member of raises
`ChannelNotFound`, which is answered with 404 rather than 403 on purpose, so the
API never confirms that a private discussion exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from backend.api.schemas.team_chat import (
    ChannelCreateRequest,
    ChannelMemberRequest,
    CreateDmRequest,
    CreateGroupRequest,
    MessageCreateRequest,
    MessageEditRequest,
    StreamMessageCreateRequest,
)
from backend.services.auth_service import auth_service, require_permission
from backend.services.media_upload_service import (
    MediaUploadError,
    media_upload_service,
)
from backend.services.stream_access import may_continue
from backend.services.team_chat_service import (
    ChannelNameTaken,
    ChannelNotFound,
    NotChannelMember,
    NotMessageAuthor,
    team_chat_service,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/team-chat", tags=["Team Chat"])

PERMISSION = "team_chat.use"
LIVE_POLL_SECONDS = 3

# Everything the service raises for a request the caller may not make. Mapped to
# a status code by `_handle` in one place, so no endpoint can accidentally
# answer 403 where the privacy rule requires a 404.
TeamChatRouteErrors = (
    ChannelNotFound,
    ChannelNameTaken,
    NotChannelMember,
    NotMessageAuthor,
    ValueError,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _decorate_messages(
    company_id: int, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach author and mention names, resolved in one control-plane query."""
    user_ids: list[int] = []

    for message in messages:
        user_ids.append(int(message["author_user_id"]))
        user_ids.extend(int(item) for item in message.get("mentions") or [])

    names = auth_service.user_display_names(company_id, user_ids)

    for message in messages:
        author_id = int(message["author_user_id"])
        message["author_name"] = names.get(author_id, f"User {author_id}")
        message["mention_names"] = {
            str(item): names.get(int(item), f"User {item}")
            for item in message.get("mentions") or []
        }

    return messages


def _decorate_members(
    company_id: int, members: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    names = auth_service.user_display_names(
        company_id, [int(member["user_id"]) for member in members]
    )

    for member in members:
        user_id = int(member["user_id"])
        member["display_name"] = names.get(user_id, f"User {user_id}")

    return members


def _handle(error: Exception) -> HTTPException:
    if isinstance(error, ChannelNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ChannelNameTaken):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, (NotChannelMember, NotMessageAuthor)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


# ----------------------------------------------------------------------
# Channels
# ----------------------------------------------------------------------


@router.get("/overview")
def team_chat_overview(
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    """Everything the screen needs on first paint."""
    company_id = auth_service.resolve_company_id(current_user)
    user_id = int(current_user["id"])

    channels = team_chat_service.list_channels(company_id=company_id, user_id=user_id)

    return {
        "status": "ok",
        "channels": channels,
        "directory": team_chat_service.directory(company_id),
        "current_user_id": user_id,
        "unread_total": sum(int(channel["unread_count"]) for channel in channels),
    }


@router.get("/channels")
def list_channels(
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    return {
        "status": "ok",
        "items": team_chat_service.list_channels(
            company_id=company_id, user_id=int(current_user["id"])
        ),
    }


@router.post("/channels", status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelCreateRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        return team_chat_service.create_channel(
            company_id=company_id,
            user_id=int(current_user["id"]),
            name=payload.name,
            topic=payload.topic,
            is_private=payload.is_private,
            member_user_ids=payload.member_user_ids,
        )
    except (ChannelNameTaken, ValueError) as error:
        raise _handle(error) from error


@router.get("/unread")
def unread_summary(
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)
    summary = team_chat_service.unread_counts(
        company_id=company_id, user_id=int(current_user["id"])
    )

    return {
        "status": "ok",
        "channels": {str(key): value for key, value in summary["channels"].items()},
        "total": summary["total"],
    }


@router.get("/directory")
def mention_directory(
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)
    return {"status": "ok", "items": team_chat_service.directory(company_id)}


@router.get("/channels/{channel_id}")
def get_channel(
    channel_id: int,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        channel = team_chat_service.get_channel(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
        )
        members = team_chat_service.list_members(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    channel["members"] = _decorate_members(company_id, members)
    return channel


@router.post("/channels/{channel_id}/join")
def join_channel(
    channel_id: int,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        return team_chat_service.join_channel(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error


@router.post("/channels/{channel_id}/leave")
def leave_channel(
    channel_id: int,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        left = team_chat_service.leave_channel(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return {"status": "left" if left else "not_a_member", "channel_id": channel_id}


@router.post("/channels/{channel_id}/members")
def add_channel_member(
    channel_id: int,
    payload: ChannelMemberRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    # The invitee must be an employee of this company. Without this check a
    # caller could name any user id in the platform.
    allowed = {int(employee["id"]) for employee in team_chat_service.directory(company_id)}

    if int(payload.user_id) not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That person is not an employee of this company.",
        )

    try:
        return team_chat_service.add_member(
            company_id=company_id,
            actor_user_id=int(current_user["id"]),
            channel_id=channel_id,
            user_id=int(payload.user_id),
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error


@router.get("/channels/{channel_id}/members")
def list_channel_members(
    channel_id: int,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        members = team_chat_service.list_members(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return {"status": "ok", "items": _decorate_members(company_id, members)}


@router.post("/channels/{channel_id}/read")
def mark_channel_read(
    channel_id: int,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        return team_chat_service.mark_read(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error


# ----------------------------------------------------------------------
# Messages
# ----------------------------------------------------------------------


@router.get("/channels/{channel_id}/messages")
def list_messages(
    channel_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        page = team_chat_service.list_messages(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
            limit=limit,
            before_id=before_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    page["items"] = _decorate_messages(company_id, page["items"])
    page["status"] = "ok"
    return page


@router.post("/channels/{channel_id}/messages", status_code=status.HTTP_201_CREATED)
def post_message(
    channel_id: int,
    payload: MessageCreateRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        message = team_chat_service.post_message(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=channel_id,
            body=payload.body,
            linked_conversation_id=payload.linked_conversation_id,
            author_name=current_user.get("full_name") or current_user.get("email"),
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return _decorate_messages(company_id, [message])[0]


@router.patch("/messages/{message_id}")
def edit_message(
    message_id: int,
    payload: MessageEditRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        message = team_chat_service.edit_message(
            company_id=company_id,
            user_id=int(current_user["id"]),
            message_id=message_id,
            body=payload.body,
            author_name=current_user.get("full_name") or current_user.get("email"),
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return _decorate_messages(company_id, [message])[0]


# ----------------------------------------------------------------------
# The composer's view: one company stream, plus direct messages and groups
#
# The same channels, the same membership and the same visibility rule as
# everything above — read through the shape the redesigned screen speaks:
# `sender_user_id`/`sender_name`/`text` rather than `author_user_id`/`body`,
# and a room titled by who is in it rather than by its storage key. Kept as a
# translation in this file rather than a rename in the service, so the older
# screen and the API's existing consumers keep the names they were built on.
# ----------------------------------------------------------------------


def _stream_shape(
    company_id: int, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            "id": int(message["id"]),
            "sender_user_id": int(message["author_user_id"]),
            "sender_name": message.get("author_name"),
            "text": message.get("body") or "",
            "mentioned_user_ids": message.get("mentions") or [],
            "attachment_url": message.get("attachment_url"),
            "attachment_type": message.get("attachment_type"),
            "attachment_filename": message.get("attachment_filename"),
            "created_at": message.get("created_at"),
            "edited_at": message.get("edited_at"),
        }
        for message in _decorate_messages(company_id, messages)
    ]


def _checked_attachment(company_id: int, url: str | None) -> str | None:
    """An attachment must name a file this company already uploaded.

    Without this an employee could point a message at any address and have the
    platform render it inside the company's own chat — and could name another
    company's upload path, which is the leak this check exists for. The same
    rule `manual_messages` applies to a customer-bound attachment.
    """
    if not url:
        return None

    prefix = f"/api/media/{int(company_id)}/"

    if not url.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attach a file uploaded to this workspace.",
        )

    try:
        media_upload_service.path_for(
            company_id=int(company_id), stored_name=url[len(prefix):]
        )
    except MediaUploadError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error

    return url


def _post(
    *,
    company_id: int,
    user_id: int,
    channel_id: int,
    payload: StreamMessageCreateRequest,
) -> dict[str, Any]:
    try:
        message = team_chat_service.post_message(
            company_id=company_id,
            user_id=user_id,
            channel_id=channel_id,
            body=payload.text,
            mentioned_user_ids=payload.mentioned_user_ids,
            attachment_url=_checked_attachment(company_id, payload.attachment_url),
            attachment_type=payload.attachment_type,
            attachment_filename=payload.attachment_filename,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return _stream_shape(company_id, [message])[0]


@router.get("/options")
def team_chat_options(
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    """Who can be messaged and mentioned."""
    company_id = auth_service.resolve_company_id(current_user)

    return {
        "status": "ok",
        "employees": team_chat_service.directory(company_id),
    }


@router.get("/stream")
def list_stream_messages(
    limit: int = Query(default=100, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)
    user_id = int(current_user["id"])
    channel_id = team_chat_service.company_stream_id(
        company_id=company_id, user_id=user_id
    )

    page = team_chat_service.list_messages(
        company_id=company_id,
        user_id=user_id,
        channel_id=channel_id,
        limit=limit,
        before_id=before_id,
    )

    return {
        "status": "ok",
        "channel_id": channel_id,
        "items": _stream_shape(company_id, page["items"]),
        "has_more": page["has_more"],
        "next_before_id": page["next_before_id"],
    }


@router.post("/stream", status_code=status.HTTP_201_CREATED)
def send_stream_message(
    payload: StreamMessageCreateRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)
    user_id = int(current_user["id"])

    return _post(
        company_id=company_id,
        user_id=user_id,
        channel_id=team_chat_service.company_stream_id(
            company_id=company_id, user_id=user_id
        ),
        payload=payload,
    )


@router.delete("/messages/{message_id}")
def delete_message(
    message_id: int,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        team_chat_service.delete_message(
            company_id=company_id,
            user_id=int(current_user["id"]),
            message_id=message_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return {"status": "ok", "deleted": True}


@router.get("/rooms")
def list_rooms(
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    return {
        "status": "ok",
        "rooms": team_chat_service.list_rooms(
            company_id=company_id, user_id=int(current_user["id"])
        ),
    }


@router.post("/rooms/dm", status_code=status.HTTP_201_CREATED)
def create_dm(
    payload: CreateDmRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        return team_chat_service.get_or_create_dm(
            company_id=company_id,
            user_id=int(current_user["id"]),
            other_user_id=payload.user_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error


@router.post("/rooms/group", status_code=status.HTTP_201_CREATED)
def create_group(
    payload: CreateGroupRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        return team_chat_service.create_group(
            company_id=company_id,
            user_id=int(current_user["id"]),
            name=payload.name,
            member_user_ids=payload.member_user_ids,
            department=payload.department,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error


@router.get("/rooms/{room_id}/messages")
def list_room_messages(
    room_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        page = team_chat_service.list_messages(
            company_id=company_id,
            user_id=int(current_user["id"]),
            channel_id=room_id,
            limit=limit,
            before_id=before_id,
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return {
        "status": "ok",
        "items": _stream_shape(company_id, page["items"]),
        "has_more": page["has_more"],
        "next_before_id": page["next_before_id"],
    }


@router.post("/rooms/{room_id}/messages", status_code=status.HTTP_201_CREATED)
def send_room_message(
    room_id: int,
    payload: StreamMessageCreateRequest,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    company_id = auth_service.resolve_company_id(current_user)

    return _post(
        company_id=company_id,
        user_id=int(current_user["id"]),
        channel_id=room_id,
        payload=payload,
    )


@router.delete("/rooms/{room_id}/messages/{message_id}")
def delete_room_message(
    room_id: int,
    message_id: int,
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    """`room_id` is in the path because the screen knows it; the message's own
    channel is what decides access, so a mismatched pair is refused rather than
    quietly deleting from the other room."""
    company_id = auth_service.resolve_company_id(current_user)
    user_id = int(current_user["id"])

    try:
        room = team_chat_service.get_room(
            company_id=company_id, user_id=user_id, room_id=room_id
        )
        message = team_chat_service.get_message(
            company_id=company_id, user_id=user_id, message_id=message_id
        )

        if int(message["channel_id"]) != int(room["id"]):
            raise ChannelNotFound("Message not found.")

        team_chat_service.delete_message(
            company_id=company_id, user_id=user_id, message_id=message_id
        )
    except TeamChatRouteErrors as error:
        raise _handle(error) from error

    return {"status": "ok", "deleted": True}


# ----------------------------------------------------------------------
# Live stream
# ----------------------------------------------------------------------


@router.get("/live/events")
async def live_team_chat_events(
    channel_id: int | None = Query(default=None, ge=1),
    current_user: dict[str, Any] = Depends(require_permission(PERMISSION)),
):
    """Push new team messages to an open screen.

    The poll compares a cheap aggregate signature scoped to the channels this
    user may see, and only builds a payload when something changed. Every
    database call runs in a worker thread, because blocking here stalls every
    other request on the server.
    """
    company_id = auth_service.resolve_company_id(current_user)
    user_id = int(current_user["id"])

    async def event_stream():
        last_signature = ""

        while True:
            # Re-checked every pass, not only when the connection opened.
            # See `backend/services/stream_access.py`: a dependency runs once,
            # and this loop outlives it by hours.
            if not await run_in_threadpool(may_continue, current_user):
                yield "event: access_ended\ndata: {}\n\n"

                return

            try:
                signature = await run_in_threadpool(
                    lambda: team_chat_service.live_signature(
                        company_id=company_id, user_id=user_id
                    )
                )
            except Exception:  # noqa: BLE001
                logger.exception("Live team chat signature failed")
                yield ": error\n\n"
                await asyncio.sleep(LIVE_POLL_SECONDS)
                continue

            if signature != last_signature:
                last_signature = signature

                channels = await run_in_threadpool(
                    lambda: team_chat_service.list_channels(
                        company_id=company_id, user_id=user_id
                    )
                )

                messages: list[dict[str, Any]] = []

                if channel_id is not None:
                    try:
                        page = await run_in_threadpool(
                            lambda: team_chat_service.list_messages(
                                company_id=company_id,
                                user_id=user_id,
                                channel_id=int(channel_id),
                                limit=50,
                            )
                        )
                        messages = await run_in_threadpool(
                            _decorate_messages, company_id, page["items"]
                        )
                    except ChannelNotFound:
                        # The channel was deleted, or the viewer was removed
                        # from it. The stream keeps running for the rest.
                        messages = []

                payload = {
                    "type": "team_chat_updated",
                    "channels": channels,
                    "channel_id": channel_id,
                    "messages": messages,
                    "unread_total": sum(
                        int(channel["unread_count"]) for channel in channels
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                yield (
                    "event: team_chat_updated\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(LIVE_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
