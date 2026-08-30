"""Support and maintenance tickets a company opens with the T-ZONE team.

Distinct from `/api/tickets`, which is a company's own customers' cases. These
are addressed to the operator and are about the platform itself.

No permission beyond being signed in, deliberately. Anybody who can hit a bug
can report it: gating this behind an administrator's permission would mean the
person who actually saw the failure has to find somebody else to describe it.
The company is still resolved from the session, so a ticket can only ever be
filed against the filer's own company.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.support_ticket_service import (
    VALID_PRIORITIES,
    support_ticket_service,
)


router = APIRouter(prefix="/api/support-tickets", tags=["Support Tickets"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


class SupportTicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    # Bounded because it is free text on its way to a shared operator screen,
    # and because an unbounded description is a way to make one company's row
    # cost every other company's page load.
    description: str = Field(min_length=1, max_length=6000)
    priority: str = "normal"


@router.get("")
def list_support_tickets(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    return {
        "tickets": support_ticket_service.list_for_company(_company_id(current_user)),
        "priorities": list(VALID_PRIORITIES),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_support_ticket(
    payload: SupportTicketCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return support_ticket_service.create(
            company_id=_company_id(current_user),
            subject=payload.subject,
            description=payload.description,
            priority=payload.priority,
            actor_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
