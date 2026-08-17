from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.schemas.customers import CustomerUpdateRequest
from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import auth_service, client_ip, require_permission
from backend.services.customer_service import customer_service


router = APIRouter(prefix="/api/customers", tags=["Customers"])


def _context(current_user):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("customers.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("customers.manage"))):
    return _context(current_user)


@router.get("")
def list_customers(
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context
    return customer_service.list_customers(
        company_id=company_id, search=search, limit=limit, offset=offset
    )


@router.get("/{customer_id}")
def get_customer(customer_id: int, request: Request, context=Depends(view_context)):
    current_user, company_id = context

    try:
        customer = customer_service.get_customer(
            company_id=company_id, customer_id=customer_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # After the record is found, so a probe for a customer id that does not
    # exist does not leave an entry saying somebody read it. A customer file
    # holds contact details the person gave this company and nobody else, so
    # who opened it is the owner's to see even though nothing changed.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CUSTOMER_OPENED,
        category="customers",
        kind="read",
        target_type="customer",
        target_id=customer_id,
        summary="Opened a customer record",
        ip_address=client_ip(request),
    )

    return customer


@router.put("/{customer_id}")
def update_customer(
    customer_id: int,
    payload: CustomerUpdateRequest,
    context=Depends(manage_context),
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
