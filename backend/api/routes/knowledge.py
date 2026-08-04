from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.services.auth_service import auth_service, get_current_user
from core.knowledge_manager import knowledge_manager


router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


class FAQItem(BaseModel):
    id: str
    title_ar: Optional[str] = None
    title_en: str
    body_ar: Optional[str] = None
    body_en: Optional[str] = None
    category: Optional[str] = None
    enabled: bool = True


def _require_knowledge_access(
    current_user: dict[str, Any],
    company_id: int,
    permission_code: str,
) -> None:
    allowed = auth_service.has_permission(
        user_id=current_user["id"],
        company_id=company_id,
        permission_code=permission_code,
        is_super_admin=bool(current_user.get("is_super_admin")),
    )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have knowledge base access.",
        )


@router.get("/faqs")
def list_all_faqs(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Every FAQ/knowledge item for the caller's company, across all
    departments. Powers the AI Teaching management page, which groups the
    whole company-scoped set by department and category. Scoped to the
    caller's resolved company_id -- never another tenant's rows."""
    company_id = auth_service.resolve_company_id(current_user)
    _require_knowledge_access(current_user, company_id, "knowledge.view")

    return knowledge_manager.list_all_faqs(company_id)


@router.get("/{service}/faqs")
def list_faqs(
    service: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_knowledge_access(current_user, company_id, "knowledge.view")

    return knowledge_manager.list_faqs(company_id, service)


@router.get("/{service}/faqs/{faq_id}")
def get_faq(
    service: str,
    faq_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_knowledge_access(current_user, company_id, "knowledge.view")

    faq = knowledge_manager.get_faq(company_id, service, faq_id)

    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")

    return faq


@router.post("/{service}/faqs")
def save_faq(
    service: str,
    faq: FAQItem,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_knowledge_access(current_user, company_id, "knowledge.manage")

    try:
        saved = knowledge_manager.save_faq(
            company_id,
            service,
            faq.model_dump(),
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "message": "FAQ saved",
        "faq": saved
    }


@router.delete("/{service}/faqs/{faq_id}")
def delete_faq(
    service: str,
    faq_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_knowledge_access(current_user, company_id, "knowledge.manage")

    deleted = knowledge_manager.delete_faq(company_id, service, faq_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="FAQ not found")

    return {
        "message": "FAQ deleted"
    }
