"""Price quotes raised from a conversation.

Same permission shape as tickets/tasks: reading needs `conversations.view`,
creating and changing status need `conversations.reply` -- an employee who can
answer a customer can quote one a price, without needing a separate manage
grant for what is, functionally, one more kind of reply.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, require_permission
from backend.services.quote_service import QuoteError, quote_service


router = APIRouter(prefix="/api/quotes", tags=["Quotes"])


def _view(current_user: dict[str, Any] = Depends(require_permission("conversations.view"))) -> int:
    return auth_service.resolve_company_id(current_user)


def _reply(current_user: dict[str, Any] = Depends(require_permission("conversations.reply"))):
    return current_user


class QuoteItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: float = Field(default=1, ge=0)
    price: float = Field(default=0, ge=0)


class QuoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    amount: float | None = Field(default=None, ge=0)
    items: list[QuoteItem] = Field(default_factory=list)
    currency: str = Field(default="USD", max_length=8)
    notes: str | None = Field(default=None, max_length=4000)
    conversation_id: int | None = Field(default=None, ge=1)
    customer_id: int | None = Field(default=None, ge=1)


class QuoteStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=20)


@router.get("")
def list_quotes(
    conversation_id: int | None = Query(default=None, ge=1),
    company_id: int = Depends(_view),
) -> dict[str, Any]:
    return {"quotes": quote_service.list(company_id, conversation_id=conversation_id)}


@router.get("/{quote_id}")
def get_quote(quote_id: int, company_id: int = Depends(_view)) -> dict[str, Any]:
    quote = quote_service.get(company_id, quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail="That quote does not exist.")
    return quote


@router.post("", status_code=201)
def create_quote(
    payload: QuoteCreate, current_user: dict = Depends(_reply)
) -> dict[str, Any]:
    company_id = auth_service.resolve_company_id(current_user)
    try:
        return quote_service.create(
            company_id=company_id,
            title=payload.title,
            items=[item.model_dump() for item in payload.items],
            amount=payload.amount,
            currency=payload.currency,
            notes=payload.notes,
            conversation_id=payload.conversation_id,
            customer_id=payload.customer_id,
            created_by_user_id=current_user.get("id"),
        )
    except QuoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{quote_id}/status")
def update_quote_status(
    quote_id: int, payload: QuoteStatusUpdate, current_user: dict = Depends(_reply)
) -> dict[str, Any]:
    company_id = auth_service.resolve_company_id(current_user)
    try:
        return quote_service.set_status(
            company_id=company_id, quote_id=quote_id, status=payload.status
        )
    except QuoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{quote_id}")
def delete_quote(quote_id: int, current_user: dict = Depends(_reply)) -> dict[str, Any]:
    company_id = auth_service.resolve_company_id(current_user)
    if not quote_service.delete(company_id=company_id, quote_id=quote_id):
        raise HTTPException(status_code=404, detail="That quote does not exist.")
    return {"deleted": True}
