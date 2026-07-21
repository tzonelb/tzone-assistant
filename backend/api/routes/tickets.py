from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

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


@router.get("/")
def list_tickets():
    db.create_tables()
    return db.get_tickets()


@router.post("/")
def create_ticket(ticket: TicketCreate):
    db.create_tables()

    ticket_id = db.create_ticket(ticket.model_dump())

    return {
        "message": "Ticket created",
        "ticket_id": ticket_id
    }


@router.get("/{ticket_id}")
def get_ticket(ticket_id: int):
    db.create_tables()

    ticket = db.get_ticket(ticket_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return ticket