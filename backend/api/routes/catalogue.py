from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.catalogue import ProductCreateRequest, ProductUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.catalogue_service import (
    CatalogueConflictError,
    CatalogueValidationError,
    catalogue_service,
)


router = APIRouter(prefix="/api/catalogue", tags=["Catalogue"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: two dedicated permission codes are seeded in database.py for
# this module -- "catalogue.view" (list/read) and "catalogue.manage"
# (create/edit/delete). Both are granted automatically to the built-in
# "owner" role (auth_service.has_permission special-cases role code
# 'owner' to always allow, the same way every other permission code in
# this codebase is wired to it -- no explicit role_permissions row is
# needed) and can be attached to any other role from the Roles &
# Permissions admin screen like any other permission code.
def _require_catalogue_access(
    current_user: dict[str, Any],
    company_id: int,
    permission_code: str,
) -> None:
    allowed = auth_service.has_permission(
        user_id=current_user["id"],
        company_id=company_id,
        permission_code=permission_code,
        is_super_admin=bool(current_user.get("is_super_admin")),
    )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have catalogue management access.",
        )


@router.get("")
def list_products(
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_catalogue_access(current_user, company_id, "catalogue.view")

    return catalogue_service.list_products(
        company_id=company_id,
        status=status_filter,
        category=category,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/categories")
def list_categories(context=Depends(current_context)):
    current_user, company_id = context
    _require_catalogue_access(current_user, company_id, "catalogue.view")

    return {"items": catalogue_service.list_categories(company_id=company_id)}


@router.get("/{product_id}")
def get_product(product_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_catalogue_access(current_user, company_id, "catalogue.view")

    try:
        return catalogue_service.get_product(company_id=company_id, product_id=product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    _require_catalogue_access(current_user, company_id, "catalogue.manage")

    values = payload.model_dump(exclude_unset=True)
    try:
        return catalogue_service.create_product(company_id=company_id, values=values)
    except CatalogueValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdateRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_catalogue_access(current_user, company_id, "catalogue.manage")

    values = payload.model_dump(exclude_unset=True)
    # The concurrency token is not a product field -- pull it out before
    # the service filters the remaining editable fields.
    expected_updated_at = values.pop("expected_updated_at", None)

    try:
        return catalogue_service.update_product(
            company_id=company_id,
            product_id=product_id,
            values=values,
            expected_updated_at=expected_updated_at,
        )
    except CatalogueConflictError as exc:
        # Mirror TasksPage's 409 contract: a structured detail the UI can
        # act on, carrying the current record so it can offer a reload.
        try:
            current = catalogue_service.get_product(
                company_id=company_id, product_id=product_id
            )
        except KeyError:
            current = None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "current": current},
        ) from exc
    except CatalogueValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{product_id}")
def delete_product(product_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_catalogue_access(current_user, company_id, "catalogue.manage")

    deleted = catalogue_service.delete_product(company_id=company_id, product_id=product_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"message": "Product deleted"}
