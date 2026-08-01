from fastapi import APIRouter, Depends

from backend.services.auth_service import auth_service, get_current_user
from backend.services.analytics_service import analytics_service


router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.get("")
def get_analytics_summary(context=Depends(current_context), days: int = 30):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "analytics.view")
    summary = analytics_service.get_summary(company_id)
    summary["conversation_volume_trend"] = analytics_service.get_conversation_volume_trend(company_id, days=days)
    summary["ai_vs_human_trend"] = analytics_service.get_ai_vs_human_trend(company_id, days=days)
    return summary
