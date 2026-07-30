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
def get_analytics_summary(context=Depends(current_context)):
    _, company_id = context
    return analytics_service.get_summary(company_id)
