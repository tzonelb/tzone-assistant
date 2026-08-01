from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.routes.conversations import _company_employees
from backend.api.schemas.team_chat import TeamMessageCreateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.team_chat_service import team_chat_service


router = APIRouter(prefix="/api/team-chat", tags=["Team Chat"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


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
