from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from core.knowledge_manager import knowledge_manager


router = APIRouter(prefix="/api/knowledge", tags=["AI Teaching & Knowledge"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


class CreateKnowledgeEntryRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    department: str = "Unassigned"
    tags: list[str] = []


class UpdateKnowledgeEntryRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    department: str | None = None
    tags: list[str] | None = None


@router.get("")
def list_knowledge(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    return {"entries": knowledge_manager.list_for_company(company_id=company_id)}


@router.post("")
def create_knowledge_entry(
    payload: CreateKnowledgeEntryRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return knowledge_manager.create(
            company_id=company_id, title=payload.title, content=payload.content,
            department=payload.department, tags=payload.tags, actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{entry_id}")
def update_knowledge_entry(
    entry_id: int,
    payload: UpdateKnowledgeEntryRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return knowledge_manager.update(
            company_id=company_id, entry_id=entry_id,
            title=payload.title, content=payload.content, department=payload.department, tags=payload.tags,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")


@router.delete("/{entry_id}")
def delete_knowledge_entry(
    entry_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        knowledge_manager.delete(company_id=company_id, entry_id=entry_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return {"status": "deleted"}
