"""Broadcast campaigns over HTTP.

The paths, the verbs and the permissions are the design branch's
(`backend/api/routes/broadcasts.py` on
`origin/fix/release-timeout-and-channel-fixes`): reading a campaign or its
report takes `channels.view`, and creating, sending or deleting one takes
`channels.manage`. A broadcast is the company's own channel speaking to its
customers, so it is guarded by the permission that decides who may operate
those channels.

How the permission is enforced is this platform's, not theirs. There it was
`Depends(get_current_user)` with an `auth_service.require_permission(...)`
call inside each handler; here it is the `require_permission` dependency every
other router uses, so `tests/test_route_exposure.py` can see the guard on the
route rather than having to read the body. The module switch
(`require_module("broadcast")`) is applied where every module switch is
applied, at `include_router` in `main.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.schemas.broadcasts import BroadcastCreateRequest
from backend.services.auth_service import auth_service, require_permission
from backend.services.broadcast_service import broadcast_service


router = APIRouter(prefix="/api/broadcasts", tags=["Broadcasts"])


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("channels.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("channels.manage"))):
    return _context(current_user)


@router.post("")
def create_broadcast(
    payload: BroadcastCreateRequest, context=Depends(manage_context)
):
    current_user, company_id = context

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("")
def list_broadcasts(context=Depends(view_context)):
    _, company_id = context

    return {"items": broadcast_service.list_broadcasts(company_id=company_id)}


@router.get("/{broadcast_id}")
def get_broadcast(broadcast_id: int, context=Depends(view_context)):
    _, company_id = context

    try:
        return broadcast_service.get_broadcast(
            company_id=company_id, broadcast_id=broadcast_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/{broadcast_id}/report")
def get_broadcast_report(broadcast_id: int, context=Depends(view_context)):
    _, company_id = context

    try:
        return broadcast_service.get_broadcast_report(
            company_id=company_id, broadcast_id=broadcast_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/{broadcast_id}/recipient-count")
def preview_broadcast_recipient_count(
    broadcast_id: int, context=Depends(view_context)
):
    _, company_id = context

    try:
        count = broadcast_service.preview_recipient_count(
            company_id=company_id, broadcast_id=broadcast_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return {"recipient_count": count}


@router.post("/{broadcast_id}/send")
def send_broadcast(broadcast_id: int, context=Depends(manage_context)):
    _, company_id = context

    try:
        return broadcast_service.send_broadcast(
            company_id=company_id, broadcast_id=broadcast_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.delete("/{broadcast_id}")
def delete_broadcast(broadcast_id: int, context=Depends(manage_context)):
    _, company_id = context

    try:
        broadcast_service.delete_broadcast(
            company_id=company_id, broadcast_id=broadcast_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return {"deleted": True}
