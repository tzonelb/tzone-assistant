from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.services.auth_service import get_current_user
from backend.services.license_key_service import license_key_service
from backend.services.platform_admin_service import platform_admin_service


router = APIRouter(prefix="/api/platform", tags=["Platform Admin"])


def _require_super_admin(current_user: dict[str, Any]) -> None:
    if not current_user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required.",
        )


class CreateCompanyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100)
    country: str | None = None
    currency: str = "USD"
    plan_id: int | None = None
    trial_days: int = 5
    main_admin_email: str | None = None
    contact_phone: str | None = None
    license_code: str | None = None


class SetCompanyStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|suspended|cancelled)$")


class UpdateModulesRequest(BaseModel):
    appointments: bool | None = None
    scheduler: bool | None = None
    catalogue: bool | None = None
    team_chat: bool | None = None
    comments: bool | None = None


class RequestPlanRequest(BaseModel):
    plan_id: int
    note: str | None = None


class ReviewSubscriptionRequestRequest(BaseModel):
    approve: bool


class ChangePlanRequest(BaseModel):
    plan_id: int
    duration_days: int = 30


class CreatePlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=50)
    price_monthly: float = 0
    currency: str = "USD"
    max_users: int = Field(ge=0, default=1)
    max_channel_accounts: int = Field(ge=0, default=1)
    max_ai_messages: int = Field(ge=0, default=500)
    max_knowledge_items: int = Field(ge=0, default=100)
    voice_ai_enabled: bool = False
    image_ai_enabled: bool = False
    accounting_connector_enabled: bool = False
    product_connector_enabled: bool = False


class UpdatePlanRequest(BaseModel):
    name: str | None = None
    price_monthly: float | None = None
    currency: str | None = None
    max_users: int | None = Field(ge=0, default=None)
    max_channel_accounts: int | None = Field(ge=0, default=None)
    max_ai_messages: int | None = Field(ge=0, default=None)
    max_knowledge_items: int | None = Field(ge=0, default=None)
    voice_ai_enabled: bool | None = None
    image_ai_enabled: bool | None = None
    accounting_connector_enabled: bool | None = None
    product_connector_enabled: bool | None = None
    status: str | None = Field(default=None, pattern="^(active|retired)$")


@router.get("/companies")
def list_companies(
    status: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    return {"companies": platform_admin_service.list_companies(status=status)}


@router.get("/companies/{company_id}")
def get_company(
    company_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        return platform_admin_service.get_company_detail(company_id=company_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Company not found")


@router.post("/companies")
def create_company(
    payload: CreateCompanyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        return platform_admin_service.create_company(**payload.model_dump(), actor_user_id=current_user.get("id"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/companies/{company_id}/status")
def set_company_status(
    company_id: int,
    payload: SetCompanyStatusRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        return platform_admin_service.set_company_status(
            company_id=company_id,
            status=payload.status,
            actor_user_id=current_user.get("id"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Company not found")


@router.patch("/companies/{company_id}/modules")
def update_company_modules(
    company_id: int,
    payload: UpdateModulesRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    modules = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return platform_admin_service.update_modules(
            company_id=company_id, modules=modules, actor_user_id=current_user.get("id"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Company not found")


@router.get("/plans")
def list_plans(
    active_only: bool = True,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    return {"plans": platform_admin_service.list_plans(active_only=active_only)}


@router.post("/plans")
def create_plan(
    payload: CreatePlanRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        return platform_admin_service.create_plan(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/plans/{plan_id}")
def update_plan(
    plan_id: int,
    payload: UpdatePlanRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return platform_admin_service.update_plan(plan_id=plan_id, values=values)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan not found")


@router.post("/companies/{company_id}/plan")
def change_plan(
    company_id: int,
    payload: ChangePlanRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        return platform_admin_service.change_plan(
            company_id=company_id,
            plan_id=payload.plan_id,
            duration_days=payload.duration_days,
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/usage")
def platform_usage(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    return platform_admin_service.platform_usage_summary()


@router.get("/plans-catalog")
def plans_catalog(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Company-scoped, read-only: every active plan's full details, for
    the self-service comparison table in Company Settings > Subscription.
    Any logged-in company member can see this — plan pricing isn't secret."""
    return {"plans": platform_admin_service.list_plans(active_only=True)}


@router.post("/subscription-requests")
def request_plan(
    payload: RequestPlanRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    from backend.services.auth_service import auth_service
    company_id = auth_service.resolve_company_id(current_user)
    auth_service.require_permission(current_user, company_id, "subscriptions.manage")
    try:
        return platform_admin_service.request_plan(
            company_id=company_id, plan_id=payload.plan_id, note=payload.note,
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/my-subscription-requests")
def my_subscription_requests(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    from backend.services.auth_service import auth_service
    company_id = auth_service.resolve_company_id(current_user)
    auth_service.require_permission(current_user, company_id, "subscriptions.view")
    return {"requests": platform_admin_service.list_subscription_requests(company_id=company_id)}


@router.get("/subscription-requests")
def list_subscription_requests(
    status: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    return {"requests": platform_admin_service.list_subscription_requests(status=status)}


@router.post("/subscription-requests/{request_id}/review")
def review_subscription_request(
    request_id: int,
    payload: ReviewSubscriptionRequestRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        return platform_admin_service.review_subscription_request(
            request_id=request_id, approve=payload.approve, actor_user_id=current_user.get("id"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Request not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/my-modules")
def my_modules(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Company-scoped, read-only: what modules Super Admin has enabled
    for this company. Which employees can actually USE an enabled module
    is controlled separately via Roles & Permissions (modules.* codes)."""
    from backend.services.auth_service import auth_service
    company_id = auth_service.resolve_company_id(current_user)
    detail = platform_admin_service.get_company_detail(company_id=company_id)
    return {
        "appointments": bool(detail.get("module_appointments_enabled")),
        "scheduler": bool(detail.get("module_scheduler_enabled")),
        "catalogue": bool(detail.get("module_catalogue_enabled")),
        "team_chat": bool(detail.get("module_team_chat_enabled")),
        "comments": bool(detail.get("module_comments_enabled")),
    }


@router.get("/my-subscription")
def my_subscription(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Company-scoped, read-only: any logged-in company user can see
    their OWN company's real plan/limits — no super-admin required, and
    nothing here can be edited (that stays exclusively in Platform Admin).
    Used by Company Settings > Subscription instead of a fake placeholder."""
    from backend.services.auth_service import auth_service
    company_id = auth_service.resolve_company_id(current_user)
    limits = platform_admin_service.get_active_subscription_limits(company_id=company_id)
    if limits is None:
        return {"has_subscription": False}

    with __import__("database.database", fromlist=["db"]).db.connect() as conn:
        active_users = conn.execute(
            "SELECT COUNT(*) AS total FROM company_users WHERE company_id = ? AND status = 'active'",
            (company_id,),
        ).fetchone()["total"]
        active_channels = conn.execute(
            "SELECT COUNT(*) AS total FROM channel_accounts WHERE company_id = ? AND status = 'active'",
            (company_id,),
        ).fetchone()["total"]
        subscription_row = conn.execute(
            "SELECT status, expires_at FROM subscriptions WHERE company_id = ? "
            "AND status IN ('active', 'trialing') ORDER BY created_at DESC LIMIT 1",
            (company_id,),
        ).fetchone()

    return {
        "has_subscription": True,
        "plan_id": limits["id"],
        "plan_name": limits["name"],
        "plan_code": limits["code"],
        "price_monthly": limits["price_monthly"],
        "subscription_status": subscription_row["status"] if subscription_row else None,
        "expires_at": subscription_row["expires_at"] if subscription_row else None,
        "users": {"used": active_users, "max": limits["max_users"]},
        "channels": {"used": active_channels, "max": limits["max_channel_accounts"]},
        "max_ai_messages": limits["max_ai_messages"],
        "max_knowledge_items": limits["max_knowledge_items"],
        "features": {
            "voice_ai": bool(limits["voice_ai_enabled"]),
            "image_ai": bool(limits["image_ai_enabled"]),
            "accounting_connector": bool(limits["accounting_connector_enabled"]),
            "product_connector": bool(limits["product_connector_enabled"]),
        },
    }


class IssueLicenseKeyRequest(BaseModel):
    plan_id: int
    note: str | None = Field(default=None, max_length=200)


@router.get("/license-keys")
def list_license_keys(current_user: dict[str, Any] = Depends(get_current_user)):
    """SUPER-ADMIN ONLY — pre-issued keys a customer can redeem at signup
    in place of picking a plan (e.g. sold offline / through a reseller)."""
    _require_super_admin(current_user)
    return {"license_keys": license_key_service.list_all()}


@router.post("/license-keys")
def issue_license_key(payload: IssueLicenseKeyRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    _require_super_admin(current_user)
    try:
        return license_key_service.issue(
            plan_id=payload.plan_id, note=payload.note, issued_by_user_id=current_user.get("id"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan not found")


@router.get("/audit-logs")
def list_audit_logs(
    company_id: int | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """SUPER-ADMIN ONLY — reads back the audit_logs rows already written by
    every mutating super-admin action (company create, status change,
    module toggle, plan change)."""
    _require_super_admin(current_user)
    return platform_admin_service.list_audit_logs(
        company_id=company_id, action=action, limit=limit, offset=offset,
    )


@router.get("/revenue")
def platform_revenue(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """SUPER-ADMIN ONLY — real MRR and plan breakdown computed from the
    subscriptions/plans tables, no estimation."""
    _require_super_admin(current_user)
    return platform_admin_service.revenue_summary()


@router.get("/companies/{company_id}/subscription-history")
def company_subscription_history(
    company_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        return {"history": platform_admin_service.list_subscription_history(company_id=company_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Company not found")
