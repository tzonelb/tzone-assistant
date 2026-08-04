from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.tasks import TaskCreateRequest, TaskUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.task_service import (
    TaskConflictError,
    TaskValidationError,
    task_service,
)


router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: two dedicated permission codes are seeded in database.py for
# this module -- "tasks.view" (list/read) and "tasks.manage" (create/edit/
# delete). Both are granted automatically to the built-in "owner" role
# (auth_service.has_permission special-cases role code 'owner' to always
# allow, the same way every other permission code in this codebase is wired
# to it -- no explicit role_permissions row is needed) and can be attached to
# any other role from the Roles & Permissions admin screen like any other
# permission code.
def _require_task_access(
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
            detail="You do not have task management access.",
        )


@router.get("")
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    assignee_user_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_task_access(current_user, company_id, "tasks.view")

    return task_service.list_tasks(
        company_id=company_id,
        status=status_filter,
        assignee_user_id=assignee_user_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/assignable-users")
def list_assignable_users(context=Depends(current_context)):
    current_user, company_id = context
    _require_task_access(current_user, company_id, "tasks.view")

    return {"items": task_service.list_assignable_users(company_id=company_id)}


@router.get("/{task_id}")
def get_task(task_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_task_access(current_user, company_id, "tasks.view")

    try:
        return task_service.get_task(company_id=company_id, task_id=task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    _require_task_access(current_user, company_id, "tasks.manage")

    values = payload.model_dump(exclude_unset=True)
    try:
        return task_service.create_task(
            company_id=company_id,
            values=values,
            actor_user_id=current_user.get("id"),
        )
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{task_id}")
def update_task(
    task_id: int,
    payload: TaskUpdateRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_task_access(current_user, company_id, "tasks.manage")

    values = payload.model_dump(exclude_unset=True)
    # The concurrency token is not a task field -- pull it out before the
    # service filters the remaining editable fields.
    expected_updated_at = values.pop("expected_updated_at", None)

    try:
        return task_service.update_task(
            company_id=company_id,
            task_id=task_id,
            values=values,
            actor_user_id=current_user.get("id"),
            expected_updated_at=expected_updated_at,
        )
    except TaskConflictError as exc:
        # Mirror CustomersPage's 409 contract: a structured detail the UI
        # can act on, carrying the current record so it can offer a reload.
        try:
            current = task_service.get_task(company_id=company_id, task_id=task_id)
        except KeyError:
            current = None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "current": current},
        ) from exc
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{task_id}")
def delete_task(task_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_task_access(current_user, company_id, "tasks.manage")

    deleted = task_service.delete_task(company_id=company_id, task_id=task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"message": "Task deleted"}
