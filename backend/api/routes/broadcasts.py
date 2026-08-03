from fastapi import APIRouter, Depends, HTTPException

from backend.api.schemas.broadcasts import BroadcastCreateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.broadcast_service import broadcast_service


router = APIRouter(prefix="/api/broadcasts", tags=["Broadcasts"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.post("")
def create_broadcast(payload: BroadcastCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        return broadcast_service.create_broadcast(
            company_id=company_id,
            name=payload.name,
            message_text=payload.message_text,
            channel=payload.channel,
            segment_id=payload.segment_id,
            lifecycle_stage=payload.lifecycle_stage,
            tag=payload.tag,
            numbers=payload.numbers,
            media_url=payload.media_url,
            media_type=payload.media_type,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_broadcasts(context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "channels.view")
    return {"items": broadcast_service.list_broadcasts(company_id=company_id)}


@router.get("/{broadcast_id}")
def get_broadcast(broadcast_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "channels.view")
    try:
        return broadcast_service.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{broadcast_id}/report")
def get_broadcast_report(broadcast_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "channels.view")
    try:
        return broadcast_service.get_broadcast_report(company_id=company_id, broadcast_id=broadcast_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{broadcast_id}/recipient-count")
def preview_broadcast_recipient_count(broadcast_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "channels.view")
    try:
        count = broadcast_service.preview_recipient_count(company_id=company_id, broadcast_id=broadcast_id)
        return {"recipient_count": count}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{broadcast_id}/send")
def send_broadcast(broadcast_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        return broadcast_service.send_broadcast(company_id=company_id, broadcast_id=broadcast_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{broadcast_id}")
def delete_broadcast(broadcast_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        broadcast_service.delete_broadcast(company_id=company_id, broadcast_id=broadcast_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": True}
