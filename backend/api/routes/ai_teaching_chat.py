from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.ai_teaching_chat_service import ai_teaching_chat_service


router = APIRouter(prefix="/api/ai-teaching-chat", tags=["AI Teaching Chat"])


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _require_access(current_user: dict[str, Any]) -> int:
    company_id = auth_service.resolve_company_id(current_user)
    allowed = auth_service.has_permission(
        user_id=current_user.get("id"),
        company_id=company_id,
        permission_code="modules.ai_teaching_chat",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to use AI Teaching Chat. Ask an owner/admin to grant it from Roles & Permissions.",
        )
    return company_id


@router.get("")
def list_messages(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _require_access(current_user)
    return {"messages": ai_teaching_chat_service.list_messages(company_id=company_id)}


@router.post("")
def send_message(
    payload: SendMessageRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _require_access(current_user)
    try:
        return ai_teaching_chat_service.send_message(
            company_id=company_id, actor_user_id=current_user.get("id"), text=payload.text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
