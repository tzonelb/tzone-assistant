"""The public endpoints a website chat widget talks to.

Deliberately not under `/api/channels`: everything there assumes an
authenticated employee acting on their own company. These two routes are the
opposite -- an anonymous visitor on a company's own website, identified by
nothing but the widget key embedded in that page's source and a visitor id
their own browser generated and kept. No session, no permission check, no
company id in the URL: the widget key is the only thing that says which
company this is, resolved the same way every other channel's webhook
resolves its account, through `database_manager.resolve_account_for_channel`.

The widget key is not a secret -- it is meant to sit in a company's page
source, wherever they publish their site -- so what stands in for the
per-message auth every other channel has is scope, not a signature: a
visitor can only ever read the messages under their own `visitor_id`, which
nobody else can guess (see `frontend/public/widget.js`, which mints it with
`crypto.randomUUID()` and never sends it anywhere but here).

General abuse protection is not reinvented here. `BodySizeLimitMiddleware`
and `GeneralRateLimitMiddleware` in `backend/api/middleware.py` already wrap
every route in the app, this one included.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.services.message_service import message_service
from channels.inbound import process_inbound_event
from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webchat", tags=["Website Chat"])

MAX_MESSAGES_PER_POLL = 100


class WidgetMessageIn(BaseModel):
    visitor_id: str = Field(min_length=8, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    # Never treated as an identity, only a courtesy label -- anyone can type
    # anything here, the same as a name a customer types on any channel.
    visitor_name: str | None = Field(default=None, max_length=120)


def _resolve_account(widget_key: str) -> dict[str, Any]:
    account = database_manager.resolve_account_for_channel(
        channel="webchat", page_id=widget_key
    )

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This chat widget is not connected to anything.",
        )

    return account


@router.post("/{widget_key}/messages", status_code=status.HTTP_201_CREATED)
def send_widget_message(widget_key: str, payload: WidgetMessageIn):
    account = _resolve_account(widget_key)

    event = {
        "ignored": False,
        "channel": "webchat",
        "user_id": payload.visitor_id,
        "recipient_id": payload.visitor_id,
        "text": payload.text.strip(),
        "message_id": None,
        "customer_name": (payload.visitor_name or "").strip() or None,
    }

    try:
        result = process_inbound_event(
            event=event,
            company_id=int(account["company_id"]),
            channel_account_id=int(account["account_id"]),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to process a webchat message")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Your message could not be delivered. Please try again.",
        ) from None

    return {"status": result.get("status", "received"), "message_id": result.get("message_id")}


@router.get("/{widget_key}/messages")
def list_widget_messages(
    widget_key: str,
    visitor_id: str = Query(min_length=8, max_length=128),
    limit: int = Query(default=50, ge=1, le=MAX_MESSAGES_PER_POLL),
):
    account = _resolve_account(widget_key)

    messages = message_service.list_messages(
        company_id=int(account["company_id"]),
        channel="webchat",
        external_user_id=visitor_id,
        limit=limit,
    )

    # Nothing here a stranger could not already infer from the conversation
    # itself, but `sender_user_id` is an internal database id with no
    # business on a public response -- it names nothing to the visitor and
    # would only be a number to scrape.
    return {
        "messages": [
            {
                "id": message["id"],
                "direction": message["direction"],
                "text": message["text"],
                "time": message["time"],
                "sender_type": message["sender_type"],
            }
            for message in messages
        ]
    }
