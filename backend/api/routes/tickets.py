"""Support tickets.

Previously this router had no authentication at all: anyone who knew the URL
could read every ticket from every company — customer phone numbers and problem
descriptions included — and create unlimited new ones.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, require_permission
from backend.services.ticket_service import ticket_service


router = APIRouter(prefix="/api/tickets", tags=["Tickets"])


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
