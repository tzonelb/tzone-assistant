"""Polling company mailboxes for new customer email.

Every other inbound channel this platform serves is a webhook: a provider
posts to a shared route, and `database_manager.resolve_account_for_channel`
works out whose account the delivery belongs to from an identifier carried in
the body. Email has no provider to call this platform back -- the only way in
is to ask a mailbox, over IMAP, whether anything new has arrived. So this is a
poller, not a route, and by the time it opens a connection it already knows
the company and the account: that is also why `resolve_account_for_channel`
carries no "email" entry, on purpose -- there is no anonymous delivery here
for it to route.

`backend/workers.py:email_poll_worker` calls `poll_all_accounts` on a timer.
Each account is isolated from the others the same way every sweep in that
module is: one company's unreachable or misconfigured mail server must not
stop the rest of the platform's mailboxes from being checked this cycle.
"""

from __future__ import annotations

import email as email_lib
import email.utils
import hashlib
import imaplib
import json
import logging
import re
from email.header import decode_header
from typing import Any

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial
from channels.inbound import process_inbound_event
from database.manager import database_manager


logger = logging.getLogger(__name__)

IMAP_TIMEOUT_SECONDS = 20

# A hard cap, not a tuning knob: without one, a single mailbox flooded with
# mail -- a mailing list misconfigured to point at it, a spam run -- would
# keep one sweep busy long enough to starve every other company's mailbox of
# its turn. The rest of what arrived is simply seen on the next poll instead.
MAX_MESSAGES_PER_POLL = 25


def poll_all_accounts() -> None:
    """One pass over every company's connected, active mailbox."""
    with database_manager.control() as conn:
        rows = conn.execute(
            "SELECT id FROM channel_accounts WHERE channel = 'email' AND status = 'active'"
        ).fetchall()

    for row in rows:
        account_id = int(row["id"])

        try:
            poll_account(account_id)
        except Exception:
            logger.exception("Polling email account %s failed", account_id)


def poll_account(account_id: int) -> None:
    """Fetch and process every unseen message in one mailbox."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id, external_account_id, access_token_sealed,
                   config_json
            FROM channel_accounts
            WHERE id = ? AND channel = 'email' AND status = 'active'
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        return

    company_id = int(row["company_id"])
    address = str(row["external_account_id"] or "").strip()

    if not address or not row["access_token_sealed"]:
        return

    try:
        password = keyring.unseal_secret(
            row["access_token_sealed"],
            database_manager.company_key(company_id),
            company_id,
            "access_token",
        )
    except CorruptedKeyMaterial:
        logger.error(
            "Mailbox password for company %s account %s could not be "
            "unsealed; skipping this poll rather than guessing it",
            company_id,
            account_id,
        )
        return

    config: dict[str, Any] = {}

    if row["config_json"]:
        try:
            config = json.loads(row["config_json"])
        except (TypeError, ValueError):
            config = {}

    host = config.get("imap_host")

    if not host or not password:
        return

    try:
        connection = _connect(config)
    except (OSError, imaplib.IMAP4.error) as exc:
        logger.warning(
            "Could not reach the IMAP server for company %s account %s: %s",
            company_id,
            account_id,
            exc,
        )
        return

    try:
        connection.login(address, password)
        connection.select("INBOX")

        status, data = connection.search(None, "UNSEEN")

        if status != "OK":
            return

        uids = (data[0] or b"").split()[:MAX_MESSAGES_PER_POLL]

        for uid in uids:
            try:
                _process_one(
                    connection, uid, company_id=company_id, account_id=account_id
                )
            except Exception:
                logger.exception(
                    "Could not process email uid=%r for company %s",
                    uid,
                    company_id,
                )
    except imaplib.IMAP4.error as exc:
        logger.warning(
            "IMAP error polling company %s account %s: %s",
            company_id,
            account_id,
            exc,
        )
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass

        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass


def _connect(config: dict[str, Any]):
    host = config.get("imap_host")
    port = int(config.get("imap_port") or 993)
    use_ssl = bool(config.get("imap_use_ssl", True))

    if use_ssl:
        return imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_SECONDS)

    return imaplib.IMAP4(host, port, timeout=IMAP_TIMEOUT_SECONDS)


def _process_one(connection, uid: bytes, *, company_id: int, account_id: int) -> None:
    """Read one message and hand it to the shared inbound pipeline.

    Fetched with `BODY.PEEK[]` rather than plain `BODY[]` -- the peek variant
    does not itself mark the message `\\Seen`, so a crash between the fetch
    and a successful `process_inbound_event` leaves it unseen and it is
    picked up again on the next sweep instead of being silently lost. The
    flag is only set once this platform has actually stored the message.
    """
    status, msg_data = connection.fetch(uid, "(BODY.PEEK[])")

    if status != "OK" or not msg_data or not msg_data[0]:
        return

    raw = msg_data[0][1]
    message = email_lib.message_from_bytes(raw)

    sender_name, sender_address = email.utils.parseaddr(message.get("From", ""))
    sender_address = (sender_address or "").strip().lower()

    if not sender_address:
        return

    message_id = str(message.get("Message-ID") or "").strip() or None

    if not message_id:
        # Some senders -- and some relays -- omit Message-ID entirely; legal
        # under RFC 5322, but it leaves this message with nothing
        # `idx_messages_provider` can key on. That index's uniqueness is
        # `WHERE provider_message_id IS NOT NULL`, so a NULL id sails
        # through dedup untouched. A crash between this message being stored
        # below and its `\Seen` flag being set reprocesses the same raw
        # bytes on the next sweep -- without a stand-in id here, that
        # reprocessing becomes a second, identical customer message in the
        # inbox rather than being caught the way every other provider's
        # retry already is.
        message_id = f"sha256:{hashlib.sha256(raw).hexdigest()}"

    subject = _decode_header(message.get("Subject"))
    body = _plain_text_body(message) or "(This message had no readable text.)"

    process_inbound_event(
        event={
            "channel": "email",
            "user_id": sender_address,
            "text": body,
            "message_id": message_id,
            "customer_name": _decode_header(sender_name) or None,
        },
        company_id=company_id,
        channel_account_id=account_id,
        extra_metadata={"email_subject": subject} if subject else None,
    )

    connection.store(uid, "+FLAGS", "(\\Seen)")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""

    decoded = ""

    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            decoded += text.decode(charset or "utf-8", errors="replace")
        else:
            decoded += text

    return decoded.strip()


def _plain_text_body(message) -> str:
    if not message.is_multipart():
        payload = message.get_payload(decode=True) or b""
        charset = message.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace").strip()

    for part in message.walk():
        disposition = str(part.get("Content-Disposition") or "")

        if part.get_content_type() == "text/plain" and "attachment" not in disposition:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace").strip()

    # No plain-text part -- fall back to a stripped HTML one rather than
    # dropping a message that has content, just not in the shape wanted.
    for part in message.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            return _strip_html(payload.decode(charset, errors="replace")).strip()

    return ""


def _strip_html(html: str) -> str:
    without_hidden = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_hidden)
    return re.sub(r"\s+", " ", without_tags)
