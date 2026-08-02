import logging
import httpx
from config.settings import config

logger = logging.getLogger(__name__)


def format_whatsapp_message(text, buttons=None):
    if not buttons:
        return text

    msg = text + "\n\nاختر رقم من الخيارات:\n"
    for i, button in enumerate(buttons, start=1):
        msg += f"{i}. {button}\n"

    return msg


def _resolve_whatsapp_credentials(company_id=None):
    """Company's own connected WhatsApp number/token if one exists,
    otherwise the platform-wide .env config (keeps existing
    single-tenant setups working unchanged)."""
    phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
    access_token = config.WHATSAPP_ACCESS_TOKEN

    if company_id is not None:
        from backend.services.channel_account_service import channel_account_service
        with __import__("database.database", fromlist=["db"]).db.connect() as conn:
            row = conn.execute(
                "SELECT id, phone_number_id FROM channel_accounts "
                "WHERE company_id = ? AND channel = 'whatsapp' AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 1",
                (company_id,),
            ).fetchone()
        if row and row["phone_number_id"]:
            try:
                company_token = channel_account_service.get_decrypted_token(account_id=row["id"])
            except (KeyError, ValueError):
                company_token = None
            if company_token:
                phone_number_id = row["phone_number_id"]
                access_token = company_token

    return phone_number_id, access_token


def send_whatsapp_text(to, text, buttons=None, company_id=None):
    phone_number_id, access_token = _resolve_whatsapp_credentials(company_id)

    if not access_token or not phone_number_id:
        logger.warning("WhatsApp credentials missing.")
        return {"sent": False, "reason": "missing_credentials"}

    url = (
        f"https://graph.facebook.com/"
        f"{config.WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "text",
        "text": {
            "body": format_whatsapp_message(text, buttons)
        }
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = httpx.post(url, json=payload, headers=headers, timeout=20)

    print("STATUS:", response.status_code)
    print("BODY:", response.text)
    return {
    "sent": response.status_code < 400,
    "status_code": response.status_code,
    "response": response.json() if response.text else {}
    }


# WhatsApp Cloud API accepts "image", "video", "audio", and "document"
# message types, each taking a public HTTPS "link" (no upload step
# needed on our side). Captions are supported on image/video/document but
# not on audio — WhatsApp silently ignores a caption field there, so it's
# only included for the types that use it. Documents also take an
# optional "filename" shown in the chat bubble instead of the raw URL.
def send_whatsapp_media(to, media_url, media_type, caption=None, company_id=None, filename=None):
    phone_number_id, access_token = _resolve_whatsapp_credentials(company_id)

    if not access_token or not phone_number_id:
        logger.warning("WhatsApp credentials missing.")
        return {"sent": False, "reason": "missing_credentials"}

    if media_type not in ("image", "video", "audio", "document"):
        return {"sent": False, "reason": f"unsupported_media_type:{media_type}"}

    url = (
        f"https://graph.facebook.com/"
        f"{config.WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )

    media_payload = {"link": media_url}
    if caption and media_type in ("image", "video", "document"):
        media_payload["caption"] = caption
    if filename and media_type == "document":
        media_payload["filename"] = filename

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": media_type,
        media_type: media_payload,
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = httpx.post(url, json=payload, headers=headers, timeout=20)

    return {
        "sent": response.status_code < 400,
        "status_code": response.status_code,
        "response": response.json() if response.text else {}
    }
