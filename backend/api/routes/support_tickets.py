from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.support_ticket_service import support_ticket_service


router = APIRouter(prefix="/api/support-tickets", tags=["Support Tickets"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


class CreateSupportTicketRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=6000)
    priority: str = "normal"


@router.get("")
def list_support_tickets(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    support_ticket_service.ensure_schema()
    company_id = _company_id(current_user)
    return {"tickets": support_ticket_service.list_for_company(company_id=company_id)}


@router.post("")
def create_support_ticket(
    payload: CreateSupportTicketRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    support_ticket_service.ensure_schema()
    company_id = _company_id(current_user)
    try:
        return support_ticket_service.create(
            company_id=company_id,
            subject=payload.subject,
            description=payload.description,
            priority=payload.priority,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
