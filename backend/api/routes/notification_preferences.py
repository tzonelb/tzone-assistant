from fastapi import APIRouter, Depends

from backend.api.schemas.notifications import (
    NotificationPreferencesResponse,
    NotificationPreferencesUpdateRequest,
)
from backend.services.auth_service import auth_service, get_current_user
from backend.services.notification_preference_service import notification_preference_service

router = APIRouter(prefix="/api/notification-preferences", tags=["Notifications"])


def _context(current_user: dict) -> tuple[int, int]:
    company_id = auth_service.resolve_company_id(current_user=current_user)
    return int(company_id), int(current_user["id"])


@router.get("", response_model=NotificationPreferencesResponse)
def get_notification_preferences(current_user: dict = Depends(get_current_user)):
    company_id, user_id = _context(current_user)
    return notification_preference_service.get_for_user(user_id=user_id, company_id=company_id)


@router.put("", response_model=NotificationPreferencesResponse)
def update_notification_preferences(
    payload: NotificationPreferencesUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    company_id, user_id = _context(current_user)
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    return notification_preference_service.update_for_user(
        user_id=user_id, company_id=company_id, **fields
    )
