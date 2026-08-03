from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.broadcast_service import (
    BroadcastAlreadyRunning,
    BroadcastNotFound,
    broadcast_service,
)


router = APIRouter(prefix="/api/broadcasts", tags=["Broadcasts"])


class BroadcastCreateRequest(BaseModel):
    channel: str
    message_text: str = Field(min_length=1, max_length=2000)
    target_department: str | None = None


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def _require_permission(
    current_user: dict[str, Any],
    company_id: int,
    *permission_codes: str,
) -> None:
    if current_user.get("is_super_admin"):
        return

    for permission_code in permission_codes:
        if auth_service.has_permission(
            current_user["id"], company_id, permission_code, False
        ):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "You do not have permission to perform this action. "
            f"Required permission: {' or '.join(permission_codes)}."
        ),
    )


@router.post("")
def create_broadcast(
    payload: BroadcastCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_permission(current_user, company_id, "channels.manage")

    try:
        return broadcast_service.create_broadcast(
            company_id=company_id,
            channel=payload.channel,
            message_text=payload.message_text.strip(),
            target_department=payload.target_department,
            actor_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/{broadcast_id}/send")
def send_or_resume_broadcast(
    broadcast_id: int,
    background_tasks: BackgroundTasks,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_permission(current_user, company_id, "channels.manage")

    try:
        broadcast = broadcast_service.start_or_resume_broadcast(
            broadcast_id=broadcast_id,
            company_id=company_id,
        )
    except BroadcastNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except BroadcastAlreadyRunning as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This broadcast is already "
                f"'{exc.status}'. It cannot be started again."
            ),
        ) from exc

    background_tasks.add_task(
        broadcast_service.run_send_loop,
        broadcast_id=broadcast_id,
        company_id=company_id,
        actor_user_id=int(current_user["id"]),
    )

    return broadcast


@router.get("/{broadcast_id}")
def get_broadcast(
    broadcast_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_permission(current_user, company_id, "channels.view", "channels.manage")

    try:
        return broadcast_service.get_broadcast(broadcast_id, company_id)
    except BroadcastNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("")
def list_broadcasts(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_permission(current_user, company_id, "channels.view", "channels.manage")

    return broadcast_service.list_broadcasts(company_id)
