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

from fastapi import APIRouter, Depends, HTTPException, status

from backend.services.auth_service import auth_service, get_current_user
from backend.services.platform_service import platform_service
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

    return {
        "company_id": company_id,
        "modules": config["modules"],
        "branding": config["branding"],
        "layout": config["layout"],
        "updated_at": config["updated_at"],
    }
