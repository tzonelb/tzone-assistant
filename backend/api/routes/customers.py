from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.customers import CustomerUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.customer_service import customer_service


router = APIRouter(prefix="/api/customers", tags=["Customers"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: there is no dedicated "customers.*" permission code seeded in
# database.py, and this fix does not invent a new one. Customer records
# (phone/email/notes) are the PII captured from conversations, so:
#  - viewing them is gated behind "conversations.view" (the closest
#    existing view-level code — anyone allowed to see conversations is
#    already exposed to this same data through them).
#  - editing them is gated behind "users.manage", the codebase's existing
#    de-facto "elevated admin action" permission (already reused this way
#    for conversation admin overrides in conversations.py and for
#    role/user administration in roles.py), since there is no
#    "conversations.manage"-equivalent write permission today.
def _require_customer_access(
    current_user: dict,
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
            detail="You do not have access to customer records.",
        )


@router.get("")
def list_customers(
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_customer_access(current_user, company_id, "conversations.view")
    return customer_service.list_customers(
        company_id=company_id, search=search, limit=limit, offset=offset
    )


@router.get("/{customer_id}")
def get_customer(customer_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_customer_access(current_user, company_id, "conversations.view")
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
    _require_customer_access(current_user, company_id, "users.manage")
    try:
        return customer_service.update_customer(
            company_id=company_id,
            customer_id=customer_id,
            values=payload.model_dump(exclude_unset=True),
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
