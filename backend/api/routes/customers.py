from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.routes.conversations import _company_employees
from backend.api.schemas.customers import (
    CustomerBulkUpdateRequest,
    CustomerCreateRequest,
    CustomerUpdateRequest,
    SegmentCreateRequest,
)
from backend.services.auth_service import auth_service, get_current_user
from backend.services.customer_service import LIFECYCLE_STAGES, customer_service


router = APIRouter(prefix="/api/customers", tags=["Customers"])
segments_router = APIRouter(prefix="/api/customer-segments", tags=["Customer Segments"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.get("/options")
def customer_options(context=Depends(current_context)):
    """Reference data for the Contacts UI — the fixed lifecycle pipeline
    plus the company's active employees (for the assignment dropdown).
    Tags stay free-form (no options list) since they're company-defined,
    same philosophy as Knowledge entry tags."""
    _, company_id = context
    return {
        "lifecycle_stages": LIFECYCLE_STAGES,
        "employees": _company_employees(company_id),
    }


@router.get("")
def list_customers(
    search: str | None = Query(default=None),
    lifecycle_stage: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    assigned_user_id: int | None = Query(default=None),
    segment_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    _, company_id = context
    try:
        return customer_service.list_customers(
            company_id=company_id,
            search=search,
            lifecycle_stage=lifecycle_stage,
            tag=tag,
            assigned_user_id=assigned_user_id,
            segment_id=segment_id,
            limit=limit,
            offset=offset,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("")
def create_customer(payload: CustomerCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    try:
        return customer_service.create_customer(
            company_id=company_id,
            display_name=payload.display_name,
            phone=payload.phone,
            email=payload.email,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bulk-update")
def bulk_update_customers(payload: CustomerBulkUpdateRequest, context=Depends(current_context)):
    current_user, company_id = context
    try:
        return customer_service.bulk_update_customers(
            company_id=company_id,
            customer_ids=payload.customer_ids,
            lifecycle_stage=payload.lifecycle_stage,
            add_tag=payload.add_tag,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{customer_id}")
def get_customer(customer_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        return customer_service.get_customer(company_id=company_id, customer_id=customer_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{customer_id}/timeline")
def get_customer_timeline(customer_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        return {"items": customer_service.get_timeline(company_id=company_id, customer_id=customer_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{customer_id}")
def update_customer(
    customer_id: int,
    payload: CustomerUpdateRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    try:
        return customer_service.update_customer(
            company_id=company_id,
            customer_id=customer_id,
            values=payload.model_dump(exclude_unset=True),
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@segments_router.get("")
def list_segments(context=Depends(current_context)):
    _, company_id = context
    return {"items": customer_service.list_segments(company_id=company_id)}


@segments_router.post("")
def create_segment(payload: SegmentCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    try:
        return customer_service.create_segment(
            company_id=company_id,
            name=payload.name,
            filters=payload.filters.model_dump(exclude_none=True),
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@segments_router.delete("/{segment_id}")
def delete_segment(segment_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        customer_service.delete_segment(company_id=company_id, segment_id=segment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
