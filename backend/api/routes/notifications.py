from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.notifications import (
    NotificationClearRequest,
    NotificationReadStateRequest,
    NotificationResponse,
    NotificationSummaryResponse,
)
from backend.services.auth_service import auth_service, get_current_user
from backend.services.notification_service import notification_service

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


def _context(current_user: dict) -> tuple[int, int]:
    company_id = auth_service.resolve_company_id(current_user=current_user)
    return int(company_id), int(current_user["id"])


@router.get("", response_model=list[NotificationResponse])
def list_notifications(
    notification_status: Literal["all", "unread", "read"] = Query("all", alias="status"),
    notification_type: str | None = Query(None, alias="type"),
    channel: str | None = Query(None),
    notification_date: date | None = Query(None, alias="date"),
    limit: int = Query(100, ge=1, le=250),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    company_id, user_id = _context(current_user)
    return notification_service.list_for_user(
        company_id=company_id,
        user_id=user_id,
        status=notification_status,
        notification_type=notification_type,
        channel=channel,
        notification_date=notification_date,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=NotificationSummaryResponse)
def notification_summary(current_user: dict = Depends(get_current_user)):
    company_id, user_id = _context(current_user)
    return notification_service.summary(company_id=company_id, user_id=user_id)


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    payload: NotificationReadStateRequest | None = None,
    current_user: dict = Depends(get_current_user),
):
    company_id, user_id = _context(current_user)
    group_ids = payload.notification_ids if payload else None
    if not notification_service.mark_read(
        notification_id=notification_id, company_id=company_id, user_id=user_id, group_ids=group_ids
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    return {"success": True, "notification_id": notification_id}


@router.post("/{notification_id}/unread")
def mark_notification_unread(
    notification_id: int,
    payload: NotificationReadStateRequest | None = None,
    current_user: dict = Depends(get_current_user),
):
    company_id, user_id = _context(current_user)
    group_ids = payload.notification_ids if payload else None
    if not notification_service.mark_unread(
        notification_id=notification_id, company_id=company_id, user_id=user_id, group_ids=group_ids
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    return {"success": True, "notification_id": notification_id}


@router.post("/read-all")
def mark_all_notifications_read(current_user: dict = Depends(get_current_user)):
    company_id, user_id = _context(current_user)
    updated = notification_service.mark_all_read(company_id=company_id, user_id=user_id)
    return {"success": True, "updated": updated}


@router.delete("/clear-visible")
def clear_visible_notifications(
    payload: NotificationClearRequest,
    current_user: dict = Depends(get_current_user),
):
    company_id, user_id = _context(current_user)
    deleted = notification_service.clear_visible(
        notification_ids=payload.notification_ids,
        company_id=company_id,
        user_id=user_id,
    )
    return {"success": True, "deleted": deleted}
