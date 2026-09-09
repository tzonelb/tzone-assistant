"""Reply Flows: the company's scripted, step-by-step conversations.

Managing flows is an owner-level act -- an active flow replaces the default AI
reply for matching customers -- so every route is behind ``settings.manage``,
the same gate the rest of the assistant's behaviour uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, require_permission
from backend.services.reply_flow_service import (
    TRIGGER_TYPES,
    ReplyFlowError,
    reply_flow_service,
)
from backend.services.reply_flow_generator import reply_flow_generator


router = APIRouter(prefix="/api/reply-flows", tags=["Reply Flows"])


def _manage(current_user: dict[str, Any] = Depends(require_permission("settings.manage"))) -> int:
    return auth_service.resolve_company_id(current_user)


class FlowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    channels: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    reply_modes: list[str] = Field(default_factory=list)


class FlowUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    status: str = "draft"
    channels: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    reply_modes: list[str] = Field(default_factory=list)
    trigger_type: str = "new_conversation"
    trigger_config: dict = Field(default_factory=dict)
    nodes: list = Field(default_factory=list)
    edges: list = Field(default_factory=list)


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.get("")
def list_flows(company_id: int = Depends(_manage)) -> dict[str, Any]:
    return {"flows": reply_flow_service.list(company_id)}


@router.get("/trigger-types")
def trigger_types(_company_id: int = Depends(_manage)) -> dict[str, Any]:
    return {"trigger_types": list(TRIGGER_TYPES)}


@router.post("", status_code=201)
def create_flow(payload: FlowCreate, company_id: int = Depends(_manage)) -> dict[str, Any]:
    try:
        return reply_flow_service.create(
            company_id=company_id,
            name=payload.name,
            channels=payload.channels,
            departments=payload.departments,
            reply_modes=payload.reply_modes,
        )
    except ReplyFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{flow_id}")
def get_flow(flow_id: int, company_id: int = Depends(_manage)) -> dict[str, Any]:
    flow = reply_flow_service.get(company_id, flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="That flow does not exist.")
    return flow


@router.put("/{flow_id}")
def update_flow(
    flow_id: int, payload: FlowUpdate, company_id: int = Depends(_manage)
) -> dict[str, Any]:
    try:
        return reply_flow_service.update(
            company_id=company_id,
            flow_id=flow_id,
            name=payload.name,
            status=payload.status,
            channels=payload.channels,
            departments=payload.departments,
            reply_modes=payload.reply_modes,
            trigger_type=payload.trigger_type,
            trigger_config=payload.trigger_config,
            nodes=payload.nodes,
            edges=payload.edges,
        )
    except ReplyFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{flow_id}")
def delete_flow(flow_id: int, company_id: int = Depends(_manage)) -> dict[str, Any]:
    if not reply_flow_service.delete(company_id=company_id, flow_id=flow_id):
        raise HTTPException(status_code=404, detail="That flow does not exist.")
    return {"deleted": True}


@router.post("/{flow_id}/duplicate", status_code=201)
def duplicate_flow(flow_id: int, company_id: int = Depends(_manage)) -> dict[str, Any]:
    try:
        flow = reply_flow_service.duplicate(company_id=company_id, flow_id=flow_id)
    except ReplyFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not flow:
        raise HTTPException(status_code=404, detail="That flow does not exist.")
    return flow


@router.post("/{flow_id}/generate")
def generate_flow(
    flow_id: int, payload: GenerateRequest, company_id: int = Depends(_manage)
) -> dict[str, Any]:
    if reply_flow_service.get(company_id, flow_id) is None:
        raise HTTPException(status_code=404, detail="That flow does not exist.")
    try:
        flow = reply_flow_generator.generate(
            company_id=company_id, flow_id=flow_id, text=payload.text
        )
    except ReplyFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if flow is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "The flow builder AI is not available right now. You can still "
                "build the flow step by step on the canvas."
            ),
        )
    return flow
