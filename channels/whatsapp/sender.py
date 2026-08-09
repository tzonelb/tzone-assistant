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


def _resolve_qr_session_key(company_id=None):
    """A company that paired WhatsApp by QR (WhatsApp Web bridge — no
    Meta developer app) sends through the bridge. The company's own
    Cloud API account wins when both are connected (official transport),
    but a QR session beats the platform-wide .env fallback — otherwise a
    QR-only company's replies would silently go out from the platform's
    default number."""
    if company_id is None:
        return None
    from backend.services.channel_account_service import channel_account_service
    with __import__("database.database", fromlist=["db"]).db.connect() as conn:
        cloud = conn.execute(
            "SELECT id FROM channel_accounts "
            "WHERE company_id = ? AND channel = 'whatsapp' AND status = 'active' "
            "AND access_token_encrypted IS NOT NULL AND phone_number_id IS NOT NULL LIMIT 1",
            (company_id,),
        ).fetchone()
    if cloud:
        # A usable Cloud API account exists (matches _resolve_whatsapp_credentials'
        # own requirements) — official transport wins.
        return None
    account = channel_account_service.get_qr_account(company_id=company_id)
    return account["external_account_id"] if account else None


def send_whatsapp_text(to, text, buttons=None, company_id=None):
    session_key = _resolve_qr_session_key(company_id)
    if session_key:
        from channels.whatsapp_qr import service as wa_bridge
        return wa_bridge.send_text(session_key, to, format_whatsapp_message(text, buttons))

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
    session_key = _resolve_qr_session_key(company_id)
    if session_key:
        # Bridge v1 is text-only: deliver media as a link with the
        # caption, which WhatsApp renders with a preview.
        from channels.whatsapp_qr import service as wa_bridge
        text = f"{caption}\n{media_url}" if caption else media_url
        return wa_bridge.send_text(session_key, to, text)

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
