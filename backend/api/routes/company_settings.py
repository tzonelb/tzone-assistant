from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.schemas.company_settings import CompanySettingsUpdate
from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
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
    try:
        return company_settings_service.get_section(
            _company_id(current_user), section
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{section}")
def update_company_setting_section(
    section: str,
    payload: CompanySettingsUpdate,
    request: Request,
    current_user: dict[str, Any] = Depends(require_permission("settings.manage")),
):
    company_id = _company_id(current_user)

    try:
        updated = company_settings_service.update_section(
            company_id=company_id,
            section=section,
            values=payload.values,
            actor_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # The key names that were written, never their values. A section is an open
    # bag of whatever a module keeps here — tokens and account identifiers
    # included — so recording the values would copy secrets into a table the
    # whole company can read.
    changed = sorted(payload.values)

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.SETTINGS_UPDATED,
        category="company_settings",
        target_type="settings_section",
        target_id=section,
        summary=f"Changed {len(changed)} setting(s) in {section}: {', '.join(changed)}",
        after={"section": section, "changed_keys": changed},
        ip_address=client_ip(request),
    )

    return updated
