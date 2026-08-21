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
    MessageCreateRequest,
    MessageEditRequest,
)
from backend.services.auth_service import auth_service, require_permission
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
