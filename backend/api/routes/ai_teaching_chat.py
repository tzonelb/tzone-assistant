from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.ai_teaching_chat_service import ai_teaching_chat_service


router = APIRouter(prefix="/api/ai-teaching-chat", tags=["AI Teaching Chat"])


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TestReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    channel: str = Field(default="website", max_length=40)
    department: str | None = Field(default=None, max_length=80)


def _require_access(current_user: dict[str, Any]) -> int:
    company_id = auth_service.resolve_company_id(current_user)
    allowed = auth_service.has_permission(
        user_id=current_user.get("id"),
        company_id=company_id,
        permission_code="modules.ai_teaching_chat",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to use AI Teaching Chat. Ask an owner/admin to grant it from Roles & Permissions.",
        )
    return company_id


@router.get("")
def list_messages(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _require_access(current_user)
    return {"messages": ai_teaching_chat_service.list_messages(company_id=company_id)}


@router.post("")
def send_message(
    payload: SendMessageRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _require_access(current_user)
    try:
        return ai_teaching_chat_service.send_message(
            company_id=company_id, actor_user_id=current_user.get("id"), text=payload.text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _run_pipeline(*, company_id: int, message: str, channel: str, department: str | None) -> dict[str, Any]:
    """Runs a typed message through the EXACT SAME reply pipeline real
    customer messages go through (core/engine.py's own sequence:
    knowledge_manager -> ai_knowledge_matcher -> ai_router), scoped to
    the given channel/department, so what's shown here is genuinely
    what a customer would see — not a separate, simplified simulation.
    Nothing is persisted: no conversation, no customer, no message row.
    Shared by /test (admin diagnostic view) and /chat-with-bot (the
    plain employee-facing chat), so both stay behind the exact same
    pipeline and never drift apart."""
    from core.ai_knowledge_matcher import ai_knowledge_matcher
    from core.ai_router import ai_router
    from core.instruction_service import instruction_service
    from core.knowledge_manager import knowledge_manager

    channel = (channel or "website").strip().lower()
    department = (department or "").strip().lower() or None
    context_tags = [channel] + ([department] if department else [])

    knowledge_items = knowledge_manager.list_for_ai(company_id, department=department, context_tags=context_tags)
    match_result = ai_knowledge_matcher.match(message=message, language=None, items=knowledge_items, context={}, max_results=3)
    selected_knowledge = ai_knowledge_matcher.select_items(match_result, knowledge_items)
    instructions = instruction_service.list_texts_for_ai(company_id, context_tags=context_tags)

    ai_result = ai_router.route(
        message=message,
        channel=channel,
        user_id="test-mode",
        context={},
        knowledge=selected_knowledge,
        company_id=company_id,
        instructions=instructions,
    )

    if not ai_result:
        raise HTTPException(status_code=502, detail="The AI did not return a reply — check that OPENAI_API_KEY is configured.")

    return {
        "reply": ai_result.get("reply") or ai_result.get("text"),
        "department_detected": ai_result.get("department") or match_result.get("department"),
        "knowledge_used": [item.get("title") for item in selected_knowledge],
        "instructions_used": instructions,
    }


@router.post("/test")
def test_reply(payload: TestReplyRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    """Admin-only diagnostic view: same reply pipeline as /chat-with-bot,
    plus the department/knowledge match info an admin needs to debug why
    the AI answered the way it did."""
    company_id = _require_access(current_user)
    return _run_pipeline(company_id=company_id, message=payload.message, channel=payload.channel, department=payload.department)


@router.post("/chat-with-bot")
def chat_with_bot(payload: TestReplyRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    """Open to every employee of the company (no modules.ai_teaching_chat
    permission needed) — a plain, no-diagnostics chat so any employee can
    see how their company's AI would respond to a customer, same real
    pipeline as the admin /test tool. Nothing is persisted here either."""
    company_id = auth_service.resolve_company_id(current_user)
    result = _run_pipeline(company_id=company_id, message=payload.message, channel=payload.channel, department=payload.department)
    return {"reply": result["reply"]}
