"""Broadcast campaigns: one message, sent once, to many contacts.

Ported from the design branch's `backend/services/broadcast_service.py`
(`origin/fix/release-timeout-and-channel-fixes`). The behaviour is theirs —
draft then send, the send lock, resuming an interrupted send, the recipient
recount, the per-recipient report. The storage and the sending are this
platform's, and the two differ enough to be worth stating once here rather
than at every call site.

*Every row lives in the company's own encrypted database.* The design branch
kept one shared unencrypted file and filtered by `company_id` in the WHERE
clause. Here `database_manager.tenant(company_id)` opens that company's file
and nothing else, so a broadcast belonging to another company is not merely
forbidden, it is not in the file being read. `company_id` is still stored and
still named in every query, for the reason the schema gives: it makes a
misrouted write detectable rather than silent.

*Table creation belongs to `database/schema_tenant.py` alone.* The design
branch's service created and patched its own tables in `ensure_schema()`;
this one only reads and writes, and a company behind the current schema is
brought up to it by `DatabaseManager.upgrade_tenant` at boot.

*Sending goes through `channels/sender.py`.* One dispatcher, per-company
credentials, the same path an employee's manual reply takes — see
`backend/api/routes/manual_messages.py`. A message a customer receives is
also recorded on that customer's conversation, because on this platform the
conversation is the record of what was said to them; a campaign that reached
somebody and left no trace in their thread would make the inbox lie.

*Two things the design branch's Broadcast expects have no equivalent here,
and both are refused rather than approximated:*

* **Segment / lifecycle-stage / tag targeting.** Their `customers` table
  carries `lifecycle_stage`, `tags_json` and `assigned_user_id`, and they
  have a `customer_segments` table of saved filters. This platform's
  contacts have none of those columns and there is no segments table, so
  there is nothing to resolve such a broadcast against. Creating one is
  refused with a message that says so. Sending to *everyone reachable on a
  channel*, and sending to a pasted number list, both work exactly as they
  do there.
* **Delivery and read receipts.** Theirs are fed by a
  `message_status_service` that webhook delivery/read events write into.
  Nothing on this platform records a provider's delivery or read event for
  an outbound message, so the report answers
  `channel_tracking_supported: False` — the same answer their report gives
  for WhatsApp, which they likewise cannot track. The screen already draws
  that case.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.customer_service import customer_service
from backend.services.media_upload_service import (
    MediaUploadError,
    media_upload_service,
)
from backend.services.message_service import message_service
from channels.sender import (
    SUPPORTED_CHANNELS,
    UnsupportedChannel,
    extract_error,
    send_media,
    send_text,
)
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# The design branch names the same four. `channels.sender` already declares
# them for this platform, so they are read from there rather than repeated —
# a channel this platform learns to send on becomes a channel a broadcast can
# use, with nothing here to keep in step.
SUPPORTED_MEDIA_TYPES = frozenset({"image", "video", "audio"})

# How long a send lock is honoured before it is assumed to belong to a request
# that died holding it. Ten minutes, as on the design branch.
SEND_LOCK_STALE_AFTER = timedelta(minutes=10)


class BroadcastService:
    # ------------------------------------------------------------------
    # Recipients
    # ------------------------------------------------------------------

    def _resolve_recipients(
        self,
        *,
        company_id: int,
        channel: str,
    ) -> list[dict[str, Any]]:
        """Every contact of this company reachable on `channel`.

        The design branch's version of this took `segment_id`,
        `lifecycle_stage` and `tag` and added them to the WHERE clause. Those
        columns do not exist on this platform's `customers` table, so this
        keeps only the part that does: the join from `customer_identities` —
        one row per channel identity a contact is known by — which is what
        produces the `{"customer_id", "external_user_id"}` pairs the send
        loop works from either way.

        A broadcast that asks for a filter this platform cannot resolve is
        refused in `create_broadcast` rather than quietly answered with this
        unfiltered list, because "everybody" is the one wrong answer that
        looks like it worked.
        """
        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT ci.customer_id AS customer_id,
                       ci.external_user_id AS external_user_id
                FROM customer_identities ci
                JOIN customers c ON c.id = ci.customer_id
                WHERE ci.company_id = ? AND ci.channel = ?
                ORDER BY ci.customer_id
                """,
                (company_id, channel),
            ).fetchall()

        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_number(number: str) -> str:
        """Basic cleanup only — not a phone-parsing library. Strips
        whitespace and any character that isn't a digit or a leading '+',
        e.g. "+1 555-0100" -> "+15550100". Two numbers that only differ by
        whether a '+' or country-code formatting was included are NOT
        considered equivalent by this normalization. Taken verbatim from the
        design branch."""
        return re.sub(r"[^\d+]", "", (number or "").strip())

    def _upsert_recipients_from_numbers(
        self, *, company_id: int, numbers: list[str]
    ) -> list[dict[str, Any]]:
        """Resolve a pasted number list into the same shape
        `_resolve_recipients` returns, so the send loop does not need to know
        which targeting mode created the broadcast.

        Each normalized number is upserted as a contact through
        `customer_service.upsert_from_channel` — the same find-or-create path
        every channel webhook already uses — so a number that is already a
        known WhatsApp contact reuses that contact instead of creating a
        duplicate.
        """
        seen: set[str] = set()
        recipients: list[dict[str, Any]] = []

        for raw_number in numbers:
            normalized = self._normalize_number(raw_number)

            if not normalized or normalized in seen:
                continue

            seen.add(normalized)
            customer = customer_service.upsert_from_channel(
                company_id=company_id,
                channel="whatsapp",
                external_user_id=normalized,
            )
            recipients.append(
                {"customer_id": customer["id"], "external_user_id": normalized}
            )

        return recipients

    # ------------------------------------------------------------------
    # Drafts
    # ------------------------------------------------------------------

    def create_broadcast(
        self,
        *,
        company_id: int,
        name: str,
        message_text: str,
        channel: str,
        segment_id: int | None = None,
        lifecycle_stage: str | None = None,
        tag: str | None = None,
        numbers: list[str] | None = None,
        media_url: str | None = None,
        media_type: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        name = (name or "").strip()

        if not name:
            raise ValueError("Broadcast name is required.")

        message_text = (message_text or "").strip()

        if not message_text:
            raise ValueError("Message text is required.")

        normalized_channel = (channel or "").strip().lower()

        if normalized_channel not in SUPPORTED_CHANNELS:
            raise ValueError(
                f'"{channel}" is not a supported channel. Choose one of: '
                f'{", ".join(sorted(SUPPORTED_CHANNELS))}.'
            )

        media_url = (media_url or "").strip() or None

        if media_url:
            media_type = (media_type or "").strip().lower()

            if media_type not in SUPPORTED_MEDIA_TYPES:
                raise ValueError(
                    f'"{media_type}" is not a supported media type. Choose one '
                    f'of: {", ".join(sorted(SUPPORTED_MEDIA_TYPES))}.'
                )

            # The provider fetches the file itself, so the address has to name
            # a file this platform stored for THIS company. Without the check
            # the platform becomes an open relay that fetches any URL an
            # employee names and delivers it from the company's own channel —
            # and could be pointed at another company's upload path. Same
            # check, same reason, as an attachment on a manual reply
            # (`backend/api/routes/manual_messages.py`).
            expected_prefix = f"/api/media/{company_id}/"

            if not media_url.startswith(expected_prefix):
                raise ValueError("Attach a file uploaded to this workspace.")

            # And it has to still be there. A draft naming a file that has
            # since been removed would fail once per recipient at send time,
            # reporting a whole campaign as rejected by the provider.
            try:
                media_upload_service.path_for(
                    company_id=company_id,
                    stored_name=media_url[len(expected_prefix):],
                )
            except MediaUploadError as exc:
                raise KeyError(str(exc)) from exc
        else:
            media_type = None

        using_numbers = bool(numbers)
        filtered = (
            segment_id is not None
            or lifecycle_stage is not None
            or tag is not None
        )

        if using_numbers and filtered:
            raise ValueError(
                "Choose either a segment/filter or a number list, not both."
            )

        if using_numbers and normalized_channel != "whatsapp":
            raise ValueError(
                "Number-list targeting is WhatsApp-only for now. Choose the "
                "WhatsApp channel, or target contacts with a segment/filter "
                "instead."
            )

        # The refusal the module docstring explains. Storing the broadcast and
        # resolving it to every contact on the channel would send a campaign
        # meant for one group of people to all of them.
        if filtered:
            raise ValueError(
                "Contacts on this platform carry no segment, lifecycle stage "
                "or tag, so a broadcast cannot be targeted by one yet. Send "
                "to a pasted number list, or to everyone reachable on the "
                "chosen channel."
            )

        raw_numbers_json: str | None = None

        if using_numbers:
            recipients = self._upsert_recipients_from_numbers(
                company_id=company_id, numbers=numbers or []
            )
            raw_numbers_json = json.dumps(
                [recipient["external_user_id"] for recipient in recipients]
            )
        else:
            recipients = self._resolve_recipients(
                company_id=company_id, channel=normalized_channel
            )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO broadcasts (
                    company_id, name, message_text, channel, segment_id,
                    lifecycle_stage, tag, status, recipient_count,
                    sent_count, failed_count, raw_numbers_json, media_url,
                    media_type, created_by_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    name,
                    message_text,
                    normalized_channel,
                    segment_id,
                    lifecycle_stage,
                    tag,
                    len(recipients),
                    raw_numbers_json,
                    media_url,
                    media_type,
                    actor_user_id,
                    now,
                ),
            )
            broadcast_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)

    def list_broadcasts(self, *, company_id: int) -> list[dict[str, Any]]:
        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT * FROM broadcasts
                WHERE company_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (int(company_id),),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_broadcast(self, *, company_id: int, broadcast_id: int) -> dict[str, Any]:
        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT * FROM broadcasts WHERE id = ? AND company_id = ? LIMIT 1",
                (int(broadcast_id), int(company_id)),
            ).fetchone()

        if not row:
            raise KeyError("Broadcast not found")

        return dict(row)

    def delete_broadcast(self, *, company_id: int, broadcast_id: int) -> None:
        broadcast = self.get_broadcast(
            company_id=company_id, broadcast_id=broadcast_id
        )

        if broadcast["status"] != "draft":
            raise ValueError("Only draft broadcasts can be deleted.")

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                "DELETE FROM broadcasts WHERE id = ? AND company_id = ?",
                (int(broadcast_id), int(company_id)),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def _recipients_for(
        self, *, company_id: int, broadcast: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Who this broadcast reaches, resolved fresh.

        A number-list broadcast re-upserts its stored numbers; anything else
        re-resolves against the contacts on its channel. Both the live recount
        and the send itself go through here, so what the confirm dialog shows
        and what the send does cannot drift apart.
        """
        if broadcast.get("raw_numbers_json"):
            try:
                stored_numbers = json.loads(broadcast["raw_numbers_json"])
            except (TypeError, ValueError):
                stored_numbers = []

            if not isinstance(stored_numbers, list):
                stored_numbers = []

            return self._upsert_recipients_from_numbers(
                company_id=company_id, numbers=stored_numbers
            )

        return self._resolve_recipients(
            company_id=company_id, channel=broadcast["channel"]
        )

    def preview_recipient_count(
        self, *, company_id: int, broadcast_id: int
    ) -> int:
        """How many contacts this draft would reach right now.

        `recipient_count` on the row is a snapshot taken at creation time — if
        the broadcast sits as a draft while contacts arrive or leave, that
        snapshot goes stale. The send always re-resolves, so this mirrors it
        for display.
        """
        broadcast = self.get_broadcast(
            company_id=company_id, broadcast_id=broadcast_id
        )

        if broadcast.get("raw_numbers_json"):
            # Counted from the stored list rather than by upserting every
            # number: a recount is a read, and it must not create contacts.
            try:
                stored_numbers = json.loads(broadcast["raw_numbers_json"])
            except (TypeError, ValueError):
                stored_numbers = []

            if not isinstance(stored_numbers, list):
                stored_numbers = []

            return len(stored_numbers)

        return len(
            self._resolve_recipients(
                company_id=company_id, channel=broadcast["channel"]
            )
        )

    def send_broadcast(self, *, company_id: int, broadcast_id: int) -> dict[str, Any]:
        """Send a draft, or resume one whose send was interrupted.

        `get_broadcast` first, so a bad id 404s distinctly from "already sent"
        (400). The UPDATE below is the real guard against two overlapping
        requests both sending: only the request that atomically flips the row
        to `sending` and claims the lock proceeds. Reading the status and then
        writing it would let both requests read `draft`.

        `sending` is accepted as a starting status, and that is what makes a
        broadcast resumable: a request killed partway through a long recipient
        list (a proxy timeout, a restart) leaves the row in `sending`, and
        nothing else would ever move it on. Calling this again picks up with
        whoever has not been sent to yet.

        The status check alone is not enough for that, though — two overlapping
        *resumes* would both match `status = 'sending'`. `send_lock_acquired_at`
        is the mutual-exclusion claim on top of it: only the request that
        clears a NULL lock, or one stale enough that its owner must have
        crashed, proceeds. It is released in the `finally` below so a genuine
        crash does not strand it for longer than that.
        """
        company_id = int(company_id)
        broadcast_id = int(broadcast_id)

        self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)

        lock_claimed_at = utc_now_iso()
        stale_before = (
            datetime.fromisoformat(lock_claimed_at) - SEND_LOCK_STALE_AFTER
        ).isoformat()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                UPDATE broadcasts
                SET status = 'sending', send_lock_acquired_at = ?
                WHERE id = ? AND company_id = ?
                  AND status IN ('draft', 'sending')
                  AND (send_lock_acquired_at IS NULL OR send_lock_acquired_at < ?)
                """,
                (lock_claimed_at, broadcast_id, company_id, stale_before),
            )
            claimed = cursor.rowcount
            conn.commit()

        if claimed == 0:
            current = self.get_broadcast(
                company_id=company_id, broadcast_id=broadcast_id
            )

            if current["status"] == "sending":
                raise ValueError(
                    "This broadcast is already being sent — please wait for "
                    "it to finish."
                )

            raise ValueError("This broadcast has already been sent.")

        try:
            return self._send_broadcast_locked(
                company_id=company_id, broadcast_id=broadcast_id
            )
        finally:
            with database_manager.tenant(company_id) as conn:
                conn.execute(
                    """
                    UPDATE broadcasts SET send_lock_acquired_at = NULL
                    WHERE id = ? AND company_id = ?
                    """,
                    (broadcast_id, company_id),
                )
                conn.commit()

    def _dispatch(
        self,
        *,
        company_id: int,
        channel: str,
        recipient_id: str,
        text: str,
        media_url: str | None,
        media_type: str | None,
    ) -> dict[str, Any]:
        """One message out, through the platform's one dispatcher.

        `channels/sender.py` resolves the company's own credentials and
        normalises every provider's answer to `ok`, so unlike the design
        branch — which called each channel's sender directly and had to know
        that WhatsApp reports `sent` while the others report `ok` — there is
        one shape to read here.
        """
        if media_url and media_type:
            # The channel fetches the file itself, so it needs an address on
            # the public internet. `media_url` is stored as this platform's
            # own path; `APP_PUBLIC_URL` is what makes it reachable.
            public_base = str(config.APP_PUBLIC_URL or "").rstrip("/")

            if not public_base.lower().startswith(("http://", "https://")):
                return {
                    "ok": False,
                    "error": (
                        "Attachments need APP_PUBLIC_URL set to this "
                        "platform's public address, because the channel "
                        "fetches the file itself."
                    ),
                }

            return send_media(
                channel=channel,
                recipient_id=recipient_id,
                company_id=company_id,
                media_url=f"{public_base}{media_url}",
                media_type=media_type,
                caption=text,
            )

        return send_text(
            channel=channel,
            recipient_id=recipient_id,
            company_id=company_id,
            text=text,
        )

    @staticmethod
    def _provider_message_id(send_result: dict[str, Any]) -> str | None:
        """The id the provider gave this message, where it gave one.

        Read exactly as a manual reply reads it
        (`backend/api/routes/manual_messages.py`). The design branch needed a
        per-channel extraction because its Telegram sender returned the Bot
        API body untouched; this platform's senders already normalise
        Telegram and Meta to the same `response.message_id`, so one reading
        covers both. WhatsApp Cloud returns its ids under a different key and
        neither branch extracts one.
        """
        response = send_result.get("response")

        if not isinstance(response, dict):
            return None

        message_id = response.get("message_id")

        return str(message_id) if message_id else None

    def _send_broadcast_locked(
        self, *, company_id: int, broadcast_id: int
    ) -> dict[str, Any]:
        broadcast = self.get_broadcast(
            company_id=company_id, broadcast_id=broadcast_id
        )
        recipients = self._recipients_for(
            company_id=company_id, broadcast=broadcast
        )

        # Resuming: skip anyone already confirmed sent, and clear the stale
        # `failed` rows of anyone about to be retried so this attempt's outcome
        # is what counts rather than the earlier one.
        with database_manager.tenant(company_id) as conn:
            already_sent = {
                row["external_user_id"]
                for row in conn.execute(
                    """
                    SELECT external_user_id FROM broadcast_recipients
                    WHERE company_id = ? AND broadcast_id = ?
                      AND send_status = 'sent'
                    """,
                    (company_id, broadcast_id),
                ).fetchall()
            }
            conn.execute(
                """
                DELETE FROM broadcast_recipients
                WHERE company_id = ? AND broadcast_id = ? AND send_status = 'failed'
                """,
                (company_id, broadcast_id),
            )
            conn.commit()

        # Everybody this campaign is for: whoever it resolves to now, plus
        # anybody already sent who no longer resolves (a contact removed since
        # the interrupted run still received the message).
        #
        # The design branch writes `fresh_total_count + len(already_sent)`
        # here, which counts an already-sent contact twice whenever they are
        # still in the fresh resolution -- the ordinary case. That leaves a
        # resumed campaign reporting more recipients than it has recipient
        # rows, so the report contradicts itself. Taking the union instead
        # gives the same answer as theirs whenever the two sets are disjoint,
        # which is the case their arithmetic was written for.
        total_recipients = len(
            {recipient["external_user_id"] for recipient in recipients}
            | already_sent
        )
        recipients = [
            recipient
            for recipient in recipients
            if recipient["external_user_id"] not in already_sent
        ]

        for recipient in recipients:
            error_message: str | None = None
            send_result: dict[str, Any] = {}

            try:
                send_result = self._dispatch(
                    company_id=company_id,
                    channel=broadcast["channel"],
                    recipient_id=recipient["external_user_id"],
                    text=broadcast["message_text"],
                    media_url=broadcast.get("media_url"),
                    media_type=broadcast.get("media_type"),
                )
                success = bool(send_result.get("ok"))
            except UnsupportedChannel as exc:
                success = False
                error_message = str(exc)
            except Exception as exc:
                # One recipient's provider failure must not abandon the rest
                # of the campaign, and the row below is what records why this
                # one did not arrive.
                logger.warning(
                    "Broadcast %s failed for one recipient on %s: %s",
                    broadcast_id,
                    broadcast["channel"],
                    exc,
                )
                success = False
                error_message = str(exc)

            if not success and error_message is None:
                error_message = extract_error(send_result)

            provider_message_id = (
                self._provider_message_id(send_result) if success else None
            )

            with database_manager.tenant(company_id) as conn:
                conn.execute(
                    """
                    INSERT INTO broadcast_recipients (
                        company_id, broadcast_id, customer_id, channel,
                        external_user_id, provider_message_id, send_status,
                        error, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        broadcast_id,
                        recipient.get("customer_id"),
                        broadcast["channel"],
                        recipient["external_user_id"],
                        provider_message_id,
                        "sent" if success else "failed",
                        error_message,
                        utc_now_iso(),
                    ),
                )
                conn.commit()

            if success:
                self._record_on_conversation(
                    company_id=company_id,
                    broadcast=broadcast,
                    external_user_id=recipient["external_user_id"],
                    provider_message_id=provider_message_id,
                )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            # Totals count every attempt across the original send and any
            # resume, not just this call's batch.
            totals_by_status = {
                row["send_status"]: int(row["total"])
                for row in conn.execute(
                    """
                    SELECT send_status, COUNT(*) AS total
                    FROM broadcast_recipients
                    WHERE company_id = ? AND broadcast_id = ?
                    GROUP BY send_status
                    """,
                    (company_id, broadcast_id),
                ).fetchall()
            }
            conn.execute(
                """
                UPDATE broadcasts
                SET status = 'sent', sent_count = ?, failed_count = ?,
                    sent_at = ?, recipient_count = ?
                WHERE id = ? AND company_id = ?
                """,
                (
                    totals_by_status.get("sent", 0),
                    totals_by_status.get("failed", 0),
                    now,
                    total_recipients,
                    broadcast_id,
                    company_id,
                ),
            )
            conn.commit()

        return self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)

    def _record_on_conversation(
        self,
        *,
        company_id: int,
        broadcast: dict[str, Any],
        external_user_id: str,
        provider_message_id: str | None,
    ) -> None:
        """Put a delivered campaign message into the customer's thread.

        This has no counterpart on the design branch, and it is here because
        of a rule this platform already holds rather than a feature invented
        for Broadcast: every outbound message a customer receives is saved on
        their conversation (`message_service.save_message`, as
        `manual_messages.py` does for a manual reply). Without it an employee
        opening the thread would see the customer's next message answering
        something the inbox never shows being said.

        A failure here is logged, not raised. The customer already has the
        message; turning a bookkeeping problem into a send failure would
        report a campaign as broken when it was delivered.
        """
        media_type = broadcast.get("media_type")
        text = broadcast["message_text"]
        metadata: dict[str, Any] = {"broadcast_id": broadcast["id"]}

        if broadcast.get("media_url"):
            metadata["media_url"] = broadcast["media_url"]
            metadata["media_type"] = media_type

        try:
            message_service.save_message(
                company_id=company_id,
                channel=broadcast["channel"],
                external_user_id=external_user_id,
                direction="out",
                text=text,
                sender_type="employee",
                sender_user_id=broadcast.get("created_by_user_id"),
                provider_message_id=provider_message_id,
                source="broadcast",
                metadata=metadata,
            )
        except Exception:
            logger.exception(
                "Could not record broadcast %s on the conversation with %s",
                broadcast["id"],
                external_user_id,
            )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_broadcast_report(
        self, *, company_id: int, broadcast_id: int
    ) -> dict[str, Any]:
        broadcast = self.get_broadcast(
            company_id=company_id, broadcast_id=broadcast_id
        )

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT
                    br.customer_id AS customer_id,
                    COALESCE(c.display_name, c.internal_name) AS customer_name,
                    br.external_user_id AS external_user_id,
                    br.provider_message_id AS provider_message_id,
                    br.send_status AS send_status,
                    br.error AS error
                FROM broadcast_recipients br
                LEFT JOIN customers c ON c.id = br.customer_id
                WHERE br.company_id = ? AND br.broadcast_id = ?
                ORDER BY br.id ASC
                """,
                (int(company_id), int(broadcast_id)),
            ).fetchall()

        recipient_rows = [dict(row) for row in rows]

        # False for every channel, not just WhatsApp as on the design branch:
        # nothing on this platform records a provider's delivery or read event
        # for an outbound message, so there is no status to report. Saying so
        # is the point — the screen draws "delivery/read tracking isn't
        # available on this channel" rather than showing zeroes that would
        # read as "nobody opened it".
        channel_tracking_supported = False

        totals = {
            "recipients": len(recipient_rows),
            "sent": 0,
            "failed": 0,
            "delivered": 0,
            "read": 0,
            "pending": 0,
        }
        recipients: list[dict[str, Any]] = []

        for row in recipient_rows:
            send_status = row["send_status"]

            if send_status == "sent":
                totals["sent"] += 1
            elif send_status == "failed":
                totals["failed"] += 1

            recipients.append(
                {
                    "customer_id": row["customer_id"],
                    "customer_name": row["customer_name"],
                    "external_user_id": row["external_user_id"],
                    "send_status": send_status,
                    "delivery_status": None,
                    "error": row["error"],
                }
            )

        return {
            "broadcast": broadcast,
            "totals": totals,
            "channel_tracking_supported": channel_tracking_supported,
            "recipients": recipients,
        }


broadcast_service = BroadcastService()
