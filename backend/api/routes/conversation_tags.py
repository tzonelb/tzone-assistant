from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.services.auth_service import auth_service, get_current_user
from backend.services.conversation_control_service import conversation_control_service


router = APIRouter(prefix="/api/conversation-tags", tags=["Conversation Tags"])


def _require_permission(
    current_user: dict[str, Any],
    company_id: int,
    permission_code: str,
    message: str,
) -> None:
    # owner role / super admin bypass handled inside has_permission.
    allowed = auth_service.has_permission(
        user_id=int(current_user["id"]),
        company_id=company_id,
        permission_code=permission_code,
        is_super_admin=bool(current_user.get("is_super_admin")),
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=message,
        )


class ConversationTagCreate(BaseModel):
    name: str
    color: str | None = None


class ConversationTagUpdate(BaseModel):
    name: str
    color: str | None = None


@router.get("")
def list_conversation_tags(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_permission(
        current_user,
        company_id,
        "conversations.view",
        "You do not have permission to view conversations.",
    )
    return {"items": conversation_control_service.list_tags(company_id)}


@router.post("")
def create_conversation_tag(
    payload: ConversationTagCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_permission(
        current_user,
        company_id,
        "conversations.reply",
        "You do not have permission to reply to conversations.",
    )
    try:
        item = conversation_control_service.create_tag(
            company_id=company_id,
            name=payload.name,
            color=payload.color,
            actor_user_id=int(current_user["id"]),
        )
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{tag_id}")
def update_conversation_tag(
    tag_id: int,
    payload: ConversationTagUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_permission(
        current_user,
        company_id,
        "conversations.reply",
        "You do not have permission to reply to conversations.",
    )
    try:
        item = conversation_control_service.update_tag(
            company_id=company_id,
            tag_id=tag_id,
            name=payload.name,
            color=payload.color,
        )
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
