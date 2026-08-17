"""The publishing calendar."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.scheduler_service import (
    STATUSES,
    SchedulerError,
    scheduler_service,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduler", tags=["Scheduler"])


class ScheduledPostCreate(BaseModel):
    channel: Literal["messenger", "instagram"]
    body: str = Field(min_length=1, max_length=5000)
    scheduled_for: str = Field(min_length=8, max_length=40)
    media_url: str | None = Field(default=None, max_length=1000)
    link_url: str | None = Field(default=None, max_length=1000)
    channel_account_id: int | None = None

    @field_validator("scheduled_for")
    @classmethod
    def must_be_a_timestamp(cls, value: str) -> str:
        """Normalise to UTC so the due check is a plain string comparison.

        Mixed offsets would make the queue publish in the wrong order, or miss a
        post entirely.
        """
        from datetime import datetime, timezone

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "scheduled_for must be an ISO timestamp, for example "
                "2026-04-20T17:00:00Z."
            ) from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc).isoformat()


class ScheduledPostUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    scheduled_for: str | None = None
    media_url: str | None = Field(default=None, max_length=1000)
    link_url: str | None = Field(default=None, max_length=1000)


@router.get("")
def list_scheduled_posts(
    status_filter: str | None = Query(default=None, alias="status"),
    channel: str = Query(default="all", max_length=40),
    starts_after: str | None = Query(default=None),
    ends_before: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(require_permission("scheduler.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    result = scheduler_service.list_posts(
        company_id=company_id,
        status=status_filter,
        channel=channel,
        starts_after=starts_after,
        ends_before=ends_before,
        limit=limit,
        offset=offset,
    )

    names = auth_service.user_display_names(
        company_id,
        [item.get("created_by_user_id") for item in result["items"]]
        + [item.get("approved_by_user_id") for item in result["items"]],
    )

    for item in result["items"]:
        created_by = item.get("created_by_user_id")
        approved_by = item.get("approved_by_user_id")
        item["created_by_name"] = names.get(int(created_by)) if created_by else None
        item["approved_by_name"] = names.get(int(approved_by)) if approved_by else None

    result["statuses"] = list(STATUSES)
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def create_scheduled_post(
    payload: ScheduledPostCreate,
    request: Request,
    current_user: dict[str, Any] = Depends(require_permission("scheduler.manage")),
):
    company_id = auth_service.resolve_company_id(current_user)

    # A refused account is the caller naming a page this company does not
    # have, which is a bad request rather than a server fault.
    try:
        post = scheduler_service.create_post(
            company_id=company_id,
            channel=payload.channel,
            body=payload.body,
            scheduled_for=payload.scheduled_for,
            media_url=payload.media_url,
            link_url=payload.link_url,
            channel_account_id=payload.channel_account_id,
            created_by_user_id=int(current_user["id"]),
        )
    except SchedulerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # When it goes out and where, not the copy itself. The post is the record
    # of what it says, and it is still editable until it publishes — a copy
    # taken here would be of a draft nobody sent.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.POST_SCHEDULED,
        category="scheduler",
        target_type="scheduled_post",
        target_id=post.get("id"),
        summary=(
            f"Scheduled a {payload.channel} post for {payload.scheduled_for}"
        ),
        after={
            "channel": payload.channel,
            "scheduled_for": payload.scheduled_for,
            "status": post.get("status"),
            "channel_account_id": post.get("channel_account_id"),
        },
        ip_address=client_ip(request),
    )

    return {"status": "created", "post": post}


@router.get("/{post_id}")
def get_scheduled_post(
    post_id: int,
    current_user: dict[str, Any] = Depends(require_permission("scheduler.view")),
):
    company_id = auth_service.resolve_company_id(current_user)
    post = scheduler_service.get_post(company_id=company_id, post_id=post_id)

    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found.")

    return post


@router.patch("/{post_id}")
def update_scheduled_post(
    post_id: int,
    payload: ScheduledPostUpdate,
    current_user: dict[str, Any] = Depends(require_permission("scheduler.manage")),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        post = scheduler_service.update_post(
            company_id=company_id,
            post_id=post_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except SchedulerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not post:
        raise HTTPException(
            status_code=409,
            detail="This post cannot be edited. It may already have been published.",
        )

    return {"status": "updated", "post": post}


@router.post("/{post_id}/approve")
def approve_scheduled_post(
    post_id: int,
    request: Request,
    current_user: dict[str, Any] = Depends(require_permission("scheduler.manage")),
):
    company_id = auth_service.resolve_company_id(current_user)

    if not scheduler_service.approve(
        company_id=company_id,
        post_id=post_id,
        approver_user_id=int(current_user["id"]),
    ):
        raise HTTPException(
            status_code=409,
            detail="Only a draft or a failed post can be approved.",
        )

    post = scheduler_service.get_post(company_id=company_id, post_id=post_id)

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.POST_APPROVED,
        category="scheduler",
        target_type="scheduled_post",
        target_id=post_id,
        summary=(
            f"Approved a {(post or {}).get('channel')} post for "
            f"{(post or {}).get('scheduled_for')}"
        ),
        after={
            "channel": (post or {}).get("channel"),
            "scheduled_for": (post or {}).get("scheduled_for"),
            "status": (post or {}).get("status"),
        },
        ip_address=client_ip(request),
    )

    return {"status": "approved", "post": post}


@router.post("/{post_id}/cancel")
def cancel_scheduled_post(
    post_id: int,
    current_user: dict[str, Any] = Depends(require_permission("scheduler.manage")),
):
    company_id = auth_service.resolve_company_id(current_user)

    if not scheduler_service.cancel(company_id=company_id, post_id=post_id):
        raise HTTPException(
            status_code=409,
            detail="This post cannot be cancelled. It may already have been published.",
        )

    return {"status": "cancelled"}
