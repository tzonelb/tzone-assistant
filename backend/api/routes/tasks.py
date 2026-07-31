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
    _, company_id = context
    try:
        task_service.delete_task(company_id=company_id, task_id=task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
