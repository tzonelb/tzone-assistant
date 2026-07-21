from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.schemas.customers import CustomerUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.customer_service import customer_service


router = APIRouter(prefix="/api/customers", tags=["Customers"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.get("")
def list_customers(
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    _, company_id = context
    return customer_service.list_customers(
        company_id=company_id, search=search, limit=limit, offset=offset
    )


@router.get("/{customer_id}")
def get_customer(customer_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        return customer_service.get_customer(company_id=company_id, customer_id=customer_id)
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
