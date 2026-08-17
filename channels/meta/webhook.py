"""Meta webhook endpoint for Messenger and Instagram.

Three properties this endpoint has to hold:

* Only Meta can post here. Every request body is checked against the app
  secret signature before a single byte is parsed.
* A message is delivered to the company that owns the receiving account, looked
  up from the page id. There is no default company, because guessing routes one
  company's customers into another company's inbox.
* Slow work never blocks the event loop, and never holds the response open. A
  verified delivery is acknowledged immediately and processed on a background
  task, so a model call — or a flood of them — cannot stall the rest of the API.
  The size of a body and the number of events in it are capped before any of
  that work is accepted; see ``channels/webhook_limits.py``.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from channels.meta.logger import log_meta_event
from channels.meta.parser import (
    detect_meta_channel,
    parse_meta_comment_events,
    parse_meta_events,
)
from channels.inbound import process_inbound_event
from backend.services.comment_service import comment_service
from backend.services.module_gate import module_gate
from backend.services.auth_service import client_ip
from channels.webhook_limits import dispatch, read_capped_body
from channels.webhook_security import (
    WebhookVerificationError,
    SIGNATURE_HEADER,
    SOURCE_CURRENT,
    record_signature_rejection,
    verify_token_challenge,
    verify_webhook_signature,
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
    raw_body = await read_capped_body(request, source="meta")

    try:
        match = verify_webhook_signature(
            raw_body=raw_body,
            signature_header=request.headers.get(SIGNATURE_HEADER),
            app_secret=config.META_APP_SECRET,
            previous_app_secret=config.META_APP_SECRET_PREVIOUS,
            allow_unsigned=config.ALLOW_UNSIGNED_WEBHOOKS,
        )
    except WebhookVerificationError as exc:
        log_meta_event("webhook_rejected", {"reason": str(exc)})
        logger.warning("Rejected unsigned or mis-signed Meta webhook: %s", exc)
        record_signature_rejection(
            source="meta", ip_address=client_ip(request), reason=str(exc)
        )
        # 403 rather than 200: an unverified body is not something we accept.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        ) from exc

    if match.source != SOURCE_CURRENT:
        # Left in the channel log so an operator can watch a rotation drain and
        # see when the previous secret stops being used. The secret itself is
        # never recorded, only which one matched.
        log_meta_event(
            "webhook_signature_source",
            {
                "source": match.source,
                "company_id": match.company_id,
                "account_id": match.account_id,
            },
        )

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

    # Acknowledged now, processed after the response has gone. Waiting for every
    # event held one of Starlette's shared worker threads for the whole batch —
    # the same pool that serves every `def` route, which is why a flood here
    # froze the dashboard as well.
    dispatch(_process_events, events, source="meta")
    dispatch(_process_comments, comment_events, source="meta_comments")

    return {
        "status": "accepted",
        "accepted": len(events) + len(comment_events),
    }


def _process_comments(events: list[dict]) -> list[dict]:
    """Store post comments, routed to the company that owns the page."""
    if not events:
        return []

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

        # Comments off means the module is not there to receive one. Storing it
        # anyway would fill a table the team cannot open, and every unanswered
        # comment would sit in it invisibly — the company would believe it had
        # switched comment handling off while the rows piled up.
        if not module_gate.enabled(company_id, "comments"):
            results.append({"status": "ignored", "reason": "module_disabled"})
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

        # Resolved as an account, not just a company. Two Instagram accounts
        # belonging to the same company may feed different departments, so the
        # company alone does not say where this message belongs.
        account = database_manager.resolve_account_for_channel(
            channel=event.get("channel", "messenger"),
            page_id=event.get("page_id") or event.get("recipient_id"),
            instagram_business_id=(
                event.get("page_id")
                if event.get("channel") == "instagram"
                else None
            ),
        )

        company_id = account["company_id"] if account else None

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
            results.append(
                process_inbound_event(
                    event=event,
                    company_id=company_id,
                    channel_account_id=account["account_id"],
                )
            )
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
