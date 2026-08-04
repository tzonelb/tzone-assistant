from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.services.auth_service import auth_service, get_current_user
from gateway.message_gateway import message_gateway


router = APIRouter(prefix="/test/whatsapp", tags=["Test WhatsApp"])


class TestWhatsAppMessage(BaseModel):
    user_id: str = "test_whatsapp_user"
    message: str


@router.post("/")
def test_whatsapp_message(
    payload: TestWhatsAppMessage,
    # SECURITY FIX: this debug endpoint drives the real message-handling
    # engine with fully attacker-controlled input. It must not be a public,
    # unauthenticated way to invoke it — require a logged-in user, matching
    # the auth convention used by every other route in this codebase.
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)

    print("=" * 50)
    print("TEST WHATSAPP ENDPOINT CALLED")
    print(f"User ID : {payload.user_id}")
    print(f"Message : {payload.message}")
    print(f"Company ID : {company_id}")

    response = message_gateway.handle_text(
        channel="whatsapp",
        user_id=payload.user_id,
        message=payload.message,
        company_id=company_id,
    )

    print("Reply:")
    print(response.text)
    print("Buttons:")
    print(response.buttons)
    print("=" * 50)

    return {
        "incoming": payload.message,
        "reply": response.text,
        "buttons": response.buttons,
        "company_id": company_id,
    }