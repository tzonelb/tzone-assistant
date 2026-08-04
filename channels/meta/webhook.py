from fastapi import APIRouter, Request, HTTPException
import json
import logging

from channels.common.rate_limiter import get_client_ip, meta_webhook_rate_limiter
from channels.meta.processor import process_meta_payload
from channels.meta.logger import log_meta_event
from channels.meta.verifier import verify_meta_signature
from config.settings import config


logger = logging.getLogger(__name__)

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
    if not meta_webhook_rate_limiter.allow(get_client_ip(request)):
        log_meta_event("rate_limited", {"ip": get_client_ip(request)})
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.body()
    signature_header = request.headers.get("x-hub-signature-256")

    if config.FACEBOOK_APP_SECRET:
        if not verify_meta_signature(body, signature_header, config.FACEBOOK_APP_SECRET):
            log_meta_event("signature_invalid", {"reason": "hmac_mismatch_or_missing_header"})
            raise HTTPException(status_code=403, detail="Invalid signature")
    elif config.DEBUG:
        # No app secret configured (real possibility in local dev). Allow
        # through but log loudly so this never silently ships to prod.
        logger.warning(
            "FACEBOOK_APP_SECRET is not configured -- accepting Meta webhook "
            "POST WITHOUT signature verification because DEBUG=true. This "
            "must never happen in production."
        )
    else:
        log_meta_event("signature_rejected", {"reason": "app_secret_not_configured"})
        raise HTTPException(status_code=403, detail="Webhook not configured")

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