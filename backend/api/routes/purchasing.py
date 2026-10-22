"""Suppliers and the purchase orders raised against them.

Reads require ``purchasing.view`` and writes require ``purchasing.manage``,
the same split ``catalogue.py`` and ``inventory.py`` use and for the same
reason: receiving a line changes a number (stock on hand) other screens
treat as fact. The company is never taken from the request -- it is resolved
from the caller's token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.purchasing import (
    PurchaseOrderCreate,
    PurchaseOrderReceive,
    PurchaseOrderStatusUpdate,
    SupplierCreate,
    SupplierUpdate,
)
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.activity_service import Action, activity_service
from backend.services.purchasing_service import purchasing_service


router = APIRouter(prefix="/api/purchasing", tags=["Purchasing"])


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("purchasing.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("purchasing.manage"))):
    return _context(current_user)


# ----------------------------------------------------------------------
# Suppliers
# ----------------------------------------------------------------------


@router.get("/suppliers")
def list_suppliers(
    search: str | None = Query(default=None, max_length=200),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return purchasing_service.list_suppliers(
            company_id=company_id, search=search, status=status_filter,
            limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
def create_supplier(
    payload: SupplierCreate, request: Request, context=Depends(manage_context)
):
    current_user, company_id = context

    try:
        supplier = purchasing_service.create_supplier(
            company_id=company_id, data=payload.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.SUPPLIER_CREATED,
        category="purchasing",
        target_type="supplier",
        target_id=supplier.get("id"),
        summary=f"Added supplier {supplier.get('name')}",
        ip_address=client_ip(request),
    )

    return supplier


@router.get("/suppliers/{supplier_id}")
def get_supplier(supplier_id: int, context=Depends(view_context)):
    _, company_id = context
    supplier = purchasing_service.get_supplier(company_id=company_id, supplier_id=supplier_id)

    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found.")

    return supplier


@router.put("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: int, payload: SupplierUpdate, request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        supplier = purchasing_service.update_supplier(
            company_id=company_id, supplier_id=supplier_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.SUPPLIER_UPDATED,
        category="purchasing",
        target_type="supplier",
        target_id=supplier_id,
        summary=f"Edited supplier {supplier.get('name')}",
        ip_address=client_ip(request),
    )

    return supplier


@router.delete("/suppliers/{supplier_id}")
def archive_supplier(supplier_id: int, request: Request, context=Depends(manage_context)):
    """Retire a supplier. Past purchase orders keep naming it."""
    current_user, company_id = context

    previous = purchasing_service.get_supplier(company_id=company_id, supplier_id=supplier_id)

    if not purchasing_service.archive_supplier(company_id=company_id, supplier_id=supplier_id):
        raise HTTPException(status_code=404, detail="Supplier not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.SUPPLIER_ARCHIVED,
        category="purchasing",
        target_type="supplier",
        target_id=supplier_id,
        summary=f"Archived supplier {(previous or {}).get('name') or supplier_id}",
        ip_address=client_ip(request),
    )

    return {"success": True}


# ----------------------------------------------------------------------
# Purchase orders
# ----------------------------------------------------------------------


@router.get("/orders")
def list_orders(
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
    supplier_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return purchasing_service.list_orders(
            company_id=company_id, status=status_filter, supplier_id=supplier_id,
            limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    payload: PurchaseOrderCreate, request: Request, context=Depends(manage_context)
):
    current_user, company_id = context

    try:
        order = purchasing_service.create_order(
            company_id=company_id,
            supplier_id=payload.supplier_id,
            items=[item.model_dump() for item in payload.items],
            reference=payload.reference,
            notes=payload.notes,
            expected_date=payload.expected_date,
            user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.PURCHASE_ORDER_CREATED,
        category="purchasing",
        target_type="purchase_order",
        target_id=order.get("id"),
        summary=f"Raised a purchase order with {order.get('supplier_name')}",
        after={"total": order.get("total")},
        ip_address=client_ip(request),
    )

    return order


@router.get("/orders/{order_id}")
def get_order(order_id: int, context=Depends(view_context)):
    _, company_id = context
    order = purchasing_service.get_order(company_id=company_id, order_id=order_id)

    if not order:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

    return order


@router.put("/orders/{order_id}/status")
def update_order_status(
    order_id: int, payload: PurchaseOrderStatusUpdate, request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        order = purchasing_service.update_order_status(
            company_id=company_id, order_id=order_id, status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not order:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.PURCHASE_ORDER_STATUS_CHANGED,
        category="purchasing",
        target_type="purchase_order",
        target_id=order_id,
        summary=f"Set purchase order {order_id} to {order.get('status')}",
        after={"status": order.get("status")},
        ip_address=client_ip(request),
    )

    return order


@router.post("/orders/{order_id}/receive")
def receive_order_items(
    order_id: int, payload: PurchaseOrderReceive, request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        order = purchasing_service.receive_items(
            company_id=company_id,
            order_id=order_id,
            items=[item.model_dump() for item in payload.items],
            user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not order:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.PURCHASE_ORDER_RECEIVED,
        category="purchasing",
        target_type="purchase_order",
        target_id=order_id,
        summary=f"Received stock against purchase order {order_id}",
        after={"status": order.get("status")},
        ip_address=client_ip(request),
    )

    return order
