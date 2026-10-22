"""Customer orders and their fulfilment.

Reads require ``orders.view`` and writes require ``orders.manage``, the same
split every other commerce module in this cluster uses: fulfilling a line
changes stock on hand, a number other screens treat as fact. The company is
never taken from the request -- it is resolved from the caller's token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.orders import OrderCreate, OrderFulfil, OrderStatusUpdate
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.activity_service import Action, activity_service
from backend.services.order_service import order_service


router = APIRouter(prefix="/api/orders", tags=["Orders"])


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("orders.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("orders.manage"))):
    return _context(current_user)


@router.get("")
def list_orders(
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
    customer_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return order_service.list_orders(
            company_id=company_id, status=status_filter, customer_id=customer_id,
            limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, request: Request, context=Depends(manage_context)):
    current_user, company_id = context

    try:
        order = order_service.create_order(
            company_id=company_id,
            customer_id=payload.customer_id,
            items=[item.model_dump() for item in payload.items],
            quote_id=payload.quote_id,
            conversation_id=payload.conversation_id,
            reference=payload.reference,
            notes=payload.notes,
            user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.ORDER_CREATED,
        category="orders",
        target_type="order",
        target_id=order.get("id"),
        summary=f"Created an order for {order.get('customer_name')}",
        after={"total": order.get("total")},
        ip_address=client_ip(request),
    )

    return order


@router.get("/{order_id}")
def get_order(order_id: int, context=Depends(view_context)):
    _, company_id = context
    order = order_service.get_order(company_id=company_id, order_id=order_id)

    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    return order


@router.put("/{order_id}/status")
def update_order_status(
    order_id: int, payload: OrderStatusUpdate, request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        order = order_service.update_order_status(
            company_id=company_id, order_id=order_id, status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.ORDER_STATUS_CHANGED,
        category="orders",
        target_type="order",
        target_id=order_id,
        summary=f"Set order {order_id} to {order.get('status')}",
        after={"status": order.get("status")},
        ip_address=client_ip(request),
    )

    return order


@router.post("/{order_id}/fulfil")
def fulfil_order_items(
    order_id: int, payload: OrderFulfil, request: Request, context=Depends(manage_context)
):
    current_user, company_id = context

    try:
        order = order_service.fulfil_items(
            company_id=company_id,
            order_id=order_id,
            items=[item.model_dump() for item in payload.items],
            user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.ORDER_FULFILLED,
        category="orders",
        target_type="order",
        target_id=order_id,
        summary=f"Fulfilled stock against order {order_id}",
        after={"status": order.get("status")},
        ip_address=client_ip(request),
    )

    return order
