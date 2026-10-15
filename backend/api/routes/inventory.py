"""Stock on hand and the movement ledger behind it.

Reads require ``inventory.view`` and writes require ``inventory.manage``,
mirroring ``catalogue.py``'s split for the same reason: recording a movement
changes a number other screens treat as fact, so it sits behind its own
permission rather than a general "can edit things" one. The company is never
taken from the request -- it is resolved from the caller's token.

``GET /products`` delegates to ``catalogue_service.list_products`` rather
than duplicating it, so the Inventory screen can list stock levels under
``inventory.view`` alone, without also requiring ``catalogue.view``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.inventory import ReorderPointUpdate, StockMovementCreate
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.activity_service import Action, activity_service
from backend.services.catalogue_service import catalogue_service
from backend.services.inventory_service import inventory_service


router = APIRouter(prefix="/api/inventory", tags=["Inventory"])


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("inventory.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("inventory.manage"))):
    return _context(current_user)


@router.get("/summary")
def get_summary(context=Depends(view_context)):
    _, company_id = context
    return inventory_service.stock_summary(company_id=company_id)


@router.get("/low-stock")
def list_low_stock(context=Depends(view_context)):
    _, company_id = context
    return {"items": inventory_service.low_stock_products(company_id=company_id)}


@router.get("/products")
def list_products(
    search: str | None = Query(default=None, max_length=200),
    stock: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return catalogue_service.list_products(
            company_id=company_id,
            search=search,
            stock=stock,
            status="active",
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/products/{product_id}/reorder-point")
def update_reorder_point(
    product_id: int,
    payload: ReorderPointUpdate,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    product = inventory_service.set_reorder_point(
        company_id=company_id,
        product_id=product_id,
        reorder_point=payload.reorder_point,
    )

    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.REORDER_POINT_UPDATED,
        category="inventory",
        target_type="product",
        target_id=product_id,
        summary=(
            f"Set the reorder point of {product.get('name')} to "
            f"{payload.reorder_point}"
            if payload.reorder_point is not None
            else f"Cleared the reorder point of {product.get('name')}"
        ),
        after={"reorder_point": product.get("reorder_point")},
        ip_address=client_ip(request),
    )

    return product


@router.get("/movements")
def list_movements(
    product_id: int | None = Query(default=None, ge=1),
    movement_type: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return inventory_service.list_movements(
            company_id=company_id,
            product_id=product_id,
            movement_type=movement_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/movements", status_code=status.HTTP_201_CREATED)
def create_movement(
    payload: StockMovementCreate,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        movement = inventory_service.record_movement(
            company_id=company_id,
            product_id=payload.product_id,
            movement_type=payload.movement_type,
            quantity_delta=payload.quantity_delta,
            reason=payload.reason,
            reference=payload.reference,
            user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not movement:
        raise HTTPException(status_code=404, detail="Product not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.STOCK_MOVEMENT_RECORDED,
        category="inventory",
        target_type="product",
        target_id=payload.product_id,
        summary=(
            f"Recorded a {movement.get('movement_type')} of "
            f"{movement.get('quantity_delta')} for {movement.get('product_name')}"
        ),
        after={
            "quantity_delta": movement.get("quantity_delta"),
            "quantity_after": movement.get("quantity_after"),
        },
        ip_address=client_ip(request),
    )

    return movement
