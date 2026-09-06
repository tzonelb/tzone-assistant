"""Canned replies for the conversation composer.

Reading is part of answering a customer, so it rides on `conversations.view` --
anyone who can open the inbox can insert a saved reply. Writing the library is a
settings decision and takes `settings.manage`, so one employee cannot rewrite the
wording the whole company answers with.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import auth_service, client_ip, require_permission
from backend.services.saved_reply_service import (
    SavedReplyError,
    SavedReplyNotFound,
    saved_reply_service,
)


router = APIRouter(prefix="/api/saved-replies", tags=["Saved replies"])


class SavedReplyCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4000)
    department: str = Field(default="", max_length=80)


class SavedReplyUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=4000)
    department: str | None = Field(default=None, max_length=80)


def _context(current_user):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("conversations.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("settings.manage"))):
    return _context(current_user)


def _refused(error: SavedReplyError) -> HTTPException:
    code = (
        status.HTTP_404_NOT_FOUND
        if isinstance(error, SavedReplyNotFound)
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=str(error))


@router.get("")
def list_saved_replies(
    department: str | None = Query(default=None, max_length=80),
    context=Depends(view_context),
):
    current_user, company_id = context

    return {
        "items": saved_reply_service.list_for_company(
            company_id=company_id, department=department
        ),
        # The composer shows an "edit library" affordance only to someone who
        # can actually use it, rather than offering a control that 403s.
        "can_manage": auth_service.has_permission(
            user_id=int(current_user["id"]),
            company_id=company_id,
            permission_code="settings.manage",
            is_super_admin=bool(current_user.get("is_super_admin")),
        ),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_saved_reply(
    payload: SavedReplyCreateRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        reply = saved_reply_service.create(
            company_id=company_id,
            title=payload.title,
            body=payload.body,
            department=payload.department,
            created_by_user_id=int(current_user["id"]),
        )
    except SavedReplyError as exc:
        raise _refused(exc) from exc

    activity_service.record(
        company_id=company_id,
        action=Action.SETTINGS_UPDATED,
        category="settings",
        kind="change",
        actor_user_id=int(current_user["id"]),
        target_type="saved_reply",
        target_id=str(reply["id"]),
        summary=f"Saved reply created: {reply['title']}",
        ip_address=client_ip(request),
    )

    return reply


@router.patch("/{reply_id}")
def update_saved_reply(
    reply_id: int,
    payload: SavedReplyUpdateRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        reply = saved_reply_service.update(
            company_id=company_id,
            reply_id=reply_id,
            title=payload.title,
            body=payload.body,
            department=payload.department,
        )
    except SavedReplyError as exc:
        raise _refused(exc) from exc

    activity_service.record(
        company_id=company_id,
        action=Action.SETTINGS_UPDATED,
        category="settings",
        kind="change",
        actor_user_id=int(current_user["id"]),
        target_type="saved_reply",
        target_id=str(reply_id),
        summary=f"Saved reply updated: {reply['title']}",
        ip_address=client_ip(request),
    )

    return reply


@router.delete("/{reply_id}")
def delete_saved_reply(
    reply_id: int,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        saved_reply_service.delete(company_id=company_id, reply_id=reply_id)
    except SavedReplyError as exc:
        raise _refused(exc) from exc

    activity_service.record(
        company_id=company_id,
        action=Action.SETTINGS_UPDATED,
        category="settings",
        kind="change",
        actor_user_id=int(current_user["id"]),
        target_type="saved_reply",
        target_id=str(reply_id),
        summary="Saved reply deleted",
        ip_address=client_ip(request),
    )

    return {"success": True}
