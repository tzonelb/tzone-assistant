from fastapi import APIRouter
from pydantic import BaseModel

from gateway.message_gateway import message_gateway


router = APIRouter(prefix="/test/whatsapp", tags=["Test WhatsApp"])


class TestWhatsAppMessage(BaseModel):
    user_id: str = "test_whatsapp_user"
    message: str


@router.post("/")
def test_whatsapp_message(payload: TestWhatsAppMessage):
    print("=" * 50)
    print("TEST WHATSAPP ENDPOINT CALLED")
    print(f"User ID : {payload.user_id}")
    print(f"Message : {payload.message}")

    response = message_gateway.handle_text(
        channel="whatsapp",
        user_id=payload.user_id,
        message=payload.message
    )

    print("Reply:")
    print(response.text)
    print("Buttons:")
    print(response.buttons)
    print("=" * 50)

    return {
        "incoming": payload.message,
        "reply": response.text,
        "buttons": response.buttons
    }