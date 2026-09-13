"""LINE webhook, one Messaging API channel per company.

Built the same shape as `channels/viber/webhook.py`: identity in the URL is
this platform's own row id rather than the provider's, because the
webhook-registration call has to build that URL before the provider's own id
is otherwise relevant. Authentication is closer to `channels/slack/
webhook.py`'s shape instead: a signed request, with the signing secret --
here called the Channel Secret -- stored separately from the token that
sends, in the existing `verify_token_sealed` column.

### How a delivery is routed

    POST /webhook/line/{account_id}

`account_id` is this platform's own channel-account id. LINE's own identity
for the channel -- its bot's `userId`, asked of `bot/info` at connect time,
see `channel_account_service.line_bot_user_id` -- is still what
`ROUTING_FIELD["line"]` is enforced unique on; it just does not need to be
in this URL to do that job.

### How a delivery is authenticated

Every LINE webhook carries `x-line-signature`: base64(HMAC-SHA256(channel
secret, raw request body)). LINE's own Channel Secret is a value distinct
from the Channel Access Token used to send -- the same two-credential shape
Slack has, which is why it fits the existing `access_token`/`verify_token`
pair without a new column.

### How one delivery can carry several messages

LINE batches: one POST's `events` array can hold more than one event, unlike
Slack's Events API which delivers one event per request under normal
operation. Each is parsed and dispatched independently, with the same
per-request event cap every other webhook here enforces.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Request, status

from backend.services.channel_account_service import channel_account_service
from channels.inbound import process_inbound_event
from channels.meta.logger import log_meta_event
from channels.webhook_limits import (
    dispatch,
    event_limit,
    log_dropped_events,
    read_capped_body,
)
from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/line", tags=["LINE"])

SIGNATURE_HEADER = "x-line-signature"


def parse_line_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every customer text message in this delivery.

    Only `type == "message"` events with a `source.type == "user"` and a
    text message are carried further -- group and room messages are excluded
    for the same reason Discord's Gateway parser excludes guild channels: a
    message posted where other people can see it is not a private
    conversation with a customer. Non-text message types (image, video,
    sticker, location, ...) are not handled yet, matching every other
    channel here that has no attachment support (see `channels/sender.py`'s
    `MEDIA_SUPPORTED_CHANNELS`).
    """
    events = payload.get("events")

    if not isinstance(events, list):
        return []

    limit = event_limit()
    parsed: list[dict[str, Any]] = []
    dropped = 0

    for raw_event in events:
        if not isinstance(raw_event, dict):
            continue

        if raw_event.get("type") != "message":
            continue

        source = raw_event.get("source")
        message = raw_event.get("message")

        if not isinstance(source, dict) or not isinstance(message, dict):
            continue

        if source.get("type") != "user":
            continue

        if message.get("type") != "text":
            continue

        user_id = str(source.get("userId") or "").strip()
        text = str(message.get("text") or "").strip()

        if not user_id or not text:
            continue

        if len(parsed) >= limit:
            dropped += 1
            continue

        parsed.append(
            {
                "channel": "line",
                "user_id": user_id,
                "text": text,
                "message_id": str(message.get("id") or "") or None,
            }
        )

    if dropped:
        log_dropped_events(source="line", kept=len(parsed), dropped=dropped)

    return parsed


def _authenticate(
    account_id: int, raw_body: bytes, signature: str | None
) -> dict[str, Any]:
    """Find the account this delivery is for, and prove it is really LINE."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id FROM channel_accounts
            WHERE id = ? AND channel = 'line' AND status = 'active'
            LIMIT 1
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        log_meta_event("line_event_unrouted", {"account_id": account_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unknown account."
        )

    company_id = int(row["company_id"])

    try:
        channel_secret = channel_account_service.verify_token_for(
            company_id=company_id, account_id=int(row["id"])
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not read the LINE channel secret for company %s", company_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification failed.",
        ) from None

    if not channel_secret:
        log_meta_event(
            "line_webhook_no_secret",
            {"company_id": company_id, "account_id": account_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no channel secret configured.",
        )

    expected = base64.b64encode(
        hmac.new(channel_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    ).decode("ascii")

    if not signature or not hmac.compare_digest(expected, signature):
        log_meta_event(
            "line_webhook_rejected", {"company_id": company_id, "account_id": account_id}
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature."
        )

    return {"company_id": company_id, "account_id": int(row["id"])}


@router.post("/{account_id}")
async def receive_event(
    request: Request,
    account_id: int = Path(ge=1),
):
    raw_body = await read_capped_body(request, source="line")

    account = _authenticate(
        account_id, raw_body, request.headers.get(SIGNATURE_HEADER)
    )

    if not raw_body:
        return {"status": "ignored", "reason": "empty_body"}

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        log_meta_event("line_invalid_json", {"size": len(raw_body)})
        return {"status": "ignored", "reason": "invalid_json"}

    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "invalid_payload"}

    events = parse_line_events(payload)

    if not events:
        return {"status": "ignored", "reason": "no_messages"}

    dispatch(
        _process_events,
        [
            {
                **event,
                "_company_id": account["company_id"],
                "_account_id": account["account_id"],
            }
            for event in events
        ],
        source="line",
    )

    return {"status": "accepted", "accepted": len(events)}


def _process_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for event in events:
        company_id = event.pop("_company_id", None)
        account_id = event.pop("_account_id", None)

        if company_id is None:
            results.append({"status": "ignored", "reason": "unknown_account"})
            continue

        try:
            results.append(
                process_inbound_event(
                    event=event,
                    company_id=company_id,
                    channel_account_id=account_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to process LINE event")
            log_meta_event(
                "line_event_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
