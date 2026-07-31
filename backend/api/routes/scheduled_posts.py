from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.schemas.scheduled_posts import ScheduledPostCreateRequest, ScheduledPostUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.channel_account_service import channel_account_service
from backend.services.scheduled_post_service import POST_CHANNELS, STATUSES, scheduled_post_service


router = APIRouter(prefix="/api/scheduled-posts", tags=["Scheduled Posts"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.get("/options")
def scheduled_post_options(context=Depends(current_context)):
    _, company_id = context
    accounts = channel_account_service.list_for_company(company_id=company_id)
    postable = [account for account in accounts if account["channel"] in POST_CHANNELS]
    return {"statuses": STATUSES, "channel_accounts": postable}


@router.post("")
def create_post(payload: ScheduledPostCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    try:
        return scheduled_post_service.create_post(
            company_id=company_id,
            text=payload.text,
            channel_account_ids=payload.channel_account_ids,
            media_urls=payload.media_urls,
            media_type=payload.media_type,
            status=payload.status,
            scheduled_at=payload.scheduled_at,
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_posts(status: str | None = Query(default=None), context=Depends(current_context)):
    _, company_id = context
    return scheduled_post_service.list_posts(company_id=company_id, status=status)


@router.get("/{post_id}")
def get_post(post_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        return scheduled_post_service.get_post(company_id=company_id, post_id=post_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{post_id}")
def update_post(post_id: int, payload: ScheduledPostUpdateRequest, context=Depends(current_context)):
    _, company_id = context
    try:
        return scheduled_post_service.update_post(
            company_id=company_id, post_id=post_id, values=payload.model_dump(exclude_unset=True),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{post_id}/publish-now")
def publish_now(post_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        return scheduled_post_service.publish_post(company_id=company_id, post_id=post_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{post_id}")
def delete_post(post_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        scheduled_post_service.delete_post(company_id=company_id, post_id=post_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
