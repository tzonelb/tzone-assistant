from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.reply_flow_service import reply_flow_service


router = APIRouter(prefix="/api/reply-flows", tags=["Reply Flows"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def _require_manage(current_user: dict[str, Any], company_id: int) -> None:
    # Reply Flows control how the AI behaves company-wide — admin-only for
    # everything, including just viewing, not only editing.
    can_manage = auth_service.has_permission(
        user_id=current_user.get("id"), company_id=company_id,
        permission_code="users.manage", is_super_admin=bool(current_user.get("is_super_admin")),
    )
    if not can_manage:
        raise HTTPException(status_code=403, detail="Only company admins can access reply flows.")


class CreateFlowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    channels: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)


class UpdateFlowRequest(BaseModel):
    name: str | None = None
    channels: list[str] | None = None
    departments: list[str] | None = None
    status: str | None = None
    nodes: list[dict] | None = None
    edges: list[dict] | None = None


@router.get("")
def list_flows(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_manage(current_user, company_id)
    return {"flows": reply_flow_service.list_for_company(company_id=company_id)}


@router.get("/{flow_id}")
def get_flow(flow_id: int, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_manage(current_user, company_id)
    try:
        return reply_flow_service.get(company_id=company_id, flow_id=flow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Reply flow not found")


@router.post("")
def create_flow(payload: CreateFlowRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_manage(current_user, company_id)
    try:
        return reply_flow_service.create(
            company_id=company_id, name=payload.name, channels=payload.channels,
            departments=payload.departments, actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{flow_id}")
def update_flow(flow_id: int, payload: UpdateFlowRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_manage(current_user, company_id)
    try:
        return reply_flow_service.update(
            company_id=company_id, flow_id=flow_id, name=payload.name, channels=payload.channels,
            departments=payload.departments, status=payload.status, nodes=payload.nodes, edges=payload.edges,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Reply flow not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{flow_id}")
def delete_flow(flow_id: int, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_manage(current_user, company_id)
    try:
        reply_flow_service.delete(company_id=company_id, flow_id=flow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Reply flow not found")
    return {"status": "deleted"}


@router.post("/{flow_id}/duplicate")
def duplicate_flow(flow_id: int, current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_manage(current_user, company_id)
    try:
        return reply_flow_service.duplicate(company_id=company_id, flow_id=flow_id, actor_user_id=current_user.get("id"))
    except KeyError:
        raise HTTPException(status_code=404, detail="Reply flow not found")
