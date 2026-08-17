"""The product catalogue the company sells from — and the assistant quotes from.

Reads require ``catalogue.view`` and writes require ``catalogue.manage``. The
company is never taken from the request: it is resolved from the caller's token,
so a client cannot list, price or delete another company's products by naming
its id.

Writes here change what the assistant tells customers about price and stock,
which is why they sit behind their own permission rather than a general "can
edit things" one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.catalogue import (
    ProductCategoryCreate,
    ProductCategoryUpdate,
    ProductCreate,
    ProductUpdate,
)
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.activity_service import Action, activity_service
from backend.services.catalogue_service import catalogue_service


router = APIRouter(prefix="/api/catalogue", tags=["Catalogue"])


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("catalogue.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("catalogue.manage"))):
    return _context(current_user)


def _record_product_change(
    current_user: dict[str, Any],
    *,
    company_id: int,
    previous: dict[str, Any],
    product: dict[str, Any],
    request: Request,
) -> None:
    """File an edit, and file a price change as its own event.

    A price is not one field among many here: the assistant states it to
    customers as a confirmed fact, and a wrong one is a promise the business
    then has to keep. Separating it means an owner can filter the log down to
    exactly the changes that reach a customer's screen.
    """
    changed_price = previous.get("price") != product.get("price")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=(
            Action.PRODUCT_PRICE_CHANGED if changed_price else Action.PRODUCT_UPDATED
        ),
        category="catalogue",
        target_type="product",
        target_id=product.get("id") or previous.get("id"),
        summary=(
            f"Changed the price of {product.get('name')} from "
            f"{previous.get('price')} to {product.get('price')}"
            if changed_price
            else f"Edited {product.get('name')}"
        ),
        before={"name": previous.get("name"), "price": previous.get("price")},
        after={"name": product.get("name"), "price": product.get("price")},
        severity="notice" if changed_price else "info",
        ip_address=client_ip(request),
    )


# ----------------------------------------------------------------------
# Categories and filter options
#
# Declared before /products/{product_id} is irrelevant, but these literal
# segments still come first so no future path parameter can swallow them.
# ----------------------------------------------------------------------


@router.get("/categories")
def list_categories(context=Depends(view_context)):
    _, company_id = context
    return {"items": catalogue_service.list_categories(company_id=company_id)}


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: ProductCategoryCreate,
    context=Depends(manage_context),
):
    _, company_id = context

    try:
        return catalogue_service.create_category(
            company_id=company_id,
            name=payload.name,
            parent_id=payload.parent_id,
            sort_order=payload.sort_order,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/categories/{category_id}")
def update_category(
    category_id: int,
    payload: ProductCategoryUpdate,
    context=Depends(manage_context),
):
    _, company_id = context

    try:
        category = catalogue_service.update_category(
            company_id=company_id,
            category_id=category_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not category:
        raise HTTPException(status_code=404, detail="Category not found.")

    return category


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, context=Depends(manage_context)):
    """Delete a category. Its products stay, unfiled."""
    _, company_id = context

    if not catalogue_service.delete_category(
        company_id=company_id, category_id=category_id
    ):
        raise HTTPException(status_code=404, detail="Category not found.")

    return {"success": True}


@router.get("/options")
def list_options(context=Depends(view_context)):
    """Everything the list screen needs to build its filters."""
    _, company_id = context

    return {
        "categories": catalogue_service.list_categories(company_id=company_id),
        "statuses": list(catalogue_service.ALLOWED_STATUS),
        "stock_filters": list(catalogue_service.STOCK_FILTERS),
    }


# ----------------------------------------------------------------------
# Products
# ----------------------------------------------------------------------


@router.get("/products")
def list_products(
    search: str | None = Query(default=None, max_length=200),
    category_id: int | None = Query(default=None, ge=1),
    stock: str | None = Query(default=None, max_length=20),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return catalogue_service.list_products(
            company_id=company_id,
            search=search,
            category_id=category_id,
            stock=stock,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        product = catalogue_service.create_product(
            company_id=company_id,
            data=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.PRODUCT_CREATED,
        category="catalogue",
        target_type="product",
        target_id=product.get("id"),
        summary=f"Added {product.get('name')}",
        after={"name": product.get("name"), "price": product.get("price")},
        ip_address=client_ip(request),
    )

    return product


@router.get("/products/{product_id}")
def get_product(product_id: int, context=Depends(view_context)):
    _, company_id = context
    product = catalogue_service.get_product(
        company_id=company_id, product_id=product_id
    )

    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    return product


@router.put("/products/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    # Read before the write, so the log can say what the price *was*. The
    # assistant quotes catalogue prices to customers as confirmed facts, which
    # makes "who changed that, and from what" the question an owner is most
    # likely to need answered — and there was nowhere to look.
    previous = catalogue_service.get_product(
        company_id=company_id, product_id=product_id
    )

    try:
        product = catalogue_service.update_product(
            company_id=company_id,
            product_id=product_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # After the 404, not before. Recording first would file an edit for a
    # product that does not exist and was never changed — a log entry that is
    # not merely useless but actively misleading during an investigation.
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    _record_product_change(
        current_user,
        company_id=company_id,
        previous=previous or {},
        product=product,
        request=request,
    )

    return product


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    previous = catalogue_service.get_product(
        company_id=company_id, product_id=product_id
    )

    if not catalogue_service.delete_product(
        company_id=company_id, product_id=product_id
    ):
        raise HTTPException(status_code=404, detail="Product not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.PRODUCT_DELETED,
        category="catalogue",
        target_type="product",
        target_id=product_id,
        summary=f"Removed {(previous or {}).get('name') or product_id}",
        before={
            "name": (previous or {}).get("name"),
            "price": (previous or {}).get("price"),
        },
        ip_address=client_ip(request),
    )

    return {"success": True}
