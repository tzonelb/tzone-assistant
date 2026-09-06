"""The company's own view of its plan: what it is on, and what it may ask for.

The operator's side of this is `/api/platform/...`, which runs on a
platform-scope session and addresses any company by id. Nothing here does: every
route resolves its company from the session through
`auth_service.resolve_company_id`, so a caller cannot name one.

Behind `subscriptions.view` rather than `dashboard.view`. What a company pays is
commercial information about the business and the wider permission is one almost
every employee holds — the same line `/api/dashboard/subscription` draws, and the
reason that route strips the price while these do not.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, require_permission
from backend.services.billing_service import billing_service


router = APIRouter(prefix="/api/billing", tags=["Billing"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


class PlanChangeRequest(BaseModel):
    plan_id: int = Field(ge=1)
    # The company's own words about how they paid — a transfer reference,
    # usually. Bounded here rather than trusted: it is free text that reaches an
    # operator's screen.
    note: str = Field(default="", max_length=500)


@router.get("/subscription")
def get_subscription(
    current_user: dict[str, Any] = Depends(require_permission("subscriptions.view")),
):
    return billing_service.subscription(_company_id(current_user))


@router.get("/modules")
def get_modules(
    current_user: dict[str, Any] = Depends(require_permission("subscriptions.view")),
):
    """Which modules the operator has switched on for this company.

    On the billing screen because that is where an owner is deciding what to
    buy: a module they do not have and a permission their role is missing look
    identical from a screen that simply is not in the navigation.
    """
    return {"modules": billing_service.modules(_company_id(current_user))}


@router.get("/plans")
def get_plans(
    current_user: dict[str, Any] = Depends(require_permission("subscriptions.view")),
):
    # Not scoped to the company — the catalogue is the same for everybody — but
    # still behind the session, because it is priced.
    _company_id(current_user)
    return {"plans": billing_service.plans()}


@router.get("/requests")
def list_requests(
    current_user: dict[str, Any] = Depends(require_permission("subscriptions.view")),
):
    return {"requests": billing_service.requests(_company_id(current_user))}


@router.post("/requests")
def create_request(
    payload: PlanChangeRequest,
    current_user: dict[str, Any] = Depends(require_permission("subscriptions.view")),
):
    """Ask the operator to move this company onto a plan, or to renew it.

    Nothing changes here. Online payment is not wired up, so this records a
    request the T-ZONE team reviews and applies from the console — a company
    that could move itself onto a larger plan would be granting itself the
    allowances that come with it.
    """
    try:
        return billing_service.request_change(
            company_id=_company_id(current_user),
            plan_id=payload.plan_id,
            note=payload.note,
            actor_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
