from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile

from backend.api.schemas.catalogue import ProductCreateRequest, ProductUpdateRequest, WhatsAppCatalogImportRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.catalogue_service import STATUSES, catalogue_service


router = APIRouter(prefix="/api/catalogue", tags=["Catalogue"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.get("/options")
def catalogue_options(context=Depends(current_context)):
    """Reference data for the Catalogue UI — the fixed status pipeline
    plus the company's currently-used categories (for the filter/autocomplete).
    Mirrors tasks.py's /options endpoint exactly."""
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    return {
        "statuses": STATUSES,
        "categories": catalogue_service.list_categories(company_id=company_id),
    }


@router.post("")
def create_product(payload: ProductCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    try:
        return catalogue_service.create_product(
            company_id=company_id,
            name=payload.name,
            sku=payload.sku,
            description=payload.description,
            category=payload.category,
            price_cents=payload.price_cents,
            stock_quantity=payload.stock_quantity,
            image_url=payload.image_url,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_products(
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    context=Depends(current_context),
):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    return catalogue_service.list_products(
        company_id=company_id,
        search=search,
        category=category,
        status=status,
    )


@router.get("/{product_id}")
def get_product(product_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    try:
        return catalogue_service.get_product(company_id=company_id, product_id=product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{product_id}")
def update_product(product_id: int, payload: ProductUpdateRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    try:
        return catalogue_service.update_product(
            company_id=company_id,
            product_id=product_id,
            values=payload.model_dump(exclude_unset=True),
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import/csv")
async def import_csv(file: UploadFile, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    content = await file.read()
    try:
        return catalogue_service.import_from_csv(
            company_id=company_id, file_content=content, actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import/whatsapp")
def import_whatsapp_catalog(payload: WhatsAppCatalogImportRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    try:
        return catalogue_service.import_from_whatsapp_catalog(
            company_id=company_id, catalog_id=payload.catalog_id,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{product_id}")
def delete_product(product_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.catalogue")
    try:
        catalogue_service.delete_product(company_id=company_id, product_id=product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
