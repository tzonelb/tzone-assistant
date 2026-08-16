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

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.catalogue import (
    ProductCategoryCreate,
    ProductCategoryUpdate,
    ProductCreate,
    ProductUpdate,
)
from backend.services.auth_service import auth_service, require_permission
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
    context=Depends(manage_context),
):
    _, company_id = context

    try:
        return catalogue_service.create_product(
            company_id=company_id,
            data=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    context=Depends(manage_context),
):
    _, company_id = context

    try:
        product = catalogue_service.update_product(
            company_id=company_id,
            product_id=product_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    return product


@router.delete("/products/{product_id}")
def delete_product(product_id: int, context=Depends(manage_context)):
    _, company_id = context

    if not catalogue_service.delete_product(
        company_id=company_id, product_id=product_id
    ):
        raise HTTPException(status_code=404, detail="Product not found.")

    return {"success": True}
