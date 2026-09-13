"""Slack webhook, one Slack app per company.

Built the same shape as `channels/telegram/webhook.py`, which is itself the
same shape as `channels/whatsapp/webhook.py`: identity in the URL, a signed
delivery, an immediate acknowledgement, and the shared `process_inbound_event`
pipeline downstream. Nothing here is Slack-specific except how a delivery is
addressed and authenticated.

### How a delivery is routed

Each company creates its own Slack app in its own workspace (Slack does not
require a shared, platform-wide app the way some providers do), so the
workspace's own id can sit in the URL exactly like a Telegram bot's id does:

    POST /webhook/slack/{team_id}

`team_id` is never typed by the operator. `channel_account_service.slack_team_id`
asks Slack's own `auth.test` for it with the bot token being pasted in, the
same reasoning `telegram_bot_id` uses for parsing a bot's token locally --
except Slack's token carries no id to parse, so this has to ask Slack instead
of a local computation.

### How a delivery is authenticated

Slack signs every request with the app's Signing Secret: `X-Slack-Signature`
is `v0=` followed by an HMAC-SHA256 of `v0:{timestamp}:{raw body}`, and
`X-Slack-Request-Timestamp` guards against a captured request being replayed
later. Both are required, both are checked, and a request more than five
minutes old is refused even with a correct signature -- Slack's own published
recommendation. The Signing Secret is stored per account in the same
`verify_token_sealed` column Telegram's webhook secret uses, so an account with
none configured is refused rather than trusted -- the workspace id in the URL
is not a secret, so without the signature anyone could post into this
company's inbox as any Slack user they chose.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Request, Response, status

from backend.services.channel_account_service import channel_account_service
from channels.inbound import process_inbound_event
from channels.meta.logger import log_meta_event
from channels.slack.profile import resolve_slack_display_name
from channels.webhook_limits import (
    dispatch,
    event_limit,
    log_dropped_events,
    read_capped_body,
)
from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/slack", tags=["Slack"])

SIGNATURE_HEADER = "X-Slack-Signature"
TIMESTAMP_HEADER = "X-Slack-Request-Timestamp"

# Slack's own recommended replay window.
MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5

# Message subtypes that are not a customer saying something new: an edit, a
# deletion, someone joining or leaving. Answering one of these as though it
# were a fresh question would reply to an event the customer never sent.
_IGNORED_SUBTYPES = frozenset(
    {
        "message_changed",
        "message_deleted",
        "message_replied",
        "channel_join",
        "channel_leave",
        "bot_message",
    }
)


def parse_slack_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The one message in this delivery, if it is really one worth answering.

    Slack's Events API delivers one event per request under normal operation
    (unlike Telegram, which can batch), but the same cap and drop-accounting
    every other webhook uses is applied here too rather than assumed away.
    """
    event = payload.get("event")

    if not isinstance(event, dict):
        return []

    if event.get("type") != "message":
        return []

    # A message sent by a bot -- including this platform's own reply --
    # carries `bot_id`. Without this check, every reply this platform sends
    # would loop back in as a new customer message.
    if event.get("bot_id"):
        return []

    if str(event.get("subtype") or "") in _IGNORED_SUBTYPES:
        return []

    channel_id = str(event.get("channel") or "").strip()
    user_id = str(event.get("user") or "").strip()
    text = str(event.get("text") or "").strip()

    if not channel_id or not user_id or not text:
        return []

    limit = event_limit()

    if limit < 1:
        log_dropped_events(source="slack", kept=0, dropped=1)
        return []

    return [
        {
            "ignored": False,
            "channel": "slack",
            # The DM/channel id, not the person's user id: it is what
            # `chat.postMessage` needs to reply into the same conversation, so
            # it plays the role `chat.id` plays for Telegram.
            "user_id": channel_id,
            "recipient_id": channel_id,
            "text": text,
            "message_id": str(event.get("ts") or "") or None,
            "timestamp": event.get("ts"),
            # Kept separately from `user_id` above: this is the person, not
            # the conversation, and is only used to look up a display name.
            "_slack_user_id": user_id,
            "raw_event": event,
        }
    ]


def _valid_timestamp(raw: str | None) -> bool:
    if not raw:
        return False

    try:
        sent_at = int(raw)
    except ValueError:
        return False

    return abs(time.time() - sent_at) <= MAX_TIMESTAMP_SKEW_SECONDS


def _authenticate(
    team_id: str, raw_body: bytes, timestamp: str | None, signature: str | None
) -> dict[str, Any]:
    """Find the account this delivery is for, and prove it is really Slack."""
    account = database_manager.resolve_account_for_channel(
        channel="slack", page_id=team_id
    )

    if not account:
        log_meta_event("slack_event_unrouted", {"team_id": team_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unknown workspace."
        )

    company_id = int(account["company_id"])

    try:
        signing_secret = channel_account_service.verify_token_for(
            company_id=company_id, account_id=int(account["account_id"])
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not read the Slack signing secret for company %s", company_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification failed.",
        ) from None

    if not signing_secret:
        log_meta_event(
            "slack_webhook_no_secret",
            {"company_id": company_id, "team_id": team_id},
        )
        logger.warning(
            "Refusing a Slack delivery for company %s: no signing secret is "
            "registered on that account",
            company_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This workspace has no signing secret configured.",
        )

    if not _valid_timestamp(timestamp):
        log_meta_event(
            "slack_webhook_stale_timestamp",
            {"company_id": company_id, "team_id": team_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or stale request timestamp.",
        )

    base = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not signature or not hmac.compare_digest(expected, signature):
        log_meta_event(
            "slack_webhook_rejected",
            {"company_id": company_id, "team_id": team_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature."
        )

    return account


@router.post("/{team_id}")
async def receive_event(
    request: Request,
    team_id: str = Path(min_length=1, max_length=32, pattern=r"^T[A-Z0-9]+$"),
):
    raw_body = await read_capped_body(request, source="slack")

    account = _authenticate(
        team_id,
        raw_body,
        request.headers.get(TIMESTAMP_HEADER),
        request.headers.get(SIGNATURE_HEADER),
    )

    if not raw_body:
        return {"status": "ignored", "reason": "empty_body"}

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        log_meta_event("slack_invalid_json", {"size": len(raw_body)})
        return {"status": "ignored", "reason": "invalid_json"}

    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "invalid_payload"}

    # Slack's own handshake when the Request URL is first configured, or
    # re-verified later. Answered only after the signature above has already
    # passed, so this cannot be used to probe an unconfigured or wrong URL.
    if payload.get("type") == "url_verification":
        return Response(
            content=json.dumps({"challenge": payload.get("challenge", "")}),
            media_type="application/json",
        )

    # Defence in depth: the URL already names the workspace, but a payload
    # whose own `team_id` disagrees with it is not something to trust just
    # because the signature happened to check out for this account.
    if str(payload.get("team_id") or "") != team_id:
        log_meta_event(
            "slack_team_id_mismatch",
            {"path_team_id": team_id, "body_team_id": payload.get("team_id")},
        )
        return {"status": "ignored", "reason": "team_mismatch"}

    events = parse_slack_events(payload)

    if not events:
        return {"status": "ignored", "reason": "no_messages"}

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
        source="slack",
    )

    return {"status": "accepted", "accepted": len(events)}


def _process_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for event in events:
        company_id = event.pop("_company_id", None)
        account_id = event.pop("_account_id", None)
        slack_user_id = event.pop("_slack_user_id", None)

        if company_id is None:
            results.append({"status": "ignored", "reason": "unknown_account"})
            continue

        if slack_user_id:
            event["customer_name"] = resolve_slack_display_name(
                user_id=slack_user_id, company_id=company_id
            )

        try:
            results.append(
                process_inbound_event(
                    event=event,
                    company_id=company_id,
                    channel_account_id=account_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to process Slack event")
            log_meta_event(
                "slack_event_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
