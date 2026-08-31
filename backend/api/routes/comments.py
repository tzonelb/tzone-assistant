"""The post-comment queue.

Replaces the deleted page that rendered two invented comments from a hardcoded
array. These are real comments from the company's own posts, and replying here
actually publishes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.comment_service import STATUSES, comment_service
from channels.comment_sender import publish_comment_reply


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comments", tags=["Comments"])


class CommentReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class CommentStatusRequest(BaseModel):
    status: Literal["open", "answered", "ignored"]


@router.get("")
def list_comments(
    status_filter: str | None = Query(default=None, alias="status"),
    channel: str = Query(default="all", max_length=40),
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(require_permission("comments.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    return comment_service.list_comments(
        company_id=company_id,
        status=status_filter,
        channel=channel,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/{comment_id}")
def get_comment(
    comment_id: int,
    current_user: dict[str, Any] = Depends(require_permission("comments.view")),
):
    company_id = auth_service.resolve_company_id(current_user)
    comment = comment_service.get_comment(company_id=company_id, comment_id=comment_id)

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found.")

    names = auth_service.user_display_names(
        company_id,
        [reply.get("author_user_id") for reply in comment["replies"]],
    )

    for reply in comment["replies"]:
        author_id = reply.get("author_user_id")
        reply["author_name"] = (
            names.get(int(author_id)) if author_id else "Assistant"
        )

    return comment


@router.post("/{comment_id}/reply")
def reply_to_comment(
    comment_id: int,
    payload: CommentReplyRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(require_permission("comments.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)
    comment = comment_service.get_comment(company_id=company_id, comment_id=comment_id)

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found.")

    result = publish_comment_reply(
        company_id=company_id,
        channel=comment["channel"],
        provider_comment_id=comment["provider_comment_id"],
        message=payload.message.strip(),
    )

    # Recorded either way. A failed publish keeps the employee's text and leaves
    # the comment open, because it is still public and still unanswered.
    comment_service.record_reply(
        company_id=company_id,
        comment_id=comment_id,
        body=payload.message.strip(),
        author_user_id=int(current_user["id"]),
        provider_reply_id=result.get("provider_reply_id"),
        send_status="sent" if result.get("ok") else "failed",
        error=None if result.get("ok") else str(result.get("error") or result.get("reason")),
    )

    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The reply was saved but could not be published: "
                f"{result.get('error') or result.get('reason')}"
            ),
        )

    # After the publish check, not before it: this entry says the company
    # answered in public, and a reply that only reached the local table did
    # not. The failed attempt is still on the comment thread with its error.
    #
    # Which comment was answered, never the text of it or of the reply — the
    # comment carries a customer's own words and their public account name, and
    # the thread already holds both.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.COMMENT_REPLIED,
        category="comments",
        target_type="comment",
        target_id=comment_id,
        summary=f"Replied to a {comment['channel']} comment",
        after={
            "channel": comment["channel"],
            "provider_reply_id": result.get("provider_reply_id"),
        },
        ip_address=client_ip(request),
    )

    return {
        "status": "published",
        "comment": comment_service.get_comment(
            company_id=company_id, comment_id=comment_id
        ),
    }


@router.patch("/{comment_id}/status")
def update_comment_status(
    comment_id: int,
    payload: CommentStatusRequest,
    current_user: dict[str, Any] = Depends(require_permission("comments.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)

    if not comment_service.set_status(
        company_id=company_id, comment_id=comment_id, status=payload.status
    ):
        raise HTTPException(status_code=404, detail="Comment not found.")

    return {"status": "updated", "statuses": list(STATUSES)}
