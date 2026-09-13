"""Viber webhook, one public account (bot) per company.

Built the same shape as `channels/slack/webhook.py`: identity in the URL, a
signed delivery, and the shared `process_inbound_event` pipeline downstream.

### How a delivery is routed

Each company creates its own Viber public account (Viber calls a bot a
"public account"), so that account's own id can sit in the URL exactly like
a Slack workspace's `team_id` does:

    POST /webhook/viber/{account_id}

Here `account_id` is *this platform's* internal channel-account id, not
Viber's own `pa:<digits>` identifier -- deliberately, because
`register_viber_webhook` (see `channel_account_service.py`) has to build this
URL before it can call Viber at all, and the one id it is guaranteed to have
at that point is the row it just inserted. Viber's own `pa:` id is still what
the account is routed on internally (`ROUTING_FIELD["viber"]`), the same way
a Slack `team_id` or a Telegram bot id is -- this URL segment only has to be
unique and hard to guess, which an auto-incrementing row id is not, so the
signature below is what actually stands between an outsider and this
company's inbox, not the URL.

### How a delivery is authenticated

Viber has no separate app secret the way Slack has a Signing Secret distinct
from its bot token. Every callback carries `X-Viber-Content-Signature`: an
HMAC-SHA256 of the raw request body, keyed with the account's own
Authentication Token -- the same token `channels/viber/sender.py` uses to
send. That token is the account's `access_token`, sealed like every other
channel's, so this asks `channel_account_service.credentials_for` for it
rather than `verify_token_for` (which Viber has no separate value for).
"""

from __future__ import annotations

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

router = APIRouter(prefix="/webhook/viber", tags=["Viber"])

SIGNATURE_HEADER = "X-Viber-Content-Signature"


def parse_viber_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The one customer message in this delivery, if this event is one.

    Viber posts one event per callback, of several types -- `webhook`
    (the handshake sent right after `set_webhook`), `subscribed`,
    `unsubscribed`, `conversation_started`, `delivered`, `seen`, `failed`,
    and `message`. Only `message` is a customer saying something; the rest
    are acknowledged (so Viber does not retry them as failures) but carry
    nothing to answer.

    Non-text message types (picture, video, sticker, contact, url, location)
    are also not carried further: there is no attachment handling for any
    channel on this platform yet (see `channels/sender.py`'s
    `MEDIA_SUPPORTED_CHANNELS`), so pretending to read one would silently
    drop whatever the customer actually sent instead of just not replying.
    """
    if payload.get("event") != "message":
        return None

    sender = payload.get("sender")
    message = payload.get("message")

    if not isinstance(sender, dict) or not isinstance(message, dict):
        return None

    if message.get("type") != "text":
        return None

    user_id = str(sender.get("id") or "").strip()
    text = str(message.get("text") or "").strip()

    if not user_id or not text:
        return None

    return {
        "channel": "viber",
        "user_id": user_id,
        "text": text,
        "message_id": str(payload.get("message_token") or "") or None,
        "customer_name": str(sender.get("name") or "").strip() or None,
    }


def _authenticate(account_id: int, raw_body: bytes, signature: str | None) -> dict[str, Any]:
    """Find the account this delivery is for, and prove it is really Viber."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id FROM channel_accounts
            WHERE id = ? AND channel = 'viber' AND status = 'active'
            LIMIT 1
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        log_meta_event("viber_event_unrouted", {"account_id": account_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unknown account."
        )

    company_id = int(row["company_id"])

    credentials = channel_account_service.credentials_for(
        company_id=company_id, channel="viber", account_id=int(row["id"])
    )
    token = (credentials or {}).get("access_token")

    if not token:
        log_meta_event(
            "viber_webhook_no_token", {"company_id": company_id, "account_id": account_id}
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no bot token configured.",
        )

    expected = hmac.new(
        token.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    if not signature or not hmac.compare_digest(expected, signature):
        log_meta_event(
            "viber_webhook_rejected", {"company_id": company_id, "account_id": account_id}
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
    raw_body = await read_capped_body(request, source="viber")

    account = _authenticate(
        account_id, raw_body, request.headers.get(SIGNATURE_HEADER)
    )

    if not raw_body:
        return {"status": "ignored", "reason": "empty_body"}

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        log_meta_event("viber_invalid_json", {"size": len(raw_body)})
        return {"status": "ignored", "reason": "invalid_json"}

    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "invalid_payload"}

    limit = event_limit()

    if limit < 1:
        log_dropped_events(source="viber", kept=0, dropped=1)
        return {"status": "ignored", "reason": "rate_limited"}

    event = parse_viber_event(payload)

    if not event:
        return {"status": "ignored", "reason": "no_messages"}

    dispatch(
        _process_events,
        [
            {
                **event,
                "_company_id": account["company_id"],
                "_account_id": account["account_id"],
            }
        ],
        source="viber",
    )

    return {"status": "accepted", "accepted": 1}


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
            logger.exception("Failed to process Viber event")
            log_meta_event(
                "viber_event_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
