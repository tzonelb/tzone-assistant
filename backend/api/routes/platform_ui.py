from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.platform_ui_service import (
    PlatformUiValidationError,
    platform_ui_service,
)


router = APIRouter(prefix="/api/platform-ui", tags=["Platform UI / Theme Studio"])


class ThemeDraftRequest(BaseModel):
    scope_type: str = Field(pattern="^(platform|plan|company)$")
    scope_id: str | None = None
    tokens: dict[str, Any] = {}
    modules: dict[str, Any] = {}


class ThemeDraftUpdateRequest(BaseModel):
    tokens: dict[str, Any] | None = None
    modules: dict[str, Any] | None = None


class ThemePublishRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


def _check_scope_permission(current_user: dict[str, Any], scope_type: str, scope_id: str | None) -> None:
    """Only a super admin may write platform/plan-scope themes. A company
    owner may write only their own tenant's company-scope override —
    never another tenant's, and never plan/platform scope."""
    if current_user.get("is_super_admin"):
        return

    if scope_type == "company" and scope_id:
        companies = auth_service.get_user_companies(current_user["id"])
        owns_this_company = any(
            str(company["id"]) == str(scope_id) and company.get("role_code") == "owner"
            for company in companies
        )
        if owns_this_company:
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only a platform super admin (or, for their own company, an owner) can manage this theme scope.",
    )


@router.get("/config")
def get_config(response: Response, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = None
    if not current_user.get("is_super_admin"):
        company_id = auth_service.resolve_company_id(current_user)
    resolved = platform_ui_service.resolve_config(company_id=company_id)
    response.headers["ETag"] = f'"{resolved["version"]}-{company_id or "platform"}"'
    return resolved


@router.get("/themes")
def list_themes(
    scope_type: str = Query(pattern="^(platform|plan|company)$"),
    scope_id: str | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    _check_scope_permission(current_user, scope_type, scope_id)
    return {"themes": platform_ui_service.list_themes(scope_type=scope_type, scope_id=scope_id)}


@router.post("/themes")
def create_theme(payload: ThemeDraftRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    _check_scope_permission(current_user, payload.scope_type, payload.scope_id)
    try:
        theme = platform_ui_service.create_draft(
            scope_type=payload.scope_type, scope_id=payload.scope_id,
            tokens=payload.tokens, modules=payload.modules,
            created_by=current_user["id"],
        )
    except PlatformUiValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return platform_ui_service.serialize(theme)


def _theme_or_404(theme_id: int) -> dict[str, Any]:
    try:
        return platform_ui_service.get_theme(theme_id=theme_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Theme not found.") from exc


@router.patch("/themes/{theme_id}")
def update_theme(theme_id: int, payload: ThemeDraftUpdateRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    theme = _theme_or_404(theme_id)
    _check_scope_permission(current_user, theme["scope_type"], theme["scope_id"])
    try:
        updated = platform_ui_service.update_draft(theme_id=theme_id, tokens=payload.tokens, modules=payload.modules)
    except PlatformUiValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return platform_ui_service.serialize(updated)


@router.post("/themes/{theme_id}/publish")
def publish_theme(theme_id: int, payload: ThemePublishRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    theme = _theme_or_404(theme_id)
    _check_scope_permission(current_user, theme["scope_type"], theme["scope_id"])
    try:
        published = platform_ui_service.publish(theme_id=theme_id, actor_user_id=current_user["id"], reason=payload.reason)
    except PlatformUiValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return platform_ui_service.serialize(published)


@router.post("/themes/{theme_id}/restore")
def restore_theme(theme_id: int, current_user: dict[str, Any] = Depends(get_current_user)):
    theme = _theme_or_404(theme_id)
    _check_scope_permission(current_user, theme["scope_type"], theme["scope_id"])
    try:
        restored = platform_ui_service.restore(theme_id=theme_id, created_by=current_user["id"])
    except PlatformUiValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return platform_ui_service.serialize(restored)
