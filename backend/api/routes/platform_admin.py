from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.services.auth_service import get_current_user
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
    trial_days: int = 14


class SetCompanyStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|suspended|cancelled)$")


class ChangePlanRequest(BaseModel):
    plan_id: int
    duration_days: int = 30


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
        return platform_admin_service.create_company(**payload.model_dump())
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


@router.get("/plans")
def list_plans(
    active_only: bool = True,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    return {"plans": platform_admin_service.list_plans(active_only=active_only)}


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
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/usage")
def platform_usage(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _require_super_admin(current_user)
    return platform_admin_service.platform_usage_summary()
