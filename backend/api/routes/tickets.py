"""Support tickets and the tasks screen built on the same table.

Previously this router had no authentication at all: anyone who knew the URL
could read every ticket from every company — customer phone numbers and problem
descriptions included — and create unlimited new ones.

Two routers live here because one table serves two audiences. ``router``
(``/api/tickets``) is the escalation view the inbox already uses and is gated on
the conversations permissions. ``tasks_router`` (``/api/tasks``) is the team's
task list, gated on ``tasks.view`` and ``tasks.manage``.

Neither of them takes a company from the client. It is resolved from the token,
and every employee id a request names is checked against that company's own
staff list before it is written, so a task cannot be assigned to a stranger.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.api.schemas.tasks import (
    TaskAssign,
    TaskCommentCreate,
    TaskCreate,
    TaskStatusChange,
    TaskUpdate,
)
from backend.services.auth_service import auth_service, require_permission
from backend.services.ticket_service import ticket_service


router = APIRouter(prefix="/api/tickets", tags=["Tickets"])

tasks_router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


class TicketCreate(BaseModel):
    platform: str = Field(min_length=1, max_length=40)
    user_id: str = Field(min_length=1, max_length=120)
    language: str | None = Field(default=None, max_length=10)
    department: str | None = Field(default=None, max_length=60)
    iptv_username: str | None = Field(default=None, max_length=120)
    device: str | None = Field(default=None, max_length=120)
    os: str | None = Field(default=None, max_length=120)
    app: str | None = Field(default=None, max_length=120)
    problem: str | None = Field(default=None, max_length=4000)


class TicketStatusUpdate(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed"]
    assigned_user_id: int | None = None


@router.get("")
def list_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    return ticket_service.list(
        company_id=company_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)

    ticket_id = ticket_service.create(
        company_id=company_id,
        data=payload.model_dump(),
    )

    return {"ticket_id": ticket_id, "status": "created"}


@router.get("/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)
    ticket = ticket_service.get(company_id=company_id, ticket_id=ticket_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    return ticket


@router.patch("/{ticket_id}")
def update_ticket_status(
    ticket_id: int,
    payload: TicketStatusUpdate,
    current_user: dict[str, Any] = Depends(require_permission("conversations.manage")),
):
    company_id = auth_service.resolve_company_id(current_user)

    updated = ticket_service.update_status(
        company_id=company_id,
        ticket_id=ticket_id,
        status=payload.status,
        assigned_user_id=payload.assigned_user_id,
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    return {"success": True}


# ----------------------------------------------------------------------
# Tasks
# ----------------------------------------------------------------------


# Which id column carries an employee, and what the resolved name is called in
# the response. Tasks and comments are decorated by the same pass.
NAME_FIELDS: dict[str, str] = {
    "assigned_user_id": "assigned_user_name",
    "created_by_user_id": "created_by_user_name",
    "author_user_id": "author_name",
}


def task_view_context(
    current_user: dict[str, Any] = Depends(require_permission("tasks.view")),
) -> tuple[dict[str, Any], int]:
    return current_user, int(auth_service.resolve_company_id(current_user))


def task_manage_context(
    current_user: dict[str, Any] = Depends(require_permission("tasks.manage")),
) -> tuple[dict[str, Any], int]:
    return current_user, int(auth_service.resolve_company_id(current_user))


def with_display_names(
    company_id: int, rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve every employee name in this response with one control query.

    Tasks live in the company's own encrypted database, which is a different
    file and cannot join to ``users``. Looking a name up per row would issue one
    control-plane query per task, so the whole page's ids are collected first
    and asked for together.
    """
    rows = list(rows)

    user_ids = [row.get(field) for row in rows for field in NAME_FIELDS]
    names = auth_service.user_display_names(company_id, user_ids)

    for row in rows:
        for field, label in NAME_FIELDS.items():
            if field not in row:
                continue

            value = row.get(field)
            row[label] = names.get(int(value)) if value else None

    return rows


def require_company_employee(company_id: int, user_id: Any) -> int | None:
    """Refuse an assignee who does not work for this company.

    User ids are sequential across the whole platform, so without this check a
    company could park its work on a stranger's name — and that stranger's task
    list would show a row from a company they have no account with.
    """
    if user_id in (None, "", 0):
        return None

    employee_ids = {
        int(employee["id"])
        for employee in auth_service.company_employees(company_id)
    }

    if int(user_id) not in employee_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That employee is not part of this company.",
        )

    return int(user_id)


@tasks_router.get("")
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    task_type: str | None = Query(default=None, max_length=40),
    priority: str | None = Query(default=None, max_length=20),
    assignee: int | None = Query(default=None, ge=1),
    unassigned: bool = Query(default=False),
    overdue: bool | None = Query(default=None),
    mine: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: tuple[dict[str, Any], int] = Depends(task_view_context),
):
    current_user, company_id = context

    assigned_user_id = int(current_user["id"]) if mine else assignee

    try:
        result = ticket_service.list_tasks(
            company_id=company_id,
            status=status_filter,
            task_type=task_type,
            priority=priority,
            assigned_user_id=assigned_user_id,
            unassigned=unassigned and not mine,
            overdue=overdue,
            search=search,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["items"] = with_display_names(company_id, result["items"])
    return result


@tasks_router.get("/options")
def task_options(context: tuple[dict[str, Any], int] = Depends(task_view_context)):
    """Everything the tasks screen needs to build its filters and its form.

    The employee list comes from the control database — the tenant database has
    no copy of it — and is what the assignment dropdown is populated from.
    """
    _, company_id = context

    employees = [
        {
            "id": int(employee["id"]),
            "display_name": employee["display_name"],
            "email": employee["email"],
            "role_name": employee["role_name"],
        }
        for employee in auth_service.company_employees(company_id)
    ]

    return {
        "statuses": list(ticket_service.ALLOWED_STATUS),
        "priorities": list(ticket_service.ALLOWED_PRIORITY),
        "task_types": list(ticket_service.ALLOWED_TASK_TYPE),
        "employees": employees,
    }


@tasks_router.get("/summary")
def task_summary(
    mine: bool = Query(default=False),
    context: tuple[dict[str, Any], int] = Depends(task_view_context),
):
    current_user, company_id = context

    return ticket_service.task_counts(
        company_id=company_id,
        assigned_user_id=int(current_user["id"]) if mine else None,
    )


@tasks_router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    context: tuple[dict[str, Any], int] = Depends(task_manage_context),
):
    current_user, company_id = context

    data = payload.model_dump()
    data["assigned_user_id"] = require_company_employee(
        company_id, data.get("assigned_user_id")
    )

    try:
        task = ticket_service.create_task(
            company_id=company_id,
            data=data,
            created_by_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return with_display_names(company_id, [task])[0]


@tasks_router.get("/{task_id}")
def get_task(
    task_id: int,
    context: tuple[dict[str, Any], int] = Depends(task_view_context),
):
    """The task and its whole thread, with one name lookup for both."""
    _, company_id = context

    try:
        task = ticket_service.get_task(company_id=company_id, task_id=task_id)
        comments = ticket_service.list_comments(
            company_id=company_id, task_id=task_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc

    with_display_names(company_id, [task, *comments])

    task["comments"] = comments
    return task


@tasks_router.put("/{task_id}")
def update_task(
    task_id: int,
    payload: TaskUpdate,
    context: tuple[dict[str, Any], int] = Depends(task_manage_context),
):
    _, company_id = context

    values = payload.model_dump(exclude_unset=True)

    if "assigned_user_id" in values:
        values["assigned_user_id"] = require_company_employee(
            company_id, values["assigned_user_id"]
        )

    try:
        task = ticket_service.update_task(
            company_id=company_id,
            task_id=task_id,
            values=values,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return with_display_names(company_id, [task])[0]


@tasks_router.patch("/{task_id}/status")
def change_task_status(
    task_id: int,
    payload: TaskStatusChange,
    context: tuple[dict[str, Any], int] = Depends(task_manage_context),
):
    _, company_id = context

    try:
        task = ticket_service.change_status(
            company_id=company_id,
            task_id=task_id,
            status=payload.status,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return with_display_names(company_id, [task])[0]


@tasks_router.post("/{task_id}/assign")
def assign_task(
    task_id: int,
    payload: TaskAssign,
    context: tuple[dict[str, Any], int] = Depends(task_manage_context),
):
    _, company_id = context

    assigned_user_id = require_company_employee(
        company_id, payload.assigned_user_id
    )

    try:
        task = ticket_service.assign_task(
            company_id=company_id,
            task_id=task_id,
            assigned_user_id=assigned_user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc

    return with_display_names(company_id, [task])[0]


@tasks_router.get("/{task_id}/comments")
def list_task_comments(
    task_id: int,
    context: tuple[dict[str, Any], int] = Depends(task_view_context),
):
    _, company_id = context

    try:
        comments = ticket_service.list_comments(
            company_id=company_id, task_id=task_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc

    return {"items": with_display_names(company_id, comments)}


@tasks_router.post("/{task_id}/comments", status_code=status.HTTP_201_CREATED)
def add_task_comment(
    task_id: int,
    payload: TaskCommentCreate,
    context: tuple[dict[str, Any], int] = Depends(task_manage_context),
):
    current_user, company_id = context

    try:
        comment = ticket_service.add_comment(
            company_id=company_id,
            task_id=task_id,
            author_user_id=int(current_user["id"]),
            body=payload.body,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return with_display_names(company_id, [comment])[0]
