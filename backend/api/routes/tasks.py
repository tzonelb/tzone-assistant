from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.routes.conversations import _company_employees
from backend.api.schemas.tasks import TaskCreateRequest, TaskUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.task_service import PRIORITIES, STATUSES, TASK_TYPES, task_service


router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def _can_modify_task(current_user, company_id: int, task: dict) -> bool:
    """Anyone can create/view tasks (this is a shared team work list — the
    Tasks page and Team Chat's "Create task" button both rely on that), but
    changing or deleting one you didn't create and aren't assigned to is a
    real gap otherwise: any employee could silently reassign, complete,
    or delete a colleague's task. Allowed here: the task's own assignee,
    whoever created it, or an admin (users.manage — the same permission
    this codebase already uses for "manager-level" actions elsewhere)."""
    actor_id = int(current_user.get("id"))
    if task.get("assigned_user_id") == actor_id or task.get("created_by_user_id") == actor_id:
        return True
    return auth_service.has_permission(
        user_id=actor_id, company_id=company_id, permission_code="users.manage",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )


@router.get("/options")
def task_options(context=Depends(current_context)):
    """Reference data for the Tasks UI — the fixed status/priority
    pipelines plus the company's active employees (for the assignee
    picker). Mirrors customers.py's /options endpoint exactly."""
    _, company_id = context
    return {
        "statuses": STATUSES,
        "priorities": PRIORITIES,
        "task_types": TASK_TYPES,
        "employees": _company_employees(company_id),
    }


@router.post("")
def create_task(payload: TaskCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    try:
        return task_service.create_task(
            company_id=company_id,
            title=payload.title,
            description=payload.description,
            task_type=payload.task_type,
            priority=payload.priority,
            assigned_user_id=payload.assigned_user_id,
            customer_id=payload.customer_id,
            conversation_id=payload.conversation_id,
            due_at=payload.due_at,
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_tasks(
    status: str | None = Query(default=None),
    assigned_user_id: int | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    context=Depends(current_context),
):
    _, company_id = context
    return task_service.list_tasks(
        company_id=company_id,
        status=status,
        assigned_user_id=assigned_user_id,
        customer_id=customer_id,
    )


@router.get("/{task_id}")
def get_task(task_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        return task_service.get_task(company_id=company_id, task_id=task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{task_id}")
def update_task(task_id: int, payload: TaskUpdateRequest, context=Depends(current_context)):
    current_user, company_id = context
    try:
        existing = task_service.get_task(company_id=company_id, task_id=task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_modify_task(current_user, company_id, existing):
        raise HTTPException(status_code=403, detail="Only the assignee, the creator, or an admin can edit this task.")
    try:
        return task_service.update_task(
            company_id=company_id,
            task_id=task_id,
            values=payload.model_dump(exclude_unset=True),
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{task_id}")
def delete_task(task_id: int, context=Depends(current_context)):
    current_user, company_id = context
    try:
        existing = task_service.get_task(company_id=company_id, task_id=task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_modify_task(current_user, company_id, existing):
        raise HTTPException(status_code=403, detail="Only the assignee, the creator, or an admin can delete this task.")
    try:
        task_service.delete_task(company_id=company_id, task_id=task_id, actor_user_id=current_user.get("id"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
