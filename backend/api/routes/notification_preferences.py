"""One employee's own notification choices, inside one company.

No permission beyond being signed in, and no `user_id` parameter anywhere: these
routes act on the caller's own record only. A route that accepted a user id here
would let an administrator silence a colleague's notifications, which is a
different feature from tuning your own and one nobody asked for.

The company comes from the session like everywhere else, so somebody who belongs
to two companies tunes the one they are signed into.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.services.auth_service import auth_service, get_current_user
from backend.services.notification_preference_service import (
    notification_preference_service,
)


router = APIRouter(prefix="/api/notification-preferences", tags=["Notifications"])


def _context(current_user: dict[str, Any]) -> tuple[int, int]:
    return (
        auth_service.resolve_company_id(current_user),
        int(current_user["id"]),
    )


class NotificationPreferencesUpdate(BaseModel):
    # Every field optional: the screen sends what it drew, and a missing key
    # means "leave this one alone" rather than "reset it to the default".
    notify_new_message: str | None = None
    notify_ai_escalation: bool | None = None
    notify_mentions: bool | None = None
    notify_tasks: bool | None = None


@router.get("")
def get_notification_preferences(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id, user_id = _context(current_user)
    return notification_preference_service.get_for_user(
        company_id=company_id, user_id=user_id
    )


@router.put("")
def update_notification_preferences(
    payload: NotificationPreferencesUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id, user_id = _context(current_user)
    values = {
        key: value
        for key, value in payload.model_dump().items()
        if value is not None
    }

    try:
        return notification_preference_service.update_for_user(
            company_id=company_id, user_id=user_id, values=values
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
