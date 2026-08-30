"""What the customer workspace is allowed to show.

The Super Admin decides which modules a company sees, how the workspace is
branded and how the shell is laid out. That decision is stored in the control
plane; this endpoint is how the customer application reads its own copy of it.

Two things it deliberately is not:

* It is not the enforcement. ``backend/services/module_access.require_module``
  is, and it guards the module routers themselves. This endpoint only lets the
  interface avoid drawing a link that would open onto a 403.
* It is not the control-plane API. A company token reaches this and nothing
  else: it returns the caller's own configuration, never another company's, and
  it cannot change anything.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from backend.services.auth_service import (
    auth_service,
    client_ip,
    get_current_user,
    require_permission,
)
from backend.services.activity_service import Action, activity_service
from backend.services.platform_service import PlatformError, platform_service
from database.manager import DatabaseError


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/platform-ui", tags=["Workspace Configuration"])


@router.get("/config")
async def get_workspace_config(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """The module, branding and layout configuration of the caller's company.

    The company is taken from the session, never from a parameter. An employee
    asking for a configuration is always asking for their own.
    """
    company_id = auth_service.resolve_company_id(current_user)

    try:
        config = platform_service.get_platform_config(company_id)
    except DatabaseError:
        logger.exception("Could not read workspace config for company %s.", company_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace configuration is temporarily unavailable.",
        ) from None

    branding = config["branding"] or {}

    return {
        "company_id": company_id,
        "modules": config["modules"],
        "branding": branding,
        "layout": config["layout"],
        # The design tokens the interface renders with, resolved over the
        # platform defaults so this is never partial. `modules` above stays the
        # operator's gate; `tokens` only decides how the interface looks.
        "tokens": config["theme"],
        "brand": {
            "name": branding.get("brand_name") or "T-ZONE",
            "logoUrl": branding.get("logo_url") or "/tzone-logo.png",
        },
        "updated_at": config["updated_at"],
    }


@router.put("/theme")
async def update_workspace_theme(
    request: Request,
    theme: dict[str, Any] = Body(..., embed=False),
    current_user: dict[str, Any] = Depends(require_permission("settings.manage")),
) -> dict[str, Any]:
    """Publish this company's design tokens (Theme Studio).

    Scoped to the caller's own company, exactly like the read above: the company
    comes from the session and never from the payload. It writes tokens and
    nothing else, so styling a workspace can never switch a module on — that
    remains the platform operator's decision.
    """
    company_id = auth_service.resolve_company_id(current_user)

    try:
        resolved = platform_service.update_theme(
            company_id,
            theme,
            actor_user_id=int(current_user["id"]),
        )
    except PlatformError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except DatabaseError:
        logger.exception("Could not save the theme for company %s.", company_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The theme could not be saved.",
        ) from None

    activity_service.record(
        company_id=company_id,
        action=Action.SETTINGS_UPDATED,
        category="settings",
        kind="change",
        actor_user_id=int(current_user["id"]),
        summary="Workspace theme published",
        ip_address=client_ip(request),
    )

    return {"tokens": resolved}
