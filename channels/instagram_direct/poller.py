"""Polling connected Instagram accounts for new Direct Messages.

Instagram's private API gives this platform nothing resembling a webhook --
it is the same shape email is in, and this file follows
`channels/email/poller.py`'s own pattern almost exactly: a periodic sweep
over every active account, each one isolated from the others so one
company's dead session cannot stop the sweep from reaching the next.

### What this deliberately does not do

Instagram's own risk model is what an unofficial client like this one has
to live inside, not fight -- see `backend/api/routes/instagram_direct.py`'s
docstring and this platform's own unofficial-channel research for the
detail. Two things follow directly from that, both load-bearing:

* **Never re-authenticate here.** The session this poller uses was
  established once, at connect time, with a stored username and password
  this platform deliberately does not keep afterward (see
  `channel_account_service`'s comment on why only the resulting session is
  sealed). If that session has gone stale, the only correct move is to stop
  and let the operator reconnect -- constructing a *new* login from a
  process that has no password to offer is not an option, and retrying the
  same dead session is exactly the "keep hammering a flagged account"
  pattern the library's own maintainers point to as what turns a soft flag
  into a ban.
* **Randomized pacing, not a fixed tight loop.** `INSTAGRAM_POLL_SECONDS`
  is a base interval; `backend/workers.py`'s worker adds jitter around it
  rather than sleeping the exact same duration every cycle, because a
  perfectly periodic request pattern is itself one of the behavioral
  signals this platform's own research found associated with automation
  detection -- a real person checking their phone does not do it on a
  metronome.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from instagrapi import Client
from instagrapi.exceptions import ClientError, LoginRequired

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial
from channels.inbound import process_inbound_event
from database.manager import database_manager


logger = logging.getLogger(__name__)

# A hard cap on how many threads and how many messages within a thread are
# read per account per sweep -- the same reasoning as email's
# MAX_MESSAGES_PER_POLL: a flooded inbox must not let one company's account
# starve every other company's turn in the same sweep.
MAX_THREADS_PER_POLL = 20
MAX_PENDING_PER_POLL = 20
THREAD_MESSAGE_LIMIT = 20


def poll_all_accounts() -> None:
    """One pass over every company's connected, active Instagram account."""
    with database_manager.control() as conn:
        rows = conn.execute(
            "SELECT id FROM channel_accounts WHERE channel = 'instagram_direct' AND status = 'active'"
        ).fetchall()

    for row in rows:
        account_id = int(row["id"])

        try:
            poll_account(account_id)
        except Exception:
            logger.exception("Polling Instagram account %s failed", account_id)


def _load_client(row: Any, *, company_id: int, account_id: int):
    """Rehydrate a live `instagrapi.Client` from this account's sealed
    session -- never from a fresh login, see this module's own docstring."""
    if not row["access_token_sealed"]:
        return None

    try:
        settings_json = keyring.unseal_secret(
            row["access_token_sealed"],
            database_manager.company_key(company_id),
            company_id,
            "access_token",
        )
    except CorruptedKeyMaterial:
        logger.error(
            "Instagram session for company %s account %s could not be "
            "unsealed; skipping this poll rather than guessing it",
            company_id,
            account_id,
        )
        return None

    try:
        settings = json.loads(settings_json)
    except (TypeError, ValueError):
        logger.error(
            "Instagram session for company %s account %s is not usable JSON",
            company_id,
            account_id,
        )
        return None

    client = Client()
    client.set_settings(settings)

    proxy = None

    if row["verify_token_sealed"]:
        try:
            proxy = keyring.unseal_secret(
                row["verify_token_sealed"],
                database_manager.company_key(company_id),
                company_id,
                "verify_token",
            )
        except CorruptedKeyMaterial:
            logger.warning(
                "Instagram proxy for company %s account %s could not be "
                "unsealed; polling without it",
                company_id,
                account_id,
            )

    if proxy:
        client.set_proxy(proxy)

    return client


def poll_account(account_id: int) -> None:
    """Check one Instagram account's DM inbox for messages this platform
    has not processed yet."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id, access_token_sealed, verify_token_sealed, config_json
            FROM channel_accounts
            WHERE id = ? AND channel = 'instagram_direct' AND status = 'active'
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        return

    company_id = int(row["company_id"])
    client = _load_client(row, company_id=company_id, account_id=account_id)

    if not client:
        return

    try:
        config: dict[str, Any] = json.loads(row["config_json"] or "{}")
    except (TypeError, ValueError):
        config = {}

    cursor = float(config.get("last_message_ts") or 0)
    newest_seen = cursor

    try:
        general = client.direct_threads(
            amount=MAX_THREADS_PER_POLL, thread_message_limit=THREAD_MESSAGE_LIMIT
        )
        pending = client.direct_pending_inbox(amount=MAX_PENDING_PER_POLL)
    except LoginRequired:
        logger.warning(
            "Instagram session for company %s account %s has expired; "
            "it needs to be reconnected",
            company_id,
            account_id,
        )
        return
    except ClientError as exc:
        logger.warning(
            "Could not poll Instagram for company %s account %s: %s",
            company_id,
            account_id,
            exc,
        )
        return

    for thread in (*pending, *general):
        # A message request this platform has not accepted yet cannot be
        # replied to -- accepting it is the one action this poller takes
        # beyond reading, and only in direct response to a genuine inbound
        # message, the same as every other channel here auto-creating a
        # conversation record for a customer it has never seen before.
        if thread.pending:
            has_customer_message = any(
                not message.is_sent_by_viewer and (message.text or "").strip()
                for message in thread.messages
            )

            if has_customer_message:
                try:
                    client.direct_pending_approve(thread.id)
                except ClientError:
                    logger.exception(
                        "Could not accept an Instagram message request for "
                        "company %s account %s",
                        company_id,
                        account_id,
                    )
                    continue

        newest_seen = _process_thread(
            thread,
            cursor=cursor,
            company_id=company_id,
            account_id=account_id,
            newest_seen=newest_seen,
        )

    if newest_seen > cursor:
        with database_manager.control() as conn:
            fresh = conn.execute(
                "SELECT config_json FROM channel_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            merged = {}

            if fresh and fresh["config_json"]:
                try:
                    merged = json.loads(fresh["config_json"])
                except (TypeError, ValueError):
                    merged = {}

            merged["last_message_ts"] = newest_seen
            conn.execute(
                "UPDATE channel_accounts SET config_json = ? WHERE id = ?",
                (json.dumps(merged), account_id),
            )
            conn.commit()


def _process_thread(
    thread: Any, *, cursor: float, company_id: int, account_id: int, newest_seen: float
) -> float:
    """Hand every not-yet-seen customer message in one thread to the shared
    inbound pipeline. Returns the newest message timestamp seen, so the
    caller can advance its cursor past everything this pass processed."""
    customer_name = None
    others = [
        user
        for user in thread.users
        if getattr(user, "username", None)
    ]

    if len(others) == 1:
        customer_name = others[0].username

    for message in thread.messages:
        message_ts = message.timestamp.timestamp()

        if message.is_sent_by_viewer or message_ts <= cursor:
            continue

        text = (message.text or "").strip()

        if text:
            process_inbound_event(
                event={
                    "channel": "instagram_direct",
                    "user_id": str(thread.id),
                    "text": text,
                    "message_id": message.id,
                    "customer_name": customer_name,
                },
                company_id=company_id,
                channel_account_id=account_id,
            )

        newest_seen = max(newest_seen, message_ts)

    return newest_seen
