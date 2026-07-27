from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.saved_reply_service import saved_reply_service


router = APIRouter(prefix="/api/saved-replies", tags=["Saved Replies"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


class CreateSavedReplyRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=4000)


class UpdateSavedReplyRequest(BaseModel):
    title: str | None = None
    body: str | None = None


@router.get("")
def list_saved_replies(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    return {"replies": saved_reply_service.list_for_company(company_id=company_id)}


@router.post("")
def create_saved_reply(
    payload: CreateSavedReplyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return saved_reply_service.create(
            company_id=company_id, title=payload.title, body=payload.body,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{reply_id}")
def update_saved_reply(
    reply_id: int,
    payload: UpdateSavedReplyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return saved_reply_service.update(
            company_id=company_id, reply_id=reply_id, title=payload.title, body=payload.body,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Saved reply not found")


@router.delete("/{reply_id}")
def delete_saved_reply(
    reply_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        saved_reply_service.delete(company_id=company_id, reply_id=reply_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Saved reply not found")
    return {"status": "deleted"}
