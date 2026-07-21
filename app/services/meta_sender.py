import httpx
from app.config import settings


async def send_meta_message(platform: str, recipient_id: str, text: str):
    if platform == "messenger":
        access_token = settings.META_PAGE_ACCESS_TOKEN
        url = "https://graph.facebook.com/v20.0/me/messages"

    elif platform == "instagram":
        access_token = settings.META_INSTAGRAM_ACCESS_TOKEN
        url = "https://graph.facebook.com/v20.0/me/messages"

    else:
        return {"status": "unsupported_platform"}

    payload = {
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "text": text
        },
        "messaging_type": "RESPONSE"
    }

    params = {
        "access_token": access_token
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, params=params, json=payload)

    return {
        "status_code": response.status_code,
        "response": response.json() if response.content else None
    }