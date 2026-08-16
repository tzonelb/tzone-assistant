"""Request bodies for the tasks API.

None of these carry a ``company_id``. The company is resolved from the caller's
token in the router, so a client cannot name a company it does not belong to —
which is the only reason task ids being sequential and guessable is safe.

The literal unions are the same tuples the service validates against. Declaring
them here means a wrong value is refused with a readable 422 naming the allowed
options, instead of reaching the database as a status nothing filters on.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["open", "in_progress", "resolved", "closed"]
TaskPriority = Literal["low", "normal", "high", "urgent"]
TaskType = Literal[
    "support",
    "task",
    "follow_up",
    "maintenance",
    "delivery",
    "internal",
]

MAX_TITLE = 200
MAX_DETAILS = 4000
MAX_DEPARTMENT = 60
MAX_DUE_DATE = 40
MAX_COMMENT = 4000


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    problem: str | None = Field(default=None, max_length=MAX_DETAILS)
    task_type: TaskType = "task"
    priority: TaskPriority = "normal"
    status: TaskStatus = "open"
    due_date: str | None = Field(default=None, max_length=MAX_DUE_DATE)
    assigned_user_id: int | None = Field(default=None, ge=1)
    department: str | None = Field(default=None, max_length=MAX_DEPARTMENT)
    conversation_id: int | None = Field(default=None, ge=1)


class TaskUpdate(BaseModel):
    """Every field optional: the router sends only what the form changed.

    ``due_date`` and ``assigned_user_id`` are meaningfully nullable, so the
    router reads this model with ``exclude_unset`` — an explicit ``null``
    clears the value, an absent key leaves it alone.
    """

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    problem: str | None = Field(default=None, max_length=MAX_DETAILS)
    task_type: TaskType | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: str | None = Field(default=None, max_length=MAX_DUE_DATE)
    assigned_user_id: int | None = Field(default=None, ge=1)
    department: str | None = Field(default=None, max_length=MAX_DEPARTMENT)


class TaskStatusChange(BaseModel):
    status: TaskStatus


class TaskAssign(BaseModel):
    assigned_user_id: int | None = Field(default=None, ge=1)


class TaskCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_COMMENT)
