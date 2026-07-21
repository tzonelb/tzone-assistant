from fastapi import APIRouter, Request, HTTPException
import json

from channels.meta.processor import process_meta_payload
from channels.meta.logger import log_meta_event
from config.settings import config


router = APIRouter(prefix="/webhook", tags=["Meta Webhook"])


@router.get("/meta")
async def verify_meta_webhook(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == config.META_VERIFY_TOKEN:
        log_meta_event("verified", {"status": "success"})
        return int(challenge)

    raise HTTPException(status_code=403, detail="Meta webhook verification failed")


@router.post("/meta")
async def receive_meta_webhook(request: Request):
    body = await request.body()

    if not body:
        log_meta_event("empty_post", {"reason": "empty_body"})
        return {
            "status": "ignored",
            "reason": "empty_body"
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        log_meta_event("invalid_json", {
            "body": body.decode("utf-8", errors="ignore")
        })
        return {
            "status": "ignored",
            "reason": "invalid_json"
        }

    log_meta_event("webhook_received", payload)

    result = process_meta_payload(payload)

    return {
        "status": "ok",
        "result": result
    }