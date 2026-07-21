from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse

from config.settings import config
from gateway.message_gateway import message_gateway
from channels.whatsapp.sender import send_whatsapp_text
from channels.whatsapp.session import whatsapp_options

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp"])


@router.get("/")
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "")

    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/")
async def receive_message(request: Request):
    data = await request.json()
    print("WHATSAPP POST RECEIVED")
    print(data)

    try:
        entry = data.get("entry", [])
        if not entry:
            return {"status": "ignored", "reason": "no_entry"}

        change = entry[0].get("changes", [{}])[0]
        value = change.get("value", {})

        metadata = value.get("metadata", {})
        incoming_phone_number_id = str(metadata.get("phone_number_id", ""))

        # Ignore Meta sample payloads, because they use fake phone_number_id like 123456123
        if incoming_phone_number_id != str(config.WHATSAPP_PHONE_NUMBER_ID):
            print("IGNORED SAMPLE OR WRONG PHONE NUMBER ID:", incoming_phone_number_id)
            return {
                "status": "ignored",
                "reason": "wrong_phone_number_id",
                "incoming_phone_number_id": incoming_phone_number_id,
                "expected_phone_number_id": config.WHATSAPP_PHONE_NUMBER_ID,
            }

        messages = value.get("messages", [])
        if not messages:
            return {"status": "ignored", "reason": "no_messages"}

        msg = messages[0]

        if msg.get("type") != "text":
            return {"status": "ignored", "reason": "non_text_message"}

        user_id = msg.get("from")
        text = msg.get("text", {}).get("body", "").strip()

        if not user_id or not text:
            return {"status": "unsupported"}

        resolved_text = whatsapp_options.resolve_message(user_id, text)

        response = message_gateway.handle_text(
            channel="whatsapp",
            user_id=user_id,
            message=resolved_text,
        )

        whatsapp_options.save_options(user_id, response.buttons)

        send_result = send_whatsapp_text(
            to=user_id,
            text=response.text,
            buttons=response.buttons,
        )

        print("SEND RESULT:", send_result)

        return {
            "status": "received",
            "from": user_id,
            "text": text,
            "sent": send_result,
        }

    except Exception as e:
        print("WHATSAPP WEBHOOK ERROR:", str(e))
        return {
            "status": "error",
            "detail": str(e),
        }