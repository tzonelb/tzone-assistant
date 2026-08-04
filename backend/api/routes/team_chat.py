from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.team_chat import MessagePostRequest, RoomCreateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.team_chat_service import (
    TeamChatValidationError,
    team_chat_service,
)


router = APIRouter(prefix="/api/team-chat", tags=["Team Chat"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: three permission codes are seeded in database.py for this
# module -- "team_chat.view" (read rooms/messages), "team_chat.post"
# (send messages), and "team_chat.manage" (create/delete rooms). All are
# granted automatically to the built-in "owner" role
# (auth_service.has_permission special-cases role code 'owner' to always
# allow, the same way every other permission code in this codebase is
# wired to it) and can be attached to any other role from the Roles &
# Permissions admin screen.
def _require_chat_access(
    current_user: dict[str, Any],
    company_id: int,
    permission_code: str,
) -> None:
    allowed = auth_service.has_permission(
        user_id=current_user["id"],
        company_id=company_id,
        permission_code=permission_code,
        is_super_admin=bool(current_user.get("is_super_admin")),
    )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have team chat access.",
        )


@router.get("/rooms")
def list_rooms(context=Depends(current_context)):
    current_user, company_id = context
    _require_chat_access(current_user, company_id, "team_chat.view")

    return {"items": team_chat_service.list_rooms(company_id=company_id)}


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
def create_room(payload: RoomCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    _require_chat_access(current_user, company_id, "team_chat.manage")

    try:
        return team_chat_service.create_room(
            company_id=company_id,
            name=payload.name,
            description=payload.description,
            actor_user_id=current_user.get("id"),
        )
    except TeamChatValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/rooms/{room_id}")
def delete_room(room_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_chat_access(current_user, company_id, "team_chat.manage")

    try:
        deleted = team_chat_service.delete_room(
            company_id=company_id, room_id=room_id
        )
    except TeamChatValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Room not found")

    return {"message": "Room deleted"}


@router.get("/rooms/{room_id}/messages")
def list_messages(
    room_id: int,
    after_id: int | None = Query(default=None),
    before_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_chat_access(current_user, company_id, "team_chat.view")

    try:
        return team_chat_service.list_messages(
            company_id=company_id,
            room_id=room_id,
            after_id=after_id,
            before_id=before_id,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/messages", status_code=status.HTTP_201_CREATED)
def post_message(
    room_id: int,
    payload: MessagePostRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_chat_access(current_user, company_id, "team_chat.post")

    try:
        return team_chat_service.post_message(
            company_id=company_id,
            room_id=room_id,
            body=payload.body,
            sender_user_id=current_user.get("id"),
        )
    except TeamChatValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
