from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.services.auth_service import auth_service, get_current_user
from backend.services.saved_reply_service import saved_reply_service
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/saved-replies", tags=["Saved Replies"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def _can_manage(current_user: dict[str, Any], company_id: int) -> bool:
    """Only owner/admin (or a super admin) may create, edit, or delete saved
    replies — employees may only browse and insert them into a conversation."""
    return auth_service.has_permission(
        user_id=current_user.get("id"),
        company_id=company_id,
        permission_code="users.manage",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )


class CreateSavedReplyRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=4000)
    department: str = ""


class UpdateSavedReplyRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    department: str | None = None


@router.get("")
def list_saved_replies(
    department: str = Query(default=""),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    return {
        "replies": saved_reply_service.list_for_company(company_id=company_id, department=department or None),
        "can_manage": _can_manage(current_user, company_id),
    }


@router.post("")
def create_saved_reply(
    payload: CreateSavedReplyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    if not _can_manage(current_user, company_id):
        raise HTTPException(status_code=403, detail="Only company admins can create saved replies.")
    try:
        return saved_reply_service.create(
            company_id=company_id, title=payload.title, body=payload.body,
            department=payload.department, actor_user_id=current_user.get("id"),
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
    if not _can_manage(current_user, company_id):
        raise HTTPException(status_code=403, detail="Only company admins can edit saved replies.")
    try:
        return saved_reply_service.update(
            company_id=company_id, reply_id=reply_id,
            title=payload.title, body=payload.body, department=payload.department,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Saved reply not found")


@router.delete("/{reply_id}")
def delete_saved_reply(
    reply_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    if not _can_manage(current_user, company_id):
        raise HTTPException(status_code=403, detail="Only company admins can delete saved replies.")
    try:
        saved_reply_service.delete(company_id=company_id, reply_id=reply_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Saved reply not found")
    return {"status": "deleted"}
