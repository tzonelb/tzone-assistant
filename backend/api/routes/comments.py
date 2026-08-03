from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.channel_account_service import channel_account_service
from backend.services.comment_service import comment_service
from backend.services.platform_admin_service import platform_admin_service


router = APIRouter(prefix="/api/comments", tags=["Community Comments"])


def _company_id(current_user: dict[str, Any]) -> int:
    company_id = int(auth_service.resolve_company_id(current_user))
    if not current_user.get("is_super_admin") and not platform_admin_service.is_module_enabled(company_id=company_id, module="comments"):
        raise HTTPException(status_code=403, detail="The Comments module is not enabled for this company.")
    return company_id


class ReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.get("/posts")
def list_posts(
    channel_account_id: int | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "modules.comments")
    posts = comment_service.list_posts(company_id=company_id, channel_account_id=channel_account_id)
    accounts = {a["id"]: a for a in channel_account_service.list_for_company(company_id=company_id)}
    for post in posts:
        acct = accounts.get(post.get("channel_account_id"))
        post["channel_account_name"] = acct["name"] if acct else None
    return {
        "posts": posts,
        "unanswered_total": comment_service.unanswered_total(company_id=company_id),
    }


@router.get("/posts/{post_external_id}/comments")
def list_comments(post_external_id: str, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "modules.comments")
    return {"comments": comment_service.list_comments(company_id=company_id, post_external_id=post_external_id)}


@router.post("/{comment_id}/reply")
def reply(comment_id: int, payload: ReplyRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "modules.comments")
    try:
        return comment_service.reply_to_comment(
            company_id=company_id, comment_id=comment_id, text=payload.text,
            actor_user_id=current_user.get("id"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Comment not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
