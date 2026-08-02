from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from database.database import db
from channels.telegram.sender import send_telegram_text, send_telegram_media
from channels.meta.sender import send_meta_text, send_meta_media
from channels.whatsapp.sender import send_whatsapp_text, send_whatsapp_media
from backend.services.customer_service import customer_service
from backend.services.message_status_service import message_status_service


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SUPPORTED_CHANNELS = {"telegram", "messenger", "instagram", "whatsapp"}
SUPPORTED_MEDIA_TYPES = {"image", "video", "audio"}


class BroadcastService:
    def __init__(self) -> None:
        # Schema setup happens explicitly via main.py's lifespan (after
        # database.database.db.create_tables()), not here — matches the
        # convention in CustomerService/ConversationControlService.
        pass

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    segment_id INTEGER,
                    lifecycle_stage TEXT,
                    tag TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    recipient_count INTEGER NOT NULL DEFAULT 0,
                    sent_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(segment_id) REFERENCES customer_segments(id) ON DELETE SET NULL,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            broadcast_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(broadcasts)").fetchall()
            }
            if "raw_numbers_json" not in broadcast_columns:
                conn.execute("ALTER TABLE broadcasts ADD COLUMN raw_numbers_json TEXT")
            if "media_url" not in broadcast_columns:
                conn.execute("ALTER TABLE broadcasts ADD COLUMN media_url TEXT")
            if "media_type" not in broadcast_columns:
                conn.execute("ALTER TABLE broadcasts ADD COLUMN media_type TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcast_recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    broadcast_id INTEGER NOT NULL,
                    customer_id INTEGER,
                    channel TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    provider_message_id TEXT,
                    send_status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES broadcasts(id) ON DELETE CASCADE
                )
                """
            )
            conn.commit()

    # ---------------------------------------------------------------
    # Recipient resolution — reuses the same filter dimensions as
    # customer_service.list_customers (lifecycle_stage, tag, and a
    # segment's saved filters), but resolves actual channel identities
    # to send to rather than a paginated customer list.
    # ---------------------------------------------------------------
    def _resolve_recipients(
        self,
        *,
        company_id: int,
        channel: str,
        segment_id: int | None = None,
        lifecycle_stage: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {}
        if segment_id is not None:
            with db.connect() as conn:
                segment_row = conn.execute(
                    "SELECT * FROM customer_segments WHERE id = ? AND company_id = ?",
                    (segment_id, company_id),
                ).fetchone()
            if not segment_row:
                raise KeyError("Segment not found")
            try:
                filters = json.loads(segment_row["filters_json"] or "{}")
            except (TypeError, ValueError):
                filters = {}
            if not isinstance(filters, dict):
                filters = {}

        # Explicit params always take precedence over (or add to) a segment's saved filters.
        if lifecycle_stage is not None:
            filters["lifecycle_stage"] = lifecycle_stage
        if tag is not None:
            filters["tag"] = tag

        where = ["ci.company_id = ?", "ci.channel = ?"]
        params: list[Any] = [company_id, channel]

        stage_value = filters.get("lifecycle_stage")
        if stage_value:
            where.append("c.lifecycle_stage = ?")
            params.append(str(stage_value).strip())

        tag_value = filters.get("tag")
        if tag_value:
            where.append("c.tags_json LIKE ?")
            params.append(f'%"{str(tag_value).strip()}"%')

        clause = " AND ".join(where)
        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT ci.customer_id, ci.external_user_id
                FROM customer_identities ci
                JOIN customers c ON c.id = ci.customer_id
                WHERE {clause}
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_number(number: str) -> str:
        """Basic cleanup only — not a phone-parsing library. Strips
        whitespace and any character that isn't a digit or a leading
        '+', e.g. "+1 555-0100" -> "+15550100". Two numbers that only
        differ by whether a '+' or country-code formatting was included
        are NOT considered equivalent by this normalization."""
        return re.sub(r"[^\d+]", "", (number or "").strip())

    def _upsert_recipients_from_numbers(
        self, *, company_id: int, numbers: list[str]
    ) -> list[dict[str, Any]]:
        """Resolves a raw pasted/uploaded number list into the same
        {"customer_id", "external_user_id"} shape _resolve_recipients()
        returns, so send_broadcast() doesn't need to know which targeting
        mode created the broadcast. Each normalized number is upserted as
        a Contact via customer_service.upsert_from_channel — the same
        find-or-create path every channel webhook already uses — so a
        number that's already a known WhatsApp contact reuses that
        customer_id instead of creating a duplicate."""
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
            recipients.append({"customer_id": customer["id"], "external_user_id": normalized})
        return recipients

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
                    f'"{media_type}" is not a supported media type. Choose one of: '
                    f'{", ".join(sorted(SUPPORTED_MEDIA_TYPES))}.'
                )
        else:
            media_type = None

        using_numbers = bool(numbers)
        if using_numbers and (segment_id is not None or lifecycle_stage is not None or tag is not None):
            raise ValueError("Choose either a segment/filter or a number list, not both.")
        if using_numbers and normalized_channel != "whatsapp":
            raise ValueError(
                "Number-list targeting is WhatsApp-only for now. Choose the WhatsApp "
                "channel, or target contacts with a segment/filter instead."
            )

        raw_numbers_json: str | None = None
        if using_numbers:
            recipients = self._upsert_recipients_from_numbers(company_id=company_id, numbers=numbers)
            raw_numbers_json = json.dumps([recipient["external_user_id"] for recipient in recipients])
        else:
            recipients = self._resolve_recipients(
                company_id=company_id,
                channel=normalized_channel,
                segment_id=segment_id,
                lifecycle_stage=lifecycle_stage,
                tag=tag,
            )

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO broadcasts (
                    company_id, name, message_text, channel, segment_id,
                    lifecycle_stage, tag, status, recipient_count,
                    sent_count, failed_count, created_by_user_id, created_at,
                    raw_numbers_json, media_url, media_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, name, message_text, normalized_channel, segment_id,
                    lifecycle_stage, tag, len(recipients), actor_user_id, now,
                    raw_numbers_json, media_url, media_type,
                ),
            )
            broadcast_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)

    def list_broadcasts(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM broadcasts WHERE company_id = ? ORDER BY created_at DESC, id DESC",
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_broadcast(self, *, company_id: int, broadcast_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM broadcasts WHERE id = ? AND company_id = ? LIMIT 1",
                (broadcast_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Broadcast not found")
        return dict(row)

    def _dispatch(
        self,
        *,
        channel: str,
        recipient_id: str,
        text: str,
        company_id: int,
        media_url: str | None = None,
        media_type: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Sends via the exact same per-channel send functions used for
        manual employee replies (see manual_messages.py's dispatch
        pattern). Returns (success, raw_result) — the raw sender
        response is preserved so callers can extract a provider_message_id
        or an error message, while success is still a normalized bool
        for the existing sent/failed counting."""
        if media_url and media_type:
            if channel == "telegram":
                result = send_telegram_media(recipient_id=recipient_id, media_url=media_url, media_type=media_type, caption=text)
                return bool(result.get("ok")), result
            elif channel == "whatsapp":
                result = send_whatsapp_media(recipient_id, media_url, media_type, caption=text, company_id=company_id)
                return bool(result.get("sent")), result
            else:
                result = send_meta_media(
                    recipient_id=recipient_id, media_url=media_url, media_type=media_type,
                    caption=text, channel=channel, company_id=company_id,
                )
                return bool(result.get("ok")), result

        if channel == "telegram":
            result = send_telegram_text(recipient_id=recipient_id, text=text)
            return bool(result.get("ok")), result
        elif channel == "whatsapp":
            result = send_whatsapp_text(recipient_id, text, company_id=company_id)
            return bool(result.get("sent")), result
        else:
            result = send_meta_text(
                recipient_id=recipient_id,
                text=text,
                channel=channel,
                company_id=company_id,
                is_human_agent=True,
            )
            return bool(result.get("ok")), result

    @staticmethod
    def _extract_provider_message_id(*, channel: str, raw_result: dict[str, Any]) -> Any:
        """Mirrors the exact per-channel extraction used for manual
        employee replies (backend/api/routes/manual_messages.py). There
        is no known extraction for whatsapp anywhere in this codebase —
        delivery/read tracking is simply unsupported for that channel."""
        if channel == "telegram":
            response_payload = raw_result.get("response", {})
            if isinstance(response_payload, dict):
                return response_payload.get("result", {}).get("message_id")
            return None
        elif channel in ("messenger", "instagram"):
            response_payload = raw_result.get("response", {})
            if isinstance(response_payload, dict):
                return response_payload.get("message_id")
            return None
        return None

    @staticmethod
    def _extract_error(*, raw_result: dict[str, Any]) -> str | None:
        if not isinstance(raw_result, dict):
            return None
        error = raw_result.get("error")
        if error:
            return str(error)
        return None

    def send_broadcast(self, *, company_id: int, broadcast_id: int) -> dict[str, Any]:
        # get_broadcast() first so a bad id 404s distinctly from "already
        # sent" (400). The UPDATE below is the actual guard against a
        # double-click / concurrent request racing two sends: only the
        # request that atomically flips draft -> sending proceeds: a
        # simple "read status, then send, then write status" would let
        # two overlapping requests both read 'draft' and both send.
        #
        # 'sending' is also accepted here as a starting status - this is
        # what makes a broadcast RESUMABLE. If the original request was
        # interrupted (client/proxy timeout, server restart) partway
        # through a large recipient list, the row is left in 'sending'
        # forever with the old code, since only 'draft' could start a
        # send and 'sending' is never re-finalized to 'sent' by anything
        # else. Calling this again on a 'sending' broadcast now resumes
        # it instead of being a permanent dead end.
        self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)
        with db.connect() as conn:
            cursor = conn.execute(
                "UPDATE broadcasts SET status = 'sending' WHERE id = ? AND company_id = ? AND status IN ('draft', 'sending')",
                (broadcast_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise ValueError("This broadcast has already been sent.")

        broadcast = self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)
        if broadcast.get("raw_numbers_json"):
            try:
                stored_numbers = json.loads(broadcast["raw_numbers_json"])
            except (TypeError, ValueError):
                stored_numbers = []
            if not isinstance(stored_numbers, list):
                stored_numbers = []
            recipients = self._upsert_recipients_from_numbers(company_id=company_id, numbers=stored_numbers)
        else:
            recipients = self._resolve_recipients(
                company_id=company_id,
                channel=broadcast["channel"],
                segment_id=broadcast["segment_id"],
                lifecycle_stage=broadcast["lifecycle_stage"],
                tag=broadcast["tag"],
            )

        # Resuming: skip anyone already confirmed sent, and clear out
        # stale 'failed' rows for anyone we're about to retry so this
        # attempt's outcome (not the earlier failed one) is what counts.
        with db.connect() as conn:
            already_sent = {
                row["external_user_id"]
                for row in conn.execute(
                    "SELECT external_user_id FROM broadcast_recipients WHERE broadcast_id = ? AND send_status = 'sent'",
                    (broadcast_id,),
                ).fetchall()
            }
            conn.execute(
                "DELETE FROM broadcast_recipients WHERE broadcast_id = ? AND send_status = 'failed'",
                (broadcast_id,),
            )
            conn.commit()
        recipients = [r for r in recipients if r["external_user_id"] not in already_sent]

        for recipient in recipients:
            error_message: str | None = None
            raw_result: dict[str, Any] = {}
            try:
                success, raw_result = self._dispatch(
                    channel=broadcast["channel"],
                    recipient_id=recipient["external_user_id"],
                    text=broadcast["message_text"],
                    company_id=company_id,
                    media_url=broadcast.get("media_url"),
                    media_type=broadcast.get("media_type"),
                )
            except Exception as exc:
                success = False
                error_message = str(exc)

            if not success and error_message is None:
                error_message = self._extract_error(raw_result=raw_result)

            provider_message_id = None
            if success:
                provider_message_id = self._extract_provider_message_id(
                    channel=broadcast["channel"], raw_result=raw_result
                )

            recipient_created_at = utc_now_iso()
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO broadcast_recipients (
                        broadcast_id, customer_id, channel, external_user_id,
                        provider_message_id, send_status, error, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        broadcast_id,
                        recipient.get("customer_id"),
                        broadcast["channel"],
                        recipient["external_user_id"],
                        str(provider_message_id) if provider_message_id else None,
                        "sent" if success else "failed",
                        error_message,
                        recipient_created_at,
                    ),
                )
                conn.commit()

            if provider_message_id:
                message_status_service.record_sent(
                    channel=broadcast["channel"],
                    provider_message_id=str(provider_message_id),
                    company_id=company_id,
                    recipient_id=recipient["external_user_id"],
                )

        now = utc_now_iso()
        with db.connect() as conn:
            # Totals reflect every attempt across the original send plus
            # any resume(s), not just this call's batch.
            totals = conn.execute(
                "SELECT send_status, COUNT(*) AS total FROM broadcast_recipients "
                "WHERE broadcast_id = ? GROUP BY send_status",
                (broadcast_id,),
            ).fetchall()
            totals_by_status = {row["send_status"]: row["total"] for row in totals}
            conn.execute(
                """
                UPDATE broadcasts
                SET status = 'sent', sent_count = ?, failed_count = ?, sent_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (totals_by_status.get("sent", 0), totals_by_status.get("failed", 0), now, broadcast_id, company_id),
            )
            conn.commit()
        return self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)

    def get_broadcast_report(self, *, company_id: int, broadcast_id: int) -> dict[str, Any]:
        broadcast = self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)

        with db.connect() as conn:
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
                WHERE br.broadcast_id = ?
                ORDER BY br.id ASC
                """,
                (broadcast_id,),
            ).fetchall()
        recipient_rows = [dict(row) for row in rows]

        channel_tracking_supported = broadcast["channel"] != "whatsapp"

        provider_message_ids = [
            row["provider_message_id"] for row in recipient_rows if row["provider_message_id"]
        ]
        statuses: dict[str, str] = {}
        if provider_message_ids:
            statuses = message_status_service.get_statuses(
                channel=broadcast["channel"], provider_message_ids=provider_message_ids
            )

        recipients: list[dict[str, Any]] = []
        totals = {
            "recipients": len(recipient_rows),
            "sent": 0,
            "failed": 0,
            "delivered": 0,
            "read": 0,
            "pending": 0,
        }
        for row in recipient_rows:
            send_status = row["send_status"]
            if send_status == "sent":
                totals["sent"] += 1
            elif send_status == "failed":
                totals["failed"] += 1

            delivery_status: str | None = None
            if row["provider_message_id"]:
                delivery_status = statuses.get(row["provider_message_id"])

            if delivery_status in ("delivered", "read"):
                totals["delivered"] += 1
            if delivery_status == "read":
                totals["read"] += 1
            if (
                send_status == "sent"
                and channel_tracking_supported
                and delivery_status not in ("delivered", "read")
            ):
                totals["pending"] += 1

            recipients.append(
                {
                    "customer_id": row["customer_id"],
                    "customer_name": row["customer_name"],
                    "external_user_id": row["external_user_id"],
                    "send_status": send_status,
                    "delivery_status": delivery_status if channel_tracking_supported else None,
                    "error": row["error"],
                }
            )

        return {
            "broadcast": broadcast,
            "totals": totals,
            "channel_tracking_supported": channel_tracking_supported,
            "recipients": recipients,
        }

    def delete_broadcast(self, *, company_id: int, broadcast_id: int) -> None:
        broadcast = self.get_broadcast(company_id=company_id, broadcast_id=broadcast_id)
        if broadcast["status"] != "draft":
            raise ValueError("Only draft broadcasts can be deleted.")
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM broadcasts WHERE id = ? AND company_id = ?",
                (broadcast_id, company_id),
            )
            conn.commit()


broadcast_service = BroadcastService()
