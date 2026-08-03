from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.api.routes.conversations import _company_employees
from backend.services.activity_log_service import activity_log_service
from backend.services.auth_service import auth_service, get_current_user


router = APIRouter(prefix="/api/activity-log", tags=["Activity Log"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(current_user=current_user, requested_company_id=None)
    return current_user, int(company_id)


@router.get("")
def list_activity(
    actor_user_id: int | None = Query(default=None),
    action: str | None = Query(default=None),
    before_id: int | None = Query(default=None),
    limit: int = Query(default=100),
    context=Depends(current_context),
):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "users.manage")
    result = activity_log_service.list_for_company(
        company_id=company_id, actor_user_id=actor_user_id, action=action, before_id=before_id, limit=limit,
    )
    result["employees"] = _company_employees(company_id)
    return result
