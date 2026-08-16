"""Meta webhook endpoint for Messenger and Instagram.

Three properties this endpoint has to hold:

* Only Meta can post here. Every request body is checked against the app
  secret signature before a single byte is parsed.
* A message is delivered to the company that owns the receiving account, looked
  up from the page id. There is no default company, because guessing routes one
  company's customers into another company's inbox.
* Slow work never blocks the event loop. Processing runs in a worker thread so a
  model call cannot stall the rest of the API.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from channels.meta.logger import log_meta_event
from channels.meta.parser import (
    detect_meta_channel,
    parse_meta_comment_events,
    parse_meta_events,
)
from channels.inbound import process_inbound_event
from channels.webhook_security import (
    WebhookVerificationError,
    SIGNATURE_HEADER,
    verify_signature,
    verify_token_challenge,
)
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["Meta Webhook"])


@router.get("/meta")
async def verify_meta_webhook(request: Request):
    """One-time subscription handshake."""
    params = request.query_params

    if verify_token_challenge(
        mode=params.get("hub.mode"),
        token=params.get("hub.verify_token"),
        expected_token=config.META_VERIFY_TOKEN,
    ):
        log_meta_event("webhook_verified", {"channel": "meta"})
        return Response(
            content=str(params.get("hub.challenge") or ""),
            media_type="text/plain",
        )

    log_meta_event("webhook_verification_failed", {"channel": "meta"})
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Webhook verification failed.",
    )


@router.post("/meta")
async def receive_meta_webhook(request: Request):
    raw_body = await request.body()

    try:
        verify_signature(
            raw_body=raw_body,
            signature_header=request.headers.get(SIGNATURE_HEADER),
            app_secret=config.META_APP_SECRET,
            allow_unsigned=config.ALLOW_UNSIGNED_WEBHOOKS,
        )
    except WebhookVerificationError as exc:
        log_meta_event("webhook_rejected", {"reason": str(exc)})
        logger.warning("Rejected unsigned or mis-signed Meta webhook: %s", exc)
        # 403 rather than 200: an unverified body is not something we accept.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        ) from exc

    if not raw_body:
        return {"status": "ignored", "reason": "empty_body"}

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        log_meta_event("webhook_invalid_json", {"size": len(raw_body)})
        return {"status": "ignored", "reason": "invalid_json"}

    events = parse_meta_events(payload)
    comment_events = parse_meta_comment_events(payload)

    if not events and not comment_events:
        log_meta_event("webhook_no_events", {"channel": detect_meta_channel(payload)})
        return {"status": "ignored", "reason": "no_events"}

    results = await run_in_threadpool(_process_events, events)
    comment_results = await run_in_threadpool(_process_comments, comment_events)

    return {
        "status": "ok",
        "processed": len(results) + len(comment_results),
        "results": results,
        "comments": comment_results,
    }


def _process_comments(events: list[dict]) -> list[dict]:
    """Store post comments, routed to the company that owns the page."""
    if not events:
        return []

    from backend.services.comment_service import comment_service

    results: list[dict] = []

    for event in events:
        company_id = database_manager.resolve_company_for_channel(
            channel=event.get("channel", "messenger"),
            page_id=event.get("page_id"),
            instagram_business_id=(
                event.get("page_id") if event.get("channel") == "instagram" else None
            ),
        )

        if company_id is None:
            log_meta_event(
                "comment_unrouted",
                {"channel": event.get("channel"), "page_id": event.get("page_id")},
            )
            results.append({"status": "ignored", "reason": "unknown_account"})
            continue

        try:
            stored = comment_service.record_incoming(
                company_id=company_id,
                channel=event["channel"],
                provider_comment_id=event["comment_id"],
                message=event["message"],
                post_id=event.get("post_id"),
                parent_comment_id=event.get("parent_comment_id"),
                author_external_id=event.get("author_external_id"),
                author_name=event.get("author_name"),
                permalink=event.get("permalink"),
                post_caption=event.get("post_caption"),
            )

            results.append(
                {
                    "status": "duplicate" if stored["duplicate"] else "stored",
                    "comment_id": stored["id"],
                    "company_id": company_id,
                }
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to store a post comment")
            log_meta_event(
                "comment_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results


def _process_events(events: list[dict]) -> list[dict]:
    """Handle every event in the delivery, isolating failures.

    One bad event must not discard the rest of the batch: Meta considers the
    whole delivery handled once we answer, so anything skipped here is lost.
    """
    results: list[dict] = []

    for event in events:
        if event.get("ignored"):
            log_meta_event("event_ignored", {"reason": event.get("reason")})
            results.append(
                {"status": "ignored", "reason": event.get("reason")}
            )
            continue

        company_id = database_manager.resolve_company_for_channel(
            channel=event.get("channel", "messenger"),
            page_id=event.get("page_id") or event.get("recipient_id"),
            instagram_business_id=(
                event.get("page_id")
                if event.get("channel") == "instagram"
                else None
            ),
        )

        if company_id is None:
            # The account this arrived on is not connected to any company.
            # Silently defaulting would attribute it to the wrong tenant.
            log_meta_event(
                "event_unrouted",
                {
                    "channel": event.get("channel"),
                    "page_id": event.get("page_id"),
                },
            )
            logger.warning(
                "Meta event for unknown account page_id=%s channel=%s",
                event.get("page_id"),
                event.get("channel"),
            )
            results.append({"status": "ignored", "reason": "unknown_account"})
            continue

        try:
            results.append(process_inbound_event(event=event, company_id=company_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to process Meta event")
            log_meta_event(
                "event_failed",
                {
                    "channel": event.get("channel"),
                    "company_id": company_id,
                    "error": type(exc).__name__,
                },
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
