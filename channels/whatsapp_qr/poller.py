"""Polling connected WhatsApp (QR scan) accounts for new messages.

Same shape as `channels/instagram_direct/poller.py` and `channels/
facebook_direct/poller.py`: a periodic sweep, one account isolated from the
next so one company's dead or slow session cannot stall the others, session
resumed from what was captured at connect time and never re-created from a
fresh login -- see `channels/whatsapp_qr/browser.py`'s own docstring for why
that distinction matters here specifically.

### The cursor problem, and why this one is different

Instagram (direct login)'s poller advances past what it has already
delivered using each message's own timestamp, because `instagrapi` hands
one back. WhatsApp Web's DOM gives this platform no equivalent stable id it
can verify without a live session to test against (see `browser.py`'s own
caveat on that). What this uses instead is the plainest thing that is still
correct without one: how many messages a chat held the last time it was
read. A chat's message list only grows in this reading (nothing here ever
deletes a message locally), so "read from index N onward" is exactly "read
what is new" -- cruder than a timestamp cursor, but it does not depend on
guessing a DOM attribute that may not exist on the day this runs live.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial
from channels.inbound import process_inbound_event
from channels.whatsapp_qr.browser import (
    WhatsAppSessionError,
    open_authenticated_page,
    read_unread_chats,
)
from database.manager import database_manager


logger = logging.getLogger(__name__)

# A hard cap on how many chats are checked per account per sweep -- the same
# reasoning as every other poller's own cap here: one company's very busy
# account must not starve every other company's turn in the same sweep.
MAX_CHATS_PER_POLL = 15


def poll_all_accounts() -> None:
    """One pass over every company's connected, active WhatsApp (QR) account."""
    with database_manager.control() as conn:
        rows = conn.execute(
            "SELECT id FROM channel_accounts WHERE channel = 'whatsapp_qr' AND status = 'active'"
        ).fetchall()

    for row in rows:
        account_id = int(row["id"])

        try:
            poll_account(account_id)
        except Exception:
            logger.exception("Polling WhatsApp (QR) account %s failed", account_id)


def _load_storage_state(row: Any, *, company_id: int, account_id: int) -> dict[str, Any] | None:
    if not row["access_token_sealed"]:
        return None

    try:
        state_json = keyring.unseal_secret(
            row["access_token_sealed"],
            database_manager.company_key(company_id),
            company_id,
            "access_token",
        )
    except CorruptedKeyMaterial:
        logger.error(
            "WhatsApp session for company %s account %s could not be "
            "unsealed; skipping this poll rather than guessing it",
            company_id,
            account_id,
        )
        return None

    try:
        state = json.loads(state_json)
    except (TypeError, ValueError):
        logger.error(
            "WhatsApp session for company %s account %s is not usable JSON",
            company_id,
            account_id,
        )
        return None

    if not isinstance(state, dict):
        return None

    return state


def poll_account(account_id: int) -> None:
    """Check one WhatsApp (QR) account for messages this platform has not
    processed yet."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id, access_token_sealed, config_json
            FROM channel_accounts
            WHERE id = ? AND channel = 'whatsapp_qr' AND status = 'active'
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        return

    company_id = int(row["company_id"])
    storage_state = _load_storage_state(row, company_id=company_id, account_id=account_id)

    if not storage_state:
        return

    try:
        config: dict[str, Any] = json.loads(row["config_json"] or "{}")
    except (TypeError, ValueError):
        config = {}

    seen_counts: dict[str, int] = dict(config.get("seen_message_counts") or {})

    try:
        session = open_authenticated_page(storage_state)
    except WhatsAppSessionError:
        logger.warning(
            "WhatsApp session for company %s account %s has expired; it "
            "needs to be reconnected",
            company_id,
            account_id,
        )
        return

    try:
        chats = read_unread_chats(session, max_chats=MAX_CHATS_PER_POLL)
    except Exception:
        logger.exception(
            "Could not read WhatsApp chats for company %s account %s",
            company_id,
            account_id,
        )
        session.close()
        return

    changed = False

    for chat in chats:
        chat_id = chat.get("chat_id")

        if not chat_id:
            continue

        messages = chat.get("messages") or []
        already_seen = seen_counts.get(chat_id, 0)

        for message in messages[already_seen:]:
            text = (message.get("text") or "").strip()

            if text and not message.get("is_outgoing"):
                process_inbound_event(
                    event={
                        "channel": "whatsapp_qr",
                        "user_id": chat_id,
                        "text": text,
                        "customer_name": chat.get("chat_name"),
                    },
                    company_id=company_id,
                    channel_account_id=account_id,
                )

        if len(messages) != already_seen:
            seen_counts[chat_id] = len(messages)
            changed = True

    session.close()

    if changed:
        with database_manager.control() as conn:
            fresh = conn.execute(
                "SELECT config_json FROM channel_accounts WHERE id = ?", (account_id,)
            ).fetchone()
            merged = {}

            if fresh and fresh["config_json"]:
                try:
                    merged = json.loads(fresh["config_json"])
                except (TypeError, ValueError):
                    merged = {}

            merged["seen_message_counts"] = seen_counts
            conn.execute(
                "UPDATE channel_accounts SET config_json = ? WHERE id = ?",
                (json.dumps(merged), account_id),
            )
            conn.commit()
