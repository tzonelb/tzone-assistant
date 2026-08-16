from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.schemas.company_settings import CompanySettingsUpdate
from backend.services.auth_service import auth_service, require_permission
from backend.services.company_settings_service import company_settings_service


router = APIRouter(prefix="/api/company-settings", tags=["Company Settings"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


@router.get("")
def get_all_company_settings(
    current_user: dict[str, Any] = Depends(require_permission("settings.view")),
):
    return {
        "company_id": _company_id(current_user),
        "sections": company_settings_service.get_all(_company_id(current_user)),
    }


@router.get("/{section}")
def get_company_setting_section(
    section: str,
    current_user: dict[str, Any] = Depends(require_permission("settings.view")),
):
    return company_settings_service.get_section(_company_id(current_user), section)


@router.put("/{section}")
def update_company_setting_section(
    section: str,
    payload: CompanySettingsUpdate,
    current_user: dict[str, Any] = Depends(require_permission("settings.manage")),
):
    try:
        return company_settings_service.update_section(
            company_id=_company_id(current_user),
            section=section,
            values=payload.values,
            actor_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
