from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.schemas.company_settings import CompanySettingsUpdate
from backend.services.auth_service import auth_service, get_current_user
from backend.services.company_settings_service import company_settings_service


router = APIRouter(prefix="/api/company-settings", tags=["Company Settings"])


# Sections that expose billing, security or credential-adjacent data.
# Viewing these (not just editing them) requires "settings.manage",
# not just "settings.view". Must stay in sync with the frontend's
# SENSITIVE_SECTIONS set in CompanySettingsPage.jsx.
SENSITIVE_SECTIONS = {"ai", "subscription", "security", "api", "backup"}


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def _require_settings_permission(
    current_user: dict[str, Any],
    company_id: int,
    code: str,
) -> None:
    if current_user.get("is_super_admin"):
        return
    if auth_service.has_permission(current_user["id"], company_id, code, False):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to access this company setting.",
    )


def _can_view_section(current_user: dict[str, Any], company_id: int, section: str) -> bool:
    if current_user.get("is_super_admin"):
        return True
    normalized = section.strip().lower()
    required_code = "settings.manage" if normalized in SENSITIVE_SECTIONS else "settings.view"
    return auth_service.has_permission(current_user["id"], company_id, required_code, False)


@router.get("")
def get_all_company_settings(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    sections = company_settings_service.get_all(company_id)
    visible_sections = {
        section: payload
        for section, payload in sections.items()
        if _can_view_section(current_user, company_id, section)
    }
    return {
        "company_id": company_id,
        "sections": visible_sections,
    }


@router.get("/{section}")
def get_company_setting_section(
    section: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    normalized = section.strip().lower()
    required_code = "settings.manage" if normalized in SENSITIVE_SECTIONS else "settings.view"
    _require_settings_permission(current_user, company_id, required_code)
    return company_settings_service.get_section(company_id, section)


@router.put("/{section}")
def update_company_setting_section(
    section: str,
    payload: CompanySettingsUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_settings_permission(current_user, company_id, "settings.manage")
    try:
        return company_settings_service.update_section(
            company_id=company_id,
            section=section,
            values=payload.values,
            actor_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
