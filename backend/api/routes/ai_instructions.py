"""AI Instructions: the company's behaviour rules for its assistant.

Reading needs `settings.manage`, the same gate the rest of the assistant's
configuration uses -- these rules shape every reply, so changing them is an
owner-level act, not something any signed-in employee should do.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, require_permission
from backend.services.instruction_service import (
    InstructionError,
    instruction_service,
)


router = APIRouter(prefix="/api/ai-instructions", tags=["AI Instructions"])


def _manage(current_user: dict[str, Any] = Depends(require_permission("settings.manage"))) -> int:
    return auth_service.resolve_company_id(current_user)


class InstructionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list)


class InstructionUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list)


class ReorderRequest(BaseModel):
    ordered_ids: list[int] = Field(default_factory=list)


@router.get("")
def list_instructions(company_id: int = Depends(_manage)) -> dict[str, Any]:
    return {"instructions": instruction_service.list(company_id)}


@router.post("", status_code=201)
def create_instruction(
    payload: InstructionCreate, company_id: int = Depends(_manage)
) -> dict[str, Any]:
    try:
        return instruction_service.create(
            company_id=company_id, text=payload.text, tags=payload.tags
        )
    except InstructionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{instruction_id}")
def update_instruction(
    instruction_id: int,
    payload: InstructionUpdate,
    company_id: int = Depends(_manage),
) -> dict[str, Any]:
    try:
        return instruction_service.update(
            company_id=company_id,
            instruction_id=instruction_id,
            text=payload.text,
            tags=payload.tags,
        )
    except InstructionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{instruction_id}")
def delete_instruction(
    instruction_id: int, company_id: int = Depends(_manage)
) -> dict[str, Any]:
    removed = instruction_service.delete(
        company_id=company_id, instruction_id=instruction_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="That instruction does not exist.")
    return {"deleted": True}


@router.post("/reorder")
def reorder_instructions(
    payload: ReorderRequest, company_id: int = Depends(_manage)
) -> dict[str, Any]:
    instruction_service.reorder(company_id=company_id, ordered_ids=payload.ordered_ids)
    return {"instructions": instruction_service.list(company_id)}
