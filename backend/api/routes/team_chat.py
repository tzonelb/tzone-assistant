from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.routes.conversations import _company_employees
from backend.api.schemas.team_chat import (
    CreateDmRequest,
    CreateGroupRequest,
    RoomMessageCreateRequest,
    TeamMessageCreateRequest,
)
from backend.services.auth_service import auth_service, get_current_user
from backend.services.platform_admin_service import platform_admin_service
from backend.services.team_chat_service import team_chat_service
from backend.services.team_chat_rooms_service import team_chat_rooms_service


router = APIRouter(prefix="/api/team-chat", tags=["Team Chat"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    company_id = int(company_id)
    if not current_user.get("is_super_admin") and not platform_admin_service.is_module_enabled(company_id=company_id, module="team_chat"):
        raise HTTPException(status_code=403, detail="The Team Chat module is not enabled for this company.")
    return current_user, company_id


@router.get("/options")
def team_chat_options(context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    return {"employees": _company_employees(company_id)}


@router.get("")
def list_messages(
    before_id: int | None = Query(default=None),
    limit: int = Query(default=50),
    context=Depends(current_context),
):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    return team_chat_service.list_messages(company_id=company_id, before_id=before_id, limit=limit)


@router.post("")
def send_message(payload: TeamMessageCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    try:
        return team_chat_service.send_message(
            company_id=company_id,
            sender_user_id=current_user.get("id"),
            text=payload.text,
            mentioned_user_ids=payload.mentioned_user_ids,
            attachment_url=payload.attachment_url,
            attachment_type=payload.attachment_type,
            attachment_filename=payload.attachment_filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{message_id}")
def delete_message(message_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    try:
        team_chat_service.delete_message(
            company_id=company_id, message_id=message_id, actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"deleted": True}


# -- DMs and groups (additive — see team_chat_rooms_service.py) -------------

@router.get("/rooms")
def list_rooms(context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    return {"rooms": team_chat_rooms_service.list_rooms_for_user(company_id=company_id, user_id=current_user.get("id"))}


@router.post("/rooms/dm")
def create_dm(payload: CreateDmRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    try:
        return team_chat_rooms_service.get_or_create_dm(
            company_id=company_id, user_a=current_user.get("id"), user_b=payload.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/group")
def create_group(payload: CreateGroupRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    try:
        return team_chat_rooms_service.create_group(
            company_id=company_id, created_by_user_id=current_user.get("id"),
            name=payload.name, member_user_ids=payload.member_user_ids, department=payload.department,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/rooms/{room_id}/messages")
def list_room_messages(room_id: int, limit: int = Query(default=100), context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    try:
        return team_chat_rooms_service.list_room_messages(
            company_id=company_id, room_id=room_id, viewer_user_id=current_user.get("id"), limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/messages")
def send_room_message(room_id: int, payload: RoomMessageCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    try:
        return team_chat_rooms_service.send_room_message(
            company_id=company_id, room_id=room_id, sender_user_id=current_user.get("id"),
            text=payload.text, mentioned_user_ids=payload.mentioned_user_ids,
            attachment_url=payload.attachment_url, attachment_type=payload.attachment_type,
            attachment_filename=payload.attachment_filename,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/rooms/{room_id}/messages/{message_id}")
def delete_room_message(room_id: int, message_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.team_chat")
    try:
        team_chat_rooms_service.delete_room_message(
            company_id=company_id, room_id=room_id, message_id=message_id, actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"deleted": True}
