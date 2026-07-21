from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.knowledge_manager import knowledge_manager


router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


class FAQItem(BaseModel):
    id: str
    title_ar: str
    title_en: str
    body_ar: str
    body_en: str
    category: Optional[str] = None
    enabled: bool = True


@router.get("/{service}/faqs")
def list_faqs(service: str):
    return knowledge_manager.list_faqs(service)


@router.get("/{service}/faqs/{faq_id}")
def get_faq(service: str, faq_id: str):
    faq = knowledge_manager.get_faq(service, faq_id)

    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")

    return faq


@router.post("/{service}/faqs")
def save_faq(service: str, faq: FAQItem):
    saved = knowledge_manager.save_faq(service, faq.model_dump())

    return {
        "message": "FAQ saved",
        "faq": saved
    }


@router.delete("/{service}/faqs/{faq_id}")
def delete_faq(service: str, faq_id: str):
    deleted = knowledge_manager.delete_faq(service, faq_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="FAQ not found")

    return {
        "message": "FAQ deleted"
    }