from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse

from config.settings import config
from channels.whatsapp.processor import process_whatsapp_message
from backend.services.channel_account_service import channel_account_service

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

        # Status updates (sent/delivered/read) arrive in their own
        # array, separate from actual messages — record them and move
        # on; this never touches the message-receiving flow below.
        statuses = value.get("statuses", [])
        if statuses:
            from backend.services.message_status_service import message_status_service
            for status_event in statuses:
                provider_message_id = status_event.get("id")
                status_value = status_event.get("status")
                if provider_message_id and status_value in ("sent", "delivered", "read"):
                    message_status_service.update_status(
                        channel="whatsapp", provider_message_id=provider_message_id, status=status_value,
                    )
            if not value.get("messages"):
                return {"status": "ok", "reason": "status_update_only"}

        # Multi-tenant: does this phone_number_id belong to a company
        # that connected its own WhatsApp? If not, fall back to the
        # legacy single .env-configured number, and ignore anything
        # that matches neither (e.g. Meta's sample payloads with fake
        # ids like 123456123).
        account_match = channel_account_service.resolve_meta_account(
            recipient_id=incoming_phone_number_id, channel="whatsapp",
        )
        is_legacy_number = (
            incoming_phone_number_id == str(config.WHATSAPP_PHONE_NUMBER_ID)
            and incoming_phone_number_id != ""
        )
        if not account_match and not is_legacy_number:
            print("IGNORED SAMPLE OR UNKNOWN PHONE NUMBER ID:", incoming_phone_number_id)
            return {
                "status": "ignored",
                "reason": "unknown_phone_number_id",
                "incoming_phone_number_id": incoming_phone_number_id,
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

        contacts = value.get("contacts", [])
        customer_name = contacts[0].get("profile", {}).get("name") if contacts else None

        result = process_whatsapp_message(
            user_id=user_id,
            text=text,
            recipient_phone_number_id=incoming_phone_number_id,
            customer_name=customer_name,
        )

        return {
            "status": "received",
            "from": user_id,
            "text": text,
            "company_id": result["company_id"],
            "queued": result["queue_result"].get("queued"),
        }

    except Exception as e:
        print("WHATSAPP WEBHOOK ERROR:", str(e))
        return {
            "status": "error",
            "detail": str(e),
        }
