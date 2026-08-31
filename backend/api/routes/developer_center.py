from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.services.auth_service import auth_service, get_current_user
from backend.services.diagnostics_service import diagnostics_service


router = APIRouter(prefix="/api/developer-center", tags=["Developer Center"])


def super_admin_context(current_user: dict[str, Any] = Depends(get_current_user)):
    if not bool(current_user.get("is_super_admin")):
        raise HTTPException(status_code=403, detail="Super Admin access required.")
    company_id = auth_service.resolve_company_id(current_user=current_user, requested_company_id=None)
    return current_user, int(company_id)


@router.get("/summary")
def get_diagnostics_summary(context=Depends(super_admin_context)):
    _, company_id = context
    return diagnostics_service.summary(company_id=company_id)


@router.get("/events")
def list_diagnostic_events(
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    context=Depends(super_admin_context),
):
    _, company_id = context
    return diagnostics_service.list_events(
        company_id=company_id,
        limit=limit,
        event_type=event_type,
        severity=severity,
        channel=channel,
    )


@router.post("/cleanup")
def cleanup_diagnostic_events(
    retention_days: int = Query(default=14, ge=1, le=365),
    context=Depends(super_admin_context),
):
    _, company_id = context
    deleted = diagnostics_service.cleanup(
        company_id=company_id,
        retention_days=retention_days,
    )
    return {"status": "ok", "deleted": deleted, "retention_days": retention_days}
