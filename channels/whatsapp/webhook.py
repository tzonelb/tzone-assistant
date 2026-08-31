"""WhatsApp Cloud API webhook.

Brought in line with the Messenger path, which it previously diverged from in
ways that mattered:

* the body is signature-checked before it is trusted,
* the message is routed to the company that owns the receiving number,
* every message in a batched delivery is handled, not just the first,
* processing goes through the same pipeline, so a human takeover suppresses the
  assistant on WhatsApp exactly as it does elsewhere,
* work runs off the event loop, so a slow model call cannot stall the API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from channels.inbound import process_inbound_event
from channels.meta.logger import log_meta_event
from backend.services.auth_service import client_ip
from channels.webhook_limits import (
    dispatch,
    event_limit,
    log_dropped_events,
    read_capped_body,
)
from channels.webhook_security import (
    SIGNATURE_HEADER,
    SOURCE_CURRENT,
    WebhookVerificationError,
    record_signature_rejection,
    verify_token_challenge,
    verify_webhook_signature,
)
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp"])


@router.get("/")
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if verify_token_challenge(
        mode=hub_mode,
        token=hub_verify_token,
        expected_token=config.WHATSAPP_VERIFY_TOKEN,
    ):
        return PlainTextResponse(content=hub_challenge or "")

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification failed.",
    )


def parse_whatsapp_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every text message in the delivery, up to the event cap.

    WhatsApp batches messages under ``entry[].changes[].value.messages[]``, so
    reading only the first entry silently discards the rest.

    At most ``WEBHOOK_MAX_EVENTS`` messages are parsed from one delivery, and
    anything past that is counted and logged rather than dropped quietly: one
    signed body must not be able to turn into unbounded work.
    """
    events: list[dict[str, Any]] = []
    cap = event_limit()
    dropped = 0

    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue

        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue

            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")

            for message in value.get("messages") or []:
                if not isinstance(message, dict):
                    continue

                if len(events) >= cap:
                    dropped += 1
                    continue

                if message.get("type") != "text":
                    events.append(
                        {
                            "ignored": True,
                            "reason": "non_text_message",
                            "channel": "whatsapp",
                            "user_id": message.get("from"),
                        }
                    )
                    continue

                sender = message.get("from")
                text = str((message.get("text") or {}).get("body") or "").strip()

                if not sender or not text:
                    continue

                events.append(
                    {
                        "ignored": False,
                        "channel": "whatsapp",
                        "user_id": str(sender),
                        "recipient_id": str(phone_number_id) if phone_number_id else None,
                        "phone_number_id": (
                            str(phone_number_id) if phone_number_id else None
                        ),
                        "text": text,
                        "message_id": message.get("id"),
                        "timestamp": message.get("timestamp"),
                        "raw_event": message,
                    }
                )

    log_dropped_events(source="whatsapp", kept=len(events), dropped=dropped)

    return events


@router.post("/")
async def receive_message(request: Request):
    raw_body = await read_capped_body(request, source="whatsapp")

    try:
        match = verify_webhook_signature(
            raw_body=raw_body,
            signature_header=request.headers.get(SIGNATURE_HEADER),
            app_secret=config.WHATSAPP_APP_SECRET,
            previous_app_secret=config.WHATSAPP_APP_SECRET_PREVIOUS,
            allow_unsigned=config.ALLOW_UNSIGNED_WEBHOOKS,
        )
    except WebhookVerificationError as exc:
        log_meta_event("whatsapp_webhook_rejected", {"reason": str(exc)})
        logger.warning("Rejected unsigned or mis-signed WhatsApp webhook: %s", exc)
        record_signature_rejection(
            source="whatsapp", ip_address=client_ip(request), reason=str(exc)
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        ) from exc

    if match.source != SOURCE_CURRENT:
        # So an operator can watch a rotation drain. Which secret matched, not
        # the secret.
        log_meta_event(
            "whatsapp_webhook_signature_source",
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
        log_meta_event("whatsapp_invalid_json", {"size": len(raw_body)})
        return {"status": "ignored", "reason": "invalid_json"}

    events = parse_whatsapp_events(payload)

    if not events:
        return {"status": "ignored", "reason": "no_messages"}

    # Acknowledged now, processed after the response has gone. Holding the
    # response open for every event kept a shared worker thread — the same pool
    # that serves the dashboard — busy for the whole batch.
    dispatch(_process_events, events, source="whatsapp")

    return {"status": "accepted", "accepted": len(events)}


def _process_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for event in events:
        if event.get("ignored"):
            results.append({"status": "ignored", "reason": event.get("reason")})
            continue

        # The account, not only its company: a company may run several numbers
        # and point each at a different department.
        account = database_manager.resolve_account_for_channel(
            channel="whatsapp",
            phone_number_id=event.get("phone_number_id"),
        )

        company_id = account["company_id"] if account else None

        if company_id is None:
            log_meta_event(
                "whatsapp_event_unrouted",
                {"phone_number_id": event.get("phone_number_id")},
            )
            logger.warning(
                "WhatsApp message for unknown number id=%s",
                event.get("phone_number_id"),
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
            logger.exception("Failed to process WhatsApp event")
            log_meta_event(
                "whatsapp_event_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
