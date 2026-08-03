from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, List

from backend.services.auth_service import auth_service, get_current_user
from database.database import db


router = APIRouter(prefix="/tickets", tags=["Tickets"])


class TicketCreate(BaseModel):
    platform: str
    user_id: str
    language: Optional[str] = None
    iptv_username: Optional[str] = None
    device: Optional[str] = None
    os: Optional[str] = None
    app: Optional[str] = None
    problem: Optional[str] = None


def _company_id(current_user: dict) -> int:
    return auth_service.resolve_company_id(current_user)


def _require_permission(current_user: dict, company_id: int, permission_code: str) -> None:
    allowed = auth_service.has_permission(
        user_id=current_user["id"],
        company_id=company_id,
        permission_code=permission_code,
        is_super_admin=bool(current_user.get("is_super_admin")),
    )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to tickets.",
        )


@router.get("/")
def list_tickets(current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_permission(current_user, company_id, "conversations.view")

    db.create_tables()
    return db.get_tickets(company_id=company_id)


@router.post("/")
def create_ticket(ticket: TicketCreate, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_permission(current_user, company_id, "conversations.reply")

    db.create_tables()

    data = ticket.model_dump()
    data["company_id"] = company_id

    ticket_id = db.create_ticket(data)

    return {
        "message": "Ticket created",
        "ticket_id": ticket_id
    }


@router.get("/{ticket_id}")
def get_ticket(ticket_id: int, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_permission(current_user, company_id, "conversations.view")

    db.create_tables()

    ticket = db.get_ticket(ticket_id, company_id=company_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return ticket
