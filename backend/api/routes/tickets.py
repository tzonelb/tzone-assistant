from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.services.auth_service import auth_service, get_current_user
from database.database import db


router = APIRouter(prefix="/tickets", tags=["Tickets"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


class TicketCreate(BaseModel):
    platform: str
    user_id: str
    language: Optional[str] = None
    iptv_username: Optional[str] = None
    device: Optional[str] = None
    os: Optional[str] = None
    app: Optional[str] = None
    problem: Optional[str] = None


@router.get("/")
def list_tickets(current_user: dict[str, Any] = Depends(get_current_user)):
    db.create_tables()
    return db.get_tickets(company_id=_company_id(current_user))


@router.post("/")
def create_ticket(ticket: TicketCreate, current_user: dict[str, Any] = Depends(get_current_user)):
    db.create_tables()

    payload = ticket.model_dump()
    payload["company_id"] = _company_id(current_user)
    ticket_id = db.create_ticket(payload)

    return {
        "message": "Ticket created",
        "ticket_id": ticket_id
    }


@router.get("/{ticket_id}")
def get_ticket(ticket_id: int, current_user: dict[str, Any] = Depends(get_current_user)):
    db.create_tables()

    ticket = db.get_ticket(ticket_id)

    if not ticket or ticket.get("company_id") != _company_id(current_user):
        raise HTTPException(status_code=404, detail="Ticket not found")

    return ticket