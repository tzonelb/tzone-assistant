from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from core.instruction_service import instruction_service


router = APIRouter(prefix="/api/instructions", tags=["AI Instructions"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


class CreateInstructionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    tags: list[str] = []


class UpdateInstructionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    tags: list[str] | None = None


class ReorderInstructionsRequest(BaseModel):
    ordered_ids: list[int]


@router.get("")
def list_instructions(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    return {"instructions": instruction_service.list_for_company(company_id=company_id)}


@router.post("")
def create_instruction(
    payload: CreateInstructionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return instruction_service.create(
            company_id=company_id, text=payload.text, tags=payload.tags, actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{instruction_id}")
def update_instruction(
    instruction_id: int,
    payload: UpdateInstructionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return instruction_service.update(company_id=company_id, instruction_id=instruction_id, text=payload.text, tags=payload.tags)
    except KeyError:
        raise HTTPException(status_code=404, detail="Instruction not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{instruction_id}")
def delete_instruction(
    instruction_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        instruction_service.delete(company_id=company_id, instruction_id=instruction_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Instruction not found")
    return {"status": "deleted"}


@router.post("/reorder")
def reorder_instructions(
    payload: ReorderInstructionsRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    return {"instructions": instruction_service.reorder(company_id=company_id, ordered_ids=payload.ordered_ids)}
