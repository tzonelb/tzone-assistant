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


def send_whatsapp_text(to, text, buttons=None):
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp credentials missing.")
        return {"sent": False, "reason": "missing_credentials"}

    url = (
        f"https://graph.facebook.com/"
        f"{config.WHATSAPP_API_VERSION}/"
        f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
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
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
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