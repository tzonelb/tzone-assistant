"""Telegram webhook, one bot per company.

Telegram used to reach this platform through `channels/telegram/bot.py` — a
standalone long-polling script, holding one bot token from `TELEGRAM_BOT_TOKEN`
in the environment, calling the engine directly. That worked, and it worked for
exactly one company: one process, one token, one business.

A platform serving a thousand companies cannot run a thousand polling processes,
and it must not answer them all from one bot. Telegram's own answer is the
webhook, which is also what Messenger and WhatsApp already use here — so this
file is deliberately the same shape as `channels/whatsapp/webhook.py`.

### How a delivery is routed

The bot id is in the path. Telegram posts every update for a bot to whatever URL
that bot was registered with, so the URL is where the identity belongs:

    POST /webhook/telegram/{bot_id}

`bot_id` is the numeric prefix of the bot's own token, which is why
`channel_account_service` derives it rather than asking an operator to type it.
It is matched against `channel_accounts.external_account_id` for the `telegram`
channel — the same lookup, through the same function, that routes a Messenger
page or a WhatsApp number.

### How a delivery is authenticated

Telegram has no request signature. What it has is a secret token registered with
`setWebhook`, echoed on every delivery in `X-Telegram-Bot-Api-Secret-Token`.
That is stored per account in the existing `verify_token_sealed` column, and
compared with `compare_digest`.

An account with no secret configured is **refused**, not waved through. The URL
contains only a bot id, which is public — it appears in the bot's own username
lookup — so without the secret anyone who knows a company's bot could post
messages into that company's inbox as any customer they liked.
"""

from __future__ import annotations

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

router = APIRouter(prefix="/webhook/telegram", tags=["Telegram"])

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def parse_telegram_events(payload: Any, bot_id: str) -> list[dict[str, Any]]:
    """Every text message in this delivery, up to the event cap.

    Telegram posts one update per request under normal operation, but
    `getUpdates`-style batches arrive as a list after a webhook is re-armed
    following an outage — so both shapes are read rather than assuming the
    common one.
    """
    updates = payload if isinstance(payload, list) else [payload]
    events: list[dict[str, Any]] = []
    dropped = 0
    limit = event_limit()

    for update in updates:
        if not isinstance(update, dict):
            continue

        # `edited_message` deliberately ignored: answering an edit as though it
        # were a new question would reply twice to one thing the customer said.
        message = update.get("message")

        if not isinstance(message, dict):
            continue

        chat = message.get("chat") or {}
        sender = chat.get("id")
        text = str(message.get("text") or "").strip()

        if not sender or not text:
            continue

        if len(events) >= limit:
            dropped += 1
            continue

        events.append(
            {
                "ignored": False,
                "channel": "telegram",
                "user_id": str(sender),
                "recipient_id": str(bot_id),
                "external_account_id": str(bot_id),
                "text": text,
                "message_id": str(message.get("message_id") or "") or None,
                "timestamp": message.get("date"),
                # The name Telegram gives us, so the inbox shows a person
                # rather than a chat id. `process_inbound_event` decides what to
                # do with it.
                "customer_name": " ".join(
                    part
                    for part in (
                        (message.get("from") or {}).get("first_name"),
                        (message.get("from") or {}).get("last_name"),
                    )
                    if part
                ).strip()
                or None,
                "raw_event": message,
            }
        )

    log_dropped_events(source="telegram", kept=len(events), dropped=dropped)

    return events


def _authenticate(bot_id: str, offered_secret: str | None) -> dict[str, Any]:
    """Find the account this delivery is for, and prove it is really Telegram.

    Returns the resolved account. Raises `HTTPException` on anything else —
    including an account that has no secret registered, which is refused rather
    than trusted: the bot id in the URL is public, so without the secret anyone
    could post into this company's inbox as any customer.
    """
    account = database_manager.resolve_account_for_channel(
        channel="telegram", page_id=bot_id
    )

    if not account:
        log_meta_event("telegram_event_unrouted", {"bot_id": bot_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unknown bot.",
        )

    company_id = int(account["company_id"])

    try:
        expected = channel_account_service.verify_token_for(
            company_id=company_id, account_id=int(account["account_id"])
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not read the Telegram webhook secret for company %s", company_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification failed.",
        ) from None

    if not expected:
        log_meta_event(
            "telegram_webhook_no_secret",
            {"company_id": company_id, "bot_id": bot_id},
        )
        logger.warning(
            "Refusing a Telegram delivery for company %s: no webhook secret is "
            "registered on that account",
            company_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This bot has no webhook secret configured.",
        )

    if not offered_secret or not hmac.compare_digest(
        str(expected), str(offered_secret)
    ):
        log_meta_event(
            "telegram_webhook_rejected",
            {"company_id": company_id, "bot_id": bot_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret.",
        )

    return account


@router.post("/{bot_id}")
async def receive_update(
    request: Request,
    bot_id: str = Path(min_length=1, max_length=32, pattern=r"^\d+$"),
):
    # Read under the same body cap as every other webhook, before anything is
    # parsed. A delivery this platform will not process must not be able to
    # spend its memory first.
    raw_body = await read_capped_body(request, source="telegram")

    account = _authenticate(bot_id, request.headers.get(SECRET_HEADER))

    if not raw_body:
        return {"status": "ignored", "reason": "empty_body"}

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        log_meta_event("telegram_invalid_json", {"size": len(raw_body)})
        return {"status": "ignored", "reason": "invalid_json"}

    events = parse_telegram_events(payload, bot_id)

    if not events:
        return {"status": "ignored", "reason": "no_messages"}

    # Acknowledged now, processed after the response has gone. Telegram retries
    # an update it does not see acknowledged quickly, so holding the response
    # open for a model call is how one slow reply becomes four copies of it.
    dispatch(
        _process_events,
        [
            {
                **event,
                "_company_id": int(account["company_id"]),
                "_account_id": int(account["account_id"]),
            }
            for event in events
        ],
        source="telegram",
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
            logger.exception("Failed to process Telegram event")
            log_meta_event(
                "telegram_event_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
