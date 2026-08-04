from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.scheduler import (
    ScheduledPostCreateRequest,
    ScheduledPostStatusRequest,
    ScheduledPostUpdateRequest,
)
from backend.services.auth_service import auth_service, get_current_user
from backend.services.scheduler_service import (
    SchedulerConflictError,
    SchedulerValidationError,
    scheduler_service,
)


router = APIRouter(prefix="/api/scheduler", tags=["Scheduler"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: two dedicated permission codes are seeded in database.py for
# this module -- "scheduler.view" (list/read) and "scheduler.manage"
# (create/edit/approve/publish/cancel/delete). Both are granted
# automatically to the built-in "owner" role (auth_service.has_permission
# special-cases role code 'owner' to always allow, the same way every
# other permission code in this codebase is wired to it) and can be
# attached to any other role from the Roles & Permissions admin screen.
def _require_scheduler_access(
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
            detail="You do not have scheduler management access.",
        )


def _conflict_response(exc: SchedulerConflictError, *, company_id: int, post_id: int):
    try:
        current = scheduler_service.get_post(company_id=company_id, post_id=post_id)
    except KeyError:
        current = None
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"message": str(exc), "current": current},
    )


@router.get("")
def list_posts(
    status_filter: str | None = Query(default=None, alias="status"),
    channel: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_scheduler_access(current_user, company_id, "scheduler.view")

    return scheduler_service.list_posts(
        company_id=company_id,
        status=status_filter,
        channel=channel,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/{post_id}")
def get_post(post_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_scheduler_access(current_user, company_id, "scheduler.view")

    try:
        return scheduler_service.get_post(company_id=company_id, post_id=post_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_post(payload: ScheduledPostCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    _require_scheduler_access(current_user, company_id, "scheduler.manage")

    values = payload.model_dump(exclude_unset=True)
    try:
        return scheduler_service.create_post(
            company_id=company_id,
            values=values,
            actor_user_id=current_user.get("id"),
        )
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{post_id}")
def update_post(
    post_id: int,
    payload: ScheduledPostUpdateRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_scheduler_access(current_user, company_id, "scheduler.manage")

    values = payload.model_dump(exclude_unset=True)
    expected_updated_at = values.pop("expected_updated_at", None)

    try:
        return scheduler_service.update_post(
            company_id=company_id,
            post_id=post_id,
            values=values,
            expected_updated_at=expected_updated_at,
        )
    except SchedulerConflictError as exc:
        raise _conflict_response(exc, company_id=company_id, post_id=post_id) from exc
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{post_id}/status")
def change_status(
    post_id: int,
    payload: ScheduledPostStatusRequest,
    context=Depends(current_context),
):
    """Move a post through the draft -> scheduled (approve) -> published
    (manual publish confirmation) / cancelled workflow. See
    scheduler_service.py's module docstring: this does not auto-publish
    to any external platform -- 'published' is a manual confirmation."""
    current_user, company_id = context
    _require_scheduler_access(current_user, company_id, "scheduler.manage")

    try:
        return scheduler_service.transition_status(
            company_id=company_id,
            post_id=post_id,
            new_status=payload.status,
            actor_user_id=current_user.get("id"),
            expected_updated_at=payload.expected_updated_at,
        )
    except SchedulerConflictError as exc:
        raise _conflict_response(exc, company_id=company_id, post_id=post_id) from exc
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{post_id}")
def delete_post(post_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_scheduler_access(current_user, company_id, "scheduler.manage")

    deleted = scheduler_service.delete_post(company_id=company_id, post_id=post_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Post not found")

    return {"message": "Post deleted"}
