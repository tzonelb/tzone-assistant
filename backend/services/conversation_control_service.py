
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import db
from channels.telegram.sender import send_telegram_text
from channels.meta.sender import send_meta_text
from channels.whatsapp.sender import send_whatsapp_text
from backend.services.message_status_service import message_status_service
from core.conversation_store import save_conversation_message

logger = logging.getLogger(__name__)


def _fire_conversation_closed_trigger(*, company_id: int, channel: str, external_user_id: str, department: str | None) -> None:
    """A conversation being marked closed/done fires the `conversation_closed`
    Reply Flow trigger (e.g. to ask the customer for a rating). Lazy import
    breaks the natural import cycle — core.reply_flow_engine already imports
    this module — mirroring the same lazy-import pattern main.py's lifespan
    uses for channels.telegram.manager. Never raises: a broken/misconfigured
    trigger flow must never block the real status change that fired it."""
    try:
        from core.reply_flow_engine import reply_flow_engine
        reply_flow_engine.fire_event(
            company_id=company_id, trigger_type="conversation_closed", channel=channel,
            external_user_id=external_user_id, department=department,
        )
    except Exception:
        logger.exception("conversation_closed reply flow trigger failed for %s/%s", channel, external_user_id)


VALID_STATUSES = {
    "new",
    "open",
    "ai_handling",
    "human_handling",
    "waiting_customer",
    "waiting_agent",
    "pending",
    "resolved",
    "closed",
    "archived",
}

# Business-tracking statuses an employee sets manually to mean "I've
# dealt with this" (as opposed to "ai_handling"/"human_handling", which
# just describe who's currently routing the conversation). An AI/human
# handoff transition must never silently clobber one of these back to
# "ai_handling" - that would make a conversation an agent just marked
# resolved impossible to find again by filtering on "Resolved".
MANUAL_STATUSES_SQL = "('waiting_customer','waiting_agent','pending','resolved','closed','archived')"


def _status_preserving_manual(new_status: str) -> str:
    """SQL fragment for a `status` column assignment: keeps whatever
    manual business status is already set, otherwise applies new_status.
    new_status is always one of two hardcoded literals from this module,
    never user input - safe to inline directly."""
    return f"CASE WHEN status IN {MANUAL_STATUSES_SQL} THEN status ELSE '{new_status}' END"

VALID_PRIORITIES = {
    "low",
    "normal",
    "high",
    "urgent",
}

DEFAULT_TAKEOVER_MINUTES = 5


class ConversationOwnershipConflict(RuntimeError):
    def __init__(self, owner_user_id: int | None) -> None:
        self.owner_user_id = owner_user_id
        super().__init__(
            "Conversation is currently owned by another employee."
        )


def _takeover_timeout_minutes(company_id: int) -> int:
    """Return the company-configured human takeover timeout safely.

    The import stays local to avoid coupling schema initialization order.
    """
    try:
        from backend.services.company_settings_service import company_settings_service

        values = company_settings_service.get_section(company_id, "ai_behavior")["values"]
        value = int(values.get("return_to_ai_timeout_minutes", DEFAULT_TAKEOVER_MINUTES))
    except (ImportError, KeyError, TypeError, ValueError):
        value = DEFAULT_TAKEOVER_MINUTES

    return max(1, min(1440, value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_datetime(
    value: Any,
) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc,
            )

        return parsed.astimezone(
            timezone.utc,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


class ConversationControlService:
    def __init__(self) -> None:
        # Schema setup happens explicitly via main.py's lifespan (after
        # database.database.db.create_tables()), not here. Calling
        # ensure_schema() eagerly at import time raced ahead of that
        # order — this singleton gets constructed the moment any module
        # imports it, which can happen before db.create_tables() runs,
        # silently creating tables with a stale/incomplete schema.
        pass

    @staticmethod
    def _table_columns(
        conn,
        table_name: str,
    ) -> set[str]:
        rows = conn.execute(
            f"""
            PRAGMA table_info({table_name})
            """
        ).fetchall()

        return {
            str(row["name"])
            for row in rows
        }

    @staticmethod
    def _table_exists(
        conn,
        table_name: str,
    ) -> bool:
        row = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()

        return row is not None

    @staticmethod
    def _add_missing_columns(
        conn,
        table_name: str,
        definitions: dict[str, str],
    ) -> None:
        existing_columns = (
            ConversationControlService
            ._table_columns(
                conn,
                table_name,
            )
        )

        for (
            column_name,
            definition,
        ) in definitions.items():
            if column_name in existing_columns:
                continue

            conn.execute(
                f"""
                ALTER TABLE {table_name}
                ADD COLUMN {column_name}
                {definition}
                """
            )

            existing_columns.add(
                column_name
            )

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            if not self._table_exists(
                conn,
                "conversations",
            ):
                conn.execute(
                    """
                    CREATE TABLE conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        channel TEXT NOT NULL,
                        external_user_id TEXT NOT NULL,
                        status TEXT NOT NULL
                            DEFAULT 'ai_handling',
                        workflow_state TEXT NOT NULL
                            DEFAULT 'ai_active',
                        ai_enabled INTEGER NOT NULL
                            DEFAULT 1,
                        handled_by_ai INTEGER NOT NULL
                            DEFAULT 1,
                        priority TEXT NOT NULL
                            DEFAULT 'normal',
                        department TEXT
                            DEFAULT 'Unassigned',
                        assigned_user_id INTEGER,
                        needs_human INTEGER NOT NULL
                            DEFAULT 0,
                        unread_count INTEGER NOT NULL
                            DEFAULT 0,
                        takeover_expires_at TEXT,
                        human_last_reply_at TEXT,
                        last_message_at TEXT,
                        branch_id INTEGER,
                        channel_account_id INTEGER,
                        customer_alias TEXT,
                        official_customer_name TEXT,
                        customer_profile_picture TEXT,
                        folder TEXT NOT NULL DEFAULT 'inbox',
                        is_starred INTEGER NOT NULL DEFAULT 0,
                        is_pinned INTEGER NOT NULL DEFAULT 0,
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

            self._add_missing_columns(
                conn,
                "conversations",
                {
                    "handled_by_ai":
                        "INTEGER NOT NULL DEFAULT 1",
                    "workflow_state":
                        "TEXT NOT NULL DEFAULT 'ai_active'",
                    "priority":
                        "TEXT NOT NULL DEFAULT 'normal'",
                    "department":
                        "TEXT DEFAULT 'Unassigned'",
                    "assigned_user_id":
                        "INTEGER",
                    "needs_human":
                        "INTEGER NOT NULL DEFAULT 0",
                    "unread_count":
                        "INTEGER NOT NULL DEFAULT 0",
                    "takeover_expires_at":
                        "TEXT",
                    "human_last_reply_at":
                        "TEXT",
                    "last_message_at":
                        "TEXT",
                    "branch_id":
                        "INTEGER",
                    "channel_account_id":
                        "INTEGER",
                    "customer_alias":
                        "TEXT",
                    "official_customer_name":
                        "TEXT",
                    "customer_profile_picture":
                        "TEXT",
                    "folder":
                        "TEXT NOT NULL DEFAULT 'inbox'",
                    "is_starred":
                        "INTEGER NOT NULL DEFAULT 0",
                    "is_pinned":
                        "INTEGER NOT NULL DEFAULT 0",
                    "tags_json":
                        "TEXT NOT NULL DEFAULT '[]'",
                    "reminder_at":
                        "TEXT",
                    "reminder_note":
                        "TEXT",
                    "reminder_set_by_user_id":
                        "INTEGER",
                    "reminder_notified_at":
                        "TEXT",
                    "reminder_auto_send":
                        "INTEGER NOT NULL DEFAULT 0",
                    "reminder_message_text":
                        "TEXT",
                    "reminder_set_at":
                        "TEXT",
                },
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS
                conversation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL,
                    actor_user_id INTEGER,
                    event_type TEXT NOT NULL,
                    event_data_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id)
                        REFERENCES conversations(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id)
                        REFERENCES users(id)
                        ON DELETE SET NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS
                conversation_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL,
                    author_user_id INTEGER,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id)
                        REFERENCES conversations(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(author_user_id)
                        REFERENCES users(id)
                        ON DELETE SET NULL
                )
                """
            )

            self._add_missing_columns(
                conn,
                "conversation_notes",
                {
                    "mentioned_user_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS branches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL
                        DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id)
                        REFERENCES companies(id)
                        ON DELETE CASCADE
                )
                """
            )

            # channel_accounts is owned by database/database.py's
            # create_tables() (the real schema: channel, name, access
            # tokens, ai_enabled/flow_enabled/etc — actively used
            # elsewhere). A second, incompatible definition used to live
            # here (channel_type/display_name, not read anywhere else)
            # that would win the race if this service's singleton got
            # imported before db.create_tables() ran, silently leaving
            # the wrong schema in place. Removed rather than reconciled
            # to avoid two sources of truth for the same table.

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    color TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(company_id, normalized_name),
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_conversations_control_lookup
                ON conversations (
                    company_id,
                    channel,
                    external_user_id
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_conversation_events_lookup
                ON conversation_events (
                    conversation_id,
                    id DESC
                )
                """
            )

            conn.commit()

    def resolve_default_company_id(
        self,
    ) -> int:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM companies
                WHERE status = 'active'
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()

            if row:
                return int(row["id"])

            row = conn.execute(
                """
                SELECT id
                FROM companies
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()

            if row:
                return int(row["id"])

        return 1

    @staticmethod
    def row_to_dict(
        row,
    ) -> dict[str, Any]:
        result = dict(row)

        result["handled_by_ai"] = bool(
            result.get(
                "handled_by_ai",
                1,
            )
        )

        result["ai_enabled"] = bool(
            result.get(
                "ai_enabled",
                1,
            )
        )

        result["needs_human"] = bool(
            result.get(
                "needs_human",
                0,
            )
        )

        result["unread_count"] = int(
            result.get(
                "unread_count",
                0,
            )
            or 0
        )

        result["is_starred"] = bool(
            result.get("is_starred", 0)
        )
        result["is_pinned"] = bool(
            result.get("is_pinned", 0)
        )

        raw_tags = result.pop("tags_json", "[]")
        try:
            parsed_tags = json.loads(raw_tags or "[]")
            result["tags"] = (
                parsed_tags
                if isinstance(parsed_tags, list)
                else []
            )
        except (json.JSONDecodeError, TypeError):
            result["tags"] = []

        return result

    def get_or_create(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
    ) -> dict[str, Any]:
        normalized_channel = (
            channel.strip().lower()
        )

        normalized_user_id = (
            external_user_id.strip()
        )

        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE company_id = ?
                  AND channel = ?
                  AND external_user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    company_id,
                    normalized_channel,
                    normalized_user_id,
                ),
            ).fetchone()

            if row:
                return self.row_to_dict(
                    row
                )

            now = utc_now_iso()

            cursor = conn.execute(
                """
                INSERT INTO conversations (
                    company_id,
                    channel,
                    external_user_id,
                    status,
                    workflow_state,
                    ai_enabled,
                    handled_by_ai,
                    priority,
                    department,
                    needs_human,
                    unread_count,
                    last_message_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    'ai_handling',
                    'ai_active',
                    1,
                    1,
                    'normal',
                    'Unassigned',
                    0,
                    0,
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    company_id,
                    normalized_channel,
                    normalized_user_id,
                    now,
                    now,
                    now,
                ),
            )

            conversation_id = (
                cursor.lastrowid
            )

            conn.commit()

            row = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE id = ?
                """,
                (conversation_id,),
            ).fetchone()

            return self.row_to_dict(
                row
            )

    def insert_event(
        self,
        conn,
        conversation_id: int,
        company_id: int,
        actor_user_id: int | None,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload = data or {}
        if payload.get("from") == payload.get("to") and "from" in payload:
            return

        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        recent = conn.execute(
            """
            SELECT event_data_json, created_at
            FROM conversation_events
            WHERE conversation_id = ?
              AND company_id = ?
              AND event_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (conversation_id, company_id, event_type),
        ).fetchone()
        if recent and str(recent["event_data_json"] or "") == encoded:
            created = parse_datetime(recent["created_at"])
            if created and (utc_now() - created).total_seconds() < 5:
                return

        conn.execute(
            """
            INSERT INTO conversation_events (
                conversation_id,
                company_id,
                actor_user_id,
                event_type,
                event_data_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                company_id,
                actor_user_id,
                event_type,
                encoded,
                utc_now_iso(),
            ),
        )

    def expire_overdue_takeovers(
        self,
    ) -> int:
        current_time = utc_now()
        expired_count = 0

        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE handled_by_ai = 0
                  AND takeover_expires_at IS NOT NULL
                """
            ).fetchall()

            for row in rows:
                state = self.row_to_dict(
                    row
                )

                expiry = parse_datetime(
                    state.get(
                        "takeover_expires_at"
                    )
                )

                if (
                    expiry is None
                    or current_time < expiry
                ):
                    continue

                conn.execute(
                    f"""
                    UPDATE conversations
                    SET
                        handled_by_ai = 1,
                        ai_enabled = 1,
                        status = {_status_preserving_manual('ai_handling')},
                        workflow_state =
                            CASE
                                WHEN unread_count > 0 THEN 'waiting_ai'
                                ELSE 'ai_active'
                            END,
                        needs_human = 0,
                        takeover_expires_at = NULL,
                        assigned_user_id = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        utc_now_iso(),
                        state["id"],
                    ),
                )

                self.insert_event(
                    conn=conn,
                    conversation_id=(
                        state["id"]
                    ),
                    company_id=(
                        state["company_id"]
                    ),
                    actor_user_id=None,
                    event_type=(
                        "automatically_returned_to_ai"
                    ),
                    data={
                        "reason":
                            "employee_response_timeout",
                        "timeout_minutes":
                            _takeover_timeout_minutes(int(state["company_id"])),
                    },
                )

                expired_count += 1

            conn.commit()

        return expired_count

    def set_reminder(
        self,
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        reminder_at: str,
        note: str | None,
        actor_user_id: int | None,
        auto_send: bool = False,
        message_text: str | None = None,
    ) -> dict[str, Any]:
        """Set (or replace) a follow-up reminder on a conversation.
        reminder_at is an ISO timestamp — when it's reached, a
        notification is created (see check_due_reminders).

        auto_send opts into also sending a pre-authored message_text
        automatically when the reminder fires (subject to the safety
        checks in check_due_reminders) instead of only notifying the
        employee. The employee-authored text is never generated by the
        system — auto_send just controls whether it gets sent for them
        automatically or left for them to send by hand.

        reminder_set_at captures "now" as its own column (never reuses
        updated_at, which is bumped by unrelated actions like
        department/priority changes) so check_due_reminders can later
        tell whether the customer has sent a new message since this
        reminder was armed.
        """
        clean_message_text = (message_text or "").strip() or None
        if auto_send and not clean_message_text:
            raise ValueError("Auto-send requires a message.")

        state = self.get_or_create(
            company_id=company_id, channel=channel, external_user_id=external_user_id,
        )
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET reminder_at = ?, reminder_note = ?, reminder_set_by_user_id = ?,
                    reminder_notified_at = NULL, reminder_auto_send = ?,
                    reminder_message_text = ?, reminder_set_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    reminder_at, note, actor_user_id,
                    1 if auto_send else 0, clean_message_text, now, now,
                    state["id"],
                ),
            )
            conn.commit()
        return self.get_state(company_id=company_id, channel=channel, external_user_id=external_user_id)

    def clear_reminder(
        self, *, company_id: int, channel: str, external_user_id: str,
    ) -> dict[str, Any]:
        state = self.get_or_create(
            company_id=company_id, channel=channel, external_user_id=external_user_id,
        )
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET reminder_at = NULL, reminder_note = NULL, reminder_notified_at = NULL,
                    reminder_auto_send = 0, reminder_message_text = NULL, reminder_set_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (utc_now_iso(), state["id"]),
            )
            conn.commit()
        return self.get_state(company_id=company_id, channel=channel, external_user_id=external_user_id)

    @staticmethod
    def _dispatch_auto_follow_up(
        *, channel: str, recipient_id: str, text: str, company_id: int,
    ) -> tuple[bool, dict[str, Any]]:
        """Sends the employee-authored follow-up text via the same
        per-channel send functions used for manual employee replies and
        broadcasts (manual_messages.py / broadcast_service.py). There is
        no AI-generated content here — text is exactly what the employee
        wrote when arming the reminder."""
        if channel == "telegram":
            result = send_telegram_text(recipient_id=recipient_id, text=text)
            return bool(result.get("ok")), result
        elif channel == "whatsapp":
            result = send_whatsapp_text(recipient_id, text, company_id=company_id)
            return bool(result.get("sent")), result
        elif channel in ("messenger", "instagram"):
            result = send_meta_text(
                recipient_id=recipient_id,
                text=text,
                channel=channel,
                company_id=company_id,
                is_human_agent=True,
            )
            return bool(result.get("ok")), result
        else:
            return False, {"error": f'Unsupported channel for auto follow-up: "{channel}".'}

    @staticmethod
    def _extract_auto_follow_up_provider_id(
        *, channel: str, raw_result: dict[str, Any],
    ) -> Any:
        """Mirrors the exact per-channel extraction used for manual
        employee replies and broadcasts. No known extraction exists for
        whatsapp anywhere in this codebase."""
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

    def check_due_reminders(self) -> list[dict[str, Any]]:
        """Called periodically by a background worker. Finds every
        reminder whose time has passed and hasn't been notified yet,
        claims each one, then (for reminders with auto-send armed and
        both safety checks passing) sends the employee's pre-authored
        follow-up text. Returns what fired, including whether auto-send
        happened / was skipped (with why) / wasn't requested, so
        main.py can build an accurate notification and so tests can
        assert on it.

        DOUBLE-SEND GUARD: reminder_notified_at is read+claimed and
        committed in one transaction, BEFORE any notification/send work
        runs below. Once a row's reminder_notified_at is written and
        committed, the WHERE reminder_notified_at IS NULL clause makes
        it invisible to every subsequent (or concurrent) call to this
        method — including a second call fired 30s later while a slow
        send from this call is still in flight, or a call made by a
        second worker process. The claim can never be "undone" by a
        failed or slow send further down, because nothing below this
        transaction ever touches reminder_notified_at again.
        """
        now_iso = utc_now_iso()
        with db.connect() as conn:
            due = conn.execute(
                """
                SELECT id, company_id, channel, external_user_id, reminder_note,
                       reminder_set_by_user_id, official_customer_name, customer_alias,
                       reminder_auto_send, reminder_message_text, reminder_set_at,
                       ai_enabled, handled_by_ai, last_message_at
                FROM conversations
                WHERE reminder_at IS NOT NULL
                  AND reminder_at <= ?
                  AND reminder_notified_at IS NULL
                """,
                (now_iso,),
            ).fetchall()

            claimed = [dict(row) for row in due]
            for row in claimed:
                conn.execute(
                    """
                    UPDATE conversations
                    SET reminder_notified_at = ?
                    WHERE id = ? AND reminder_notified_at IS NULL
                    """,
                    (now_iso, row["id"]),
                )
            conn.commit()

        # Everything below runs only AFTER the claim above is committed.
        # Sending (network I/O) happens outside the transaction so a slow
        # or hung send can never hold the conversations table locked.
        fired: list[dict[str, Any]] = []
        for row in claimed:
            result = dict(row)

            auto_send_requested = bool(row.get("reminder_auto_send"))
            auto_send_status = "not_requested"
            auto_send_skip_reason: str | None = None

            if auto_send_requested:
                message_text = (row.get("reminder_message_text") or "").strip()
                is_ai_handling = bool(row.get("handled_by_ai")) and bool(row.get("ai_enabled"))
                reminder_set_at = parse_datetime(row.get("reminder_set_at"))
                last_message_at = parse_datetime(row.get("last_message_at"))

                # SAFETY CHECK 1 (customer already replied): compare the
                # timestamp captured at the moment the reminder was armed
                # (reminder_set_at — never updated_at, which unrelated
                # actions like department changes also bump) against
                # last_message_at. A strict "later than" means an inbound
                # or outbound message happened after the reminder was
                # set. Parsed as datetimes (not raw string compare) so
                # differing ISO fractional-second formatting can't cause
                # a false read.
                customer_already_replied = bool(
                    reminder_set_at is not None
                    and last_message_at is not None
                    and last_message_at > reminder_set_at
                )

                # SAFETY CHECK 2 (human owns the conversation): mirrors
                # is_ai_handling()'s exact definition — both handled_by_ai
                # and ai_enabled must be 1, otherwise a human has taken
                # over and an automated send would step on them.
                if reminder_set_at is None:
                    # Can't verify safety without a captured baseline —
                    # fail closed rather than assume it's safe to send.
                    auto_send_status = "skipped"
                    auto_send_skip_reason = "missing_reminder_set_at"
                elif not is_ai_handling:
                    auto_send_status = "skipped"
                    auto_send_skip_reason = "human_owned"
                elif customer_already_replied:
                    auto_send_status = "skipped"
                    auto_send_skip_reason = "customer_already_replied"
                elif not message_text:
                    auto_send_status = "skipped"
                    auto_send_skip_reason = "missing_message_text"
                else:
                    try:
                        success, raw_result = self._dispatch_auto_follow_up(
                            channel=row["channel"],
                            recipient_id=row["external_user_id"],
                            text=message_text,
                            company_id=row["company_id"],
                        )
                    except Exception as exc:
                        success = False
                        raw_result = {"error": str(exc)}

                    if success:
                        auto_send_status = "sent"
                        provider_message_id = self._extract_auto_follow_up_provider_id(
                            channel=row["channel"], raw_result=raw_result,
                        )
                        save_conversation_message(
                            channel=row["channel"],
                            user_id=row["external_user_id"],
                            direction="out",
                            text=message_text,
                            metadata={
                                "source": "auto_follow_up",
                                "sender_type": "system",
                                "reminder_set_by_user_id": row.get("reminder_set_by_user_id"),
                                "provider_message_id": provider_message_id,
                            },
                        )
                        if provider_message_id:
                            message_status_service.record_sent(
                                channel=row["channel"],
                                provider_message_id=str(provider_message_id),
                                company_id=row["company_id"],
                                recipient_id=row["external_user_id"],
                            )
                    else:
                        auto_send_status = "failed"
                        auto_send_skip_reason = (
                            str(raw_result.get("error"))
                            if isinstance(raw_result, dict) and raw_result.get("error")
                            else "send_failed"
                        )

            result["auto_send_requested"] = auto_send_requested
            result["auto_send_status"] = auto_send_status
            result["auto_send_skip_reason"] = auto_send_skip_reason
            fired.append(result)

        return fired

    def get_state(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
    ) -> dict[str, Any]:
        self.expire_overdue_takeovers()

        return self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=(
                external_user_id
            ),
        )

    def is_ai_handling(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
    ) -> bool:
        state = self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=(
                external_user_id
            ),
        )

        return bool(
            state.get(
                "handled_by_ai",
                True,
            )
        ) and bool(
            state.get(
                "ai_enabled",
                True,
            )
        )

    def set_ai_mode(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        handled_by_ai: bool,
        actor_user_id: int,
    ) -> dict[str, Any]:
        state = self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        now = utc_now_iso()
        expires_at = None
        if not handled_by_ai:
            expires_at = (
                utc_now()
                + timedelta(minutes=_takeover_timeout_minutes(company_id))
            ).isoformat()

        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                """
                SELECT * FROM conversations
                WHERE id = ? AND company_id = ?
                LIMIT 1
                """,
                (state["id"], company_id),
            ).fetchone()
            if current is None:
                conn.rollback()
                raise ValueError("Conversation not found.")

            current_state = self.row_to_dict(current)
            current_owner = current_state.get("assigned_user_id")
            if (
                not handled_by_ai
                and not bool(current_state.get("handled_by_ai", True))
                and current_owner not in (None, actor_user_id)
            ):
                conn.rollback()
                raise ConversationOwnershipConflict(
                    int(current_owner) if current_owner is not None else None
                )

            old_status = current_state.get("status") or (
                "ai_handling"
                if current_state.get("handled_by_ai", True)
                else "human_handling"
            )
            new_status = "ai_handling" if handled_by_ai else "human_handling"

            if handled_by_ai:
                conn.execute(
                    f"""
                    UPDATE conversations
                    SET handled_by_ai = 1,
                        ai_enabled = 1,
                        status = {_status_preserving_manual('ai_handling')},
                        workflow_state = CASE
                            WHEN unread_count > 0 THEN 'waiting_ai'
                            ELSE 'ai_active'
                        END,
                        needs_human = 0,
                        assigned_user_id = NULL,
                        takeover_expires_at = NULL,
                        updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (now, state["id"], company_id),
                )
            else:
                cursor = conn.execute(
                    f"""
                    UPDATE conversations
                    SET handled_by_ai = 0,
                        ai_enabled = 0,
                        status = {_status_preserving_manual('human_handling')},
                        workflow_state = 'human_active',
                        needs_human = 1,
                        assigned_user_id = ?,
                        takeover_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND company_id = ?
                      AND (
                          handled_by_ai = 1
                          OR assigned_user_id IS NULL
                          OR assigned_user_id = ?
                      )
                    """,
                    (
                        actor_user_id, expires_at, now,
                        state["id"], company_id, actor_user_id,
                    ),
                )
                if cursor.rowcount != 1:
                    owner = conn.execute(
                        "SELECT assigned_user_id FROM conversations WHERE id = ?",
                        (state["id"],),
                    ).fetchone()
                    conn.rollback()
                    raise ConversationOwnershipConflict(
                        int(owner["assigned_user_id"])
                        if owner and owner["assigned_user_id"] is not None
                        else None
                    )

            self.insert_event(
                conn=conn,
                conversation_id=int(state["id"]),
                company_id=company_id,
                actor_user_id=actor_user_id,
                event_type="returned_to_ai" if handled_by_ai else "human_takeover",
                data={
                    "from": old_status,
                    "to": new_status,
                    "takeover_expires_at": expires_at,
                },
            )
            conn.commit()

        return self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

    def record_opened(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        actor_user_id: int,
    ) -> dict[str, Any]:
        """Mark read only for the employee who currently owns the chat.

        Opening an unassigned or another employee's conversation must never
        hide unread work from the responsible employee.
        """
        state = self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        if state.get("assigned_user_id") != actor_user_id:
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.insert_event(
                    conn=conn,
                    conversation_id=int(state["id"]),
                    company_id=company_id,
                    actor_user_id=actor_user_id,
                    event_type="conversation_viewed",
                    data={"was_owner": False},
                )
                conn.commit()
            return state

        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE conversations
                SET unread_count = 0,
                    workflow_state = CASE
                        WHEN handled_by_ai = 1 THEN 'ai_active'
                        ELSE 'human_active'
                    END,
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                  AND assigned_user_id = ?
                  AND unread_count > 0
                """,
                (utc_now_iso(), state["id"], company_id, actor_user_id),
            )
            if cursor.rowcount:
                self.insert_event(
                    conn=conn,
                    conversation_id=int(state["id"]),
                    company_id=company_id,
                    actor_user_id=actor_user_id,
                    event_type="conversation_read",
                    data={"source": "owner_opened"},
                )
            conn.commit()

        return self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

    def assert_can_reply(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        actor_user_id: int,
    ) -> dict[str, Any]:
        """Require an active human lock owned by the replying employee."""
        state = self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

        owner_id = state.get("assigned_user_id")
        if bool(state.get("handled_by_ai", True)) or bool(state.get("ai_enabled", True)):
            raise ConversationOwnershipConflict(
                int(owner_id) if owner_id is not None else None
            )
        if owner_id is None or int(owner_id) != int(actor_user_id):
            raise ConversationOwnershipConflict(
                int(owner_id) if owner_id is not None else None
            )
        return state

    def renew_reply_lease(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        actor_user_id: int,
    ) -> dict[str, Any]:
        """Verify the replying employee still owns this human-handled
        conversation, and extend the takeover lease (takeover_expires_at)
        so it doesn't expire out from under them while they're actively
        replying.

        FIX (Patch 9.1 follow-up): this method was being called from
        manual_messages.py but was never implemented, causing every manual
        reply to crash with AttributeError / 500. This restores the P0
        item from the report: "تجديد مدة الملكية عند كل Reply".
        """
        state = self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")

            fresh = conn.execute(
                """
                SELECT * FROM conversations
                WHERE id = ? AND company_id = ?
                LIMIT 1
                """,
                (state["id"], company_id),
            ).fetchone()

            if fresh is None:
                conn.rollback()
                raise ValueError("Conversation not found.")

            fresh_state = self.row_to_dict(fresh)
            owner_id = fresh_state.get("assigned_user_id")
            is_ai_handled = bool(
                fresh_state.get("handled_by_ai", True)
            ) or bool(fresh_state.get("ai_enabled", True))

            if (
                is_ai_handled
                or owner_id is None
                or int(owner_id) != int(actor_user_id)
            ):
                conn.rollback()
                raise ConversationOwnershipConflict(
                    int(owner_id) if owner_id is not None else None
                )

            expires_at = (
                utc_now()
                + timedelta(
                    minutes=_takeover_timeout_minutes(company_id)
                )
            ).isoformat()

            conn.execute(
                """
                UPDATE conversations
                SET takeover_expires_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                  AND assigned_user_id = ?
                  AND handled_by_ai = 0
                  AND ai_enabled = 0
                """,
                (
                    expires_at,
                    utc_now_iso(),
                    state["id"],
                    company_id,
                    actor_user_id,
                ),
            )

            conn.commit()

        return self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

    def release(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        actor_user_id: int,
        force: bool = False,
    ) -> dict[str, Any]:
        """Release a human conversation back to the shared employee queue.

        This does not hand the conversation back to AI immediately — it
        starts the same takeover-expiry timer used elsewhere (see
        expire_overdue_takeovers, which already runs every 10s in the
        background). If no employee takes the conversation within that
        window, it returns to AI automatically. If an employee takes it
        over first, take-over resets this timer as usual.
        """
        state = self.get_state(company_id, channel, external_user_id)
        release_expiry = (
            utc_now() + timedelta(minutes=_takeover_timeout_minutes(company_id))
        ).isoformat()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if force:
                cursor = conn.execute(
                    """
                    UPDATE conversations
                    SET assigned_user_id = NULL,
                        handled_by_ai = 0,
                        ai_enabled = 0,
                        status = 'waiting_agent',
                        workflow_state = 'waiting_agent',
                        needs_human = 1,
                        takeover_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND company_id = ?
                      AND handled_by_ai = 0
                      AND ai_enabled = 0
                    """,
                    (release_expiry, utc_now_iso(), state["id"], company_id),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE conversations
                    SET assigned_user_id = NULL,
                        handled_by_ai = 0,
                        ai_enabled = 0,
                        status = 'waiting_agent',
                        workflow_state = 'waiting_agent',
                        needs_human = 1,
                        takeover_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND company_id = ?
                      AND handled_by_ai = 0
                      AND ai_enabled = 0
                      AND assigned_user_id = ?
                    """,
                    (release_expiry, utc_now_iso(), state["id"], company_id, actor_user_id),
                )
            if cursor.rowcount != 1:
                current = conn.execute(
                    "SELECT assigned_user_id FROM conversations WHERE id = ?",
                    (state["id"],),
                ).fetchone()
                conn.rollback()
                raise ConversationOwnershipConflict(
                    int(current["assigned_user_id"])
                    if current and current["assigned_user_id"] is not None
                    else None
                )
            self.insert_event(
                conn=conn,
                conversation_id=int(state["id"]),
                company_id=company_id,
                actor_user_id=actor_user_id,
                event_type="conversation_released",
                data={"from_user_id": actor_user_id},
            )
            conn.commit()
        return self.get_state(company_id, channel, external_user_id)

    def record_employee_reply(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        actor_user_id: int,
        message_preview: str,
    ) -> dict[str, Any]:
        """Extend the existing lock after a successful employee reply."""
        state = self.get_state(company_id, channel, external_user_id)
        now = utc_now_iso()
        next_expiry = (
            utc_now() + timedelta(minutes=_takeover_timeout_minutes(company_id))
        ).isoformat()

        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"""
                UPDATE conversations
                SET status = {_status_preserving_manual('human_handling')},
                    workflow_state = 'human_active',
                    needs_human = 1,
                    human_last_reply_at = ?,
                    takeover_expires_at = ?,
                    last_message_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                  AND handled_by_ai = 0
                  AND ai_enabled = 0
                  AND assigned_user_id = ?
                """,
                (now, next_expiry, now, now, state["id"], company_id, actor_user_id),
            )
            if cursor.rowcount != 1:
                current = conn.execute(
                    "SELECT assigned_user_id FROM conversations WHERE id = ?",
                    (state["id"],),
                ).fetchone()
                conn.rollback()
                raise ConversationOwnershipConflict(
                    int(current["assigned_user_id"])
                    if current and current["assigned_user_id"] is not None
                    else None
                )
            self.insert_event(
                conn=conn,
                conversation_id=int(state["id"]),
                company_id=company_id,
                actor_user_id=actor_user_id,
                event_type="employee_replied",
                data={
                    "message_preview": message_preview[:200],
                    "next_timeout_at": next_expiry,
                },
            )
            conn.commit()
        return self.get_state(company_id, channel, external_user_id)

    def seconds_until_ai_return(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
    ) -> float | None:
        state = self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        if bool(state.get("handled_by_ai", True)):
            return 0.0
        expiry = parse_datetime(state.get("takeover_expires_at"))
        if expiry is None:
            return None
        return max(0.0, (expiry - utc_now()).total_seconds())

    def mark_ai_processing(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
    ) -> dict[str, Any]:
        state = self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET workflow_state = 'ai_processing',
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                  AND handled_by_ai = 1
                  AND ai_enabled = 1
                """,
                (utc_now_iso(), state["id"], company_id),
            )
            conn.commit()
        return self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

    def record_ai_reply(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        message_count: int,
        delay_seconds: int,
    ) -> dict[str, Any]:
        state = self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        with db.connect() as conn:
            conn.execute(
                f"""
                UPDATE conversations
                SET status = {_status_preserving_manual('ai_handling')},
                    workflow_state = 'ai_active',
                    last_message_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                """,
                (utc_now_iso(), utc_now_iso(), state["id"], company_id),
            )
            self.insert_event(
                conn=conn,
                conversation_id=int(state["id"]),
                company_id=company_id,
                actor_user_id=None,
                event_type="ai_replied",
                data={
                    "message_count": int(message_count),
                    "batched": int(message_count) > 1,
                    "delay_seconds": int(delay_seconds),
                },
            )
            conn.commit()
        return self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

    def mark_ai_ready_after_error(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
    ) -> dict[str, Any]:
        state = self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET workflow_state =
                        CASE
                            WHEN handled_by_ai = 1 AND ai_enabled = 1
                                THEN 'waiting_ai'
                            ELSE 'waiting_human'
                        END,
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                """,
                (utc_now_iso(), state["id"], company_id),
            )
            conn.commit()
        return self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

    def update_state(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        actor_user_id: int,
        status: str | None = None,
        priority: str | None = None,
        department: str | None = None,
        assigned_user_id: int | None = None,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        state = self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=(
                external_user_id
            ),
        )

        if (
            status is not None
            and status
            not in VALID_STATUSES
        ):
            raise ValueError(
                "Invalid conversation status."
            )

        if (
            priority is not None
            and priority
            not in VALID_PRIORITIES
        ):
            raise ValueError(
                "Invalid conversation priority."
            )

        requested_changes = {
            "status": status,
            "priority": priority,
            "department": department,
            "assigned_user_id":
                assigned_user_id,
        }

        actual_changes: list[
            tuple[str, Any, Any]
        ] = []

        for (
            field_name,
            new_value,
        ) in requested_changes.items():
            if new_value is None:
                continue

            old_value = state.get(
                field_name
            )

            if old_value == new_value:
                continue

            actual_changes.append(
                (
                    field_name,
                    old_value,
                    new_value,
                )
            )

        if not actual_changes:
            return state

        event_names = {
            "status":
                "status_changed",
            "priority":
                "priority_changed",
            "department":
                "department_changed",
            "assigned_user_id":
                "assignment_changed",
        }

        with db.connect() as conn:
            # SECURITY FIX (Patch 9.1): all writes to this conversation row
            # now happen inside a single BEGIN IMMEDIATE transaction, same
            # locking discipline as set_ai_mode()/take_over(), so a second
            # request cannot interleave and silently steal ownership.
            conn.execute("BEGIN IMMEDIATE")

            if "assigned_user_id" in {c[0] for c in actual_changes}:
                # Re-read the row *inside* the lock — the `state` snapshot
                # taken before the transaction can be stale.
                fresh = conn.execute(
                    """
                    SELECT * FROM conversations
                    WHERE id = ? AND company_id = ?
                    LIMIT 1
                    """,
                    (state["id"], company_id),
                ).fetchone()
                if fresh is None:
                    conn.rollback()
                    raise ValueError("Conversation not found.")

                fresh_state = self.row_to_dict(fresh)
                current_owner = fresh_state.get("assigned_user_id")
                new_owner = next(
                    v for f, _, v in actual_changes if f == "assigned_user_id"
                )
                is_reassign_away_from_someone_else = (
                    current_owner is not None
                    and int(current_owner) != int(actor_user_id)
                    and int(current_owner) != int(new_owner)
                )
                is_self_claim_conflict = (
                    not is_admin
                    and current_owner is not None
                    and int(current_owner) != int(actor_user_id)
                )
                if is_self_claim_conflict:
                    conn.rollback()
                    raise ConversationOwnershipConflict(
                        int(current_owner) if current_owner is not None else None
                    )
                # Admin overriding another employee's ownership: allowed,
                # but recorded distinctly so it is visible in the Timeline
                # instead of looking like a silent/normal reassignment.
                admin_override = is_admin and is_reassign_away_from_someone_else

            for (
                field_name,
                _,
                new_value,
            ) in actual_changes:
                if field_name == "assigned_user_id":
                    expires_at = (
                        utc_now()
                        + timedelta(minutes=_takeover_timeout_minutes(company_id))
                    ).isoformat()
                    conn.execute(
                        f"""
                        UPDATE conversations
                        SET assigned_user_id = ?,
                            handled_by_ai = 0,
                            ai_enabled = 0,
                            status = {_status_preserving_manual('human_handling')},
                            workflow_state = 'human_active',
                            needs_human = 1,
                            takeover_expires_at = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND company_id = ?
                        """,
                        (
                            new_value,
                            expires_at,
                            utc_now_iso(),
                            state["id"],
                            company_id,
                        ),
                    )
                else:
                    conn.execute(
                        f"""
                        UPDATE conversations
                        SET
                            {field_name} = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND company_id = ?
                        """,
                        (
                            new_value,
                            utc_now_iso(),
                            state["id"],
                            company_id,
                        ),
                    )

            for (
                field_name,
                old_value,
                new_value,
            ) in actual_changes:
                event_type = event_names[field_name]
                event_data = {
                    "field": field_name,
                    "from": old_value,
                    "to": new_value,
                }
                if field_name == "assigned_user_id" and admin_override:
                    event_type = "admin_override_assignment"
                    event_data["overridden_owner_id"] = old_value
                self.insert_event(
                    conn=conn,
                    conversation_id=(
                        state["id"]
                    ),
                    company_id=company_id,
                    actor_user_id=(
                        actor_user_id
                    ),
                    event_type=event_type,
                    data=event_data,
                )

            conn.commit()

        status_change = next((c for c in actual_changes if c[0] == "status"), None)
        if status_change is not None and status_change[2] == "closed":
            department_change = next((c for c in actual_changes if c[0] == "department"), None)
            fresh_department = department_change[2] if department_change is not None else state.get("department")
            _fire_conversation_closed_trigger(
                company_id=company_id, channel=channel, external_user_id=external_user_id,
                department=fresh_department,
            )

        return self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=(
                external_user_id
            ),
        )


    def update_workspace_state(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        actor_user_id: int,
        *,
        customer_alias: str | None = None,
        folder: str | None = None,
        is_starred: bool | None = None,
        is_pinned: bool | None = None,
        tags: list[str] | None = None,
        clear_assignment: bool = False,
        is_unread: bool | None = None,
    ) -> dict[str, Any]:
        state = self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

        valid_folders = {
            "inbox",
            "done",
            "archived",
        }

        if folder is not None and folder not in valid_folders:
            raise ValueError("Invalid conversation folder.")

        updates: list[tuple[str, Any, Any, str]] = []

        if customer_alias is not None:
            clean_alias = customer_alias.strip() or None
            if clean_alias != state.get("customer_alias"):
                updates.append((
                    "customer_alias",
                    state.get("customer_alias"),
                    clean_alias,
                    "customer_alias_changed",
                ))

        if folder is not None and folder != state.get("folder", "inbox"):
            updates.append((
                "folder",
                state.get("folder", "inbox"),
                folder,
                "folder_changed",
            ))

        if is_starred is not None and bool(is_starred) != bool(state.get("is_starred")):
            updates.append((
                "is_starred",
                bool(state.get("is_starred")),
                bool(is_starred),
                "conversation_starred" if is_starred else "conversation_unstarred",
            ))

        if is_pinned is not None and bool(is_pinned) != bool(state.get("is_pinned")):
            updates.append((
                "is_pinned",
                bool(state.get("is_pinned")),
                bool(is_pinned),
                "conversation_pinned" if is_pinned else "conversation_unpinned",
            ))

        normalized_tags: list[str] | None = None
        if tags is not None:
            normalized_tags = []
            seen: set[str] = set()
            for item in tags:
                clean = str(item).strip()
                key = clean.casefold()
                if not clean or key in seen:
                    continue
                seen.add(key)
                normalized_tags.append(clean[:50])

            old_tags = state.get("tags", [])
            if normalized_tags != old_tags:
                updates.append((
                    "tags_json",
                    old_tags,
                    normalized_tags,
                    "tags_changed",
                ))

        if is_unread is not None:
            old_unread = int(state.get("unread_count", 0) or 0) > 0
            if bool(is_unread) != old_unread:
                updates.append((
                    "unread_count",
                    int(state.get("unread_count", 0) or 0),
                    1 if is_unread else 0,
                    "conversation_marked_unread" if is_unread else "conversation_marked_read",
                ))

        if clear_assignment and state.get("assigned_user_id") is not None:
            updates.append((
                "assigned_user_id",
                state.get("assigned_user_id"),
                None,
                "assignment_changed",
            ))

        if not updates:
            return state

        with db.connect() as conn:
            for field_name, old_value, new_value, event_type in updates:
                stored_value = new_value
                if field_name in {"is_starred", "is_pinned"}:
                    stored_value = 1 if new_value else 0
                elif field_name == "tags_json":
                    stored_value = json.dumps(
                        new_value,
                        ensure_ascii=False,
                    )

                conn.execute(
                    f"""
                    UPDATE conversations
                    SET {field_name} = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND company_id = ?
                    """,
                    (
                        stored_value,
                        utc_now_iso(),
                        state["id"],
                        company_id,
                    ),
                )

                self.insert_event(
                    conn=conn,
                    conversation_id=state["id"],
                    company_id=company_id,
                    actor_user_id=actor_user_id,
                    event_type=event_type,
                    data={
                        "field": field_name,
                        "from": old_value,
                        "to": new_value,
                    },
                )

            conn.commit()

        folder_change = next((u for u in updates if u[0] == "folder"), None)
        if folder_change is not None and folder_change[2] == "done":
            _fire_conversation_closed_trigger(
                company_id=company_id, channel=channel, external_user_id=external_user_id,
                department=state.get("department"),
            )

        return self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )


    def record_customer_message(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        official_customer_name: str | None = None,
        customer_profile_picture: str | None = None,
    ) -> dict[str, Any]:
        state = self.get_or_create(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

        old_folder = state.get("folder", "inbox")
        old_unread = int(state.get("unread_count", 0) or 0)
        now = utc_now_iso()

        with db.connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET unread_count = ?,
                    folder = 'inbox',
                    workflow_state =
                        CASE
                            WHEN handled_by_ai = 1 AND ai_enabled = 1
                                THEN 'waiting_ai'
                            ELSE 'waiting_human'
                        END,
                    official_customer_name = COALESCE(?, official_customer_name),
                    customer_profile_picture = COALESCE(?, customer_profile_picture),
                    last_message_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND company_id = ?
                """,
                (
                    old_unread + 1,
                    (official_customer_name or "").strip() or None,
                    (customer_profile_picture or "").strip() or None,
                    now,
                    now,
                    state["id"],
                    company_id,
                ),
            )

            # Automatic inbox/unread transitions are state updates, not
            # human-facing timeline events. They remain visible in diagnostics.

            conn.commit()

        return self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=external_user_id,
        )

    def add_note(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
        author_user_id: int,
        note: str,
        mentioned_user_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        clean_note = note.strip()

        if not clean_note:
            raise ValueError(
                "Note cannot be empty."
            )

        state = self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=(
                external_user_id
            ),
        )

        with db.connect() as conn:
            valid_mentions: list[int] = []
            if mentioned_user_ids:
                placeholders = ",".join("?" for _ in mentioned_user_ids)
                mention_rows = conn.execute(
                    f"""
                    SELECT users.id FROM users
                    JOIN company_users ON company_users.user_id = users.id
                    WHERE company_users.company_id = ? AND company_users.status = 'active'
                      AND users.id IN ({placeholders})
                    """,
                    (company_id, *mentioned_user_ids),
                ).fetchall()
                valid_mentions = [int(row["id"]) for row in mention_rows]

            cursor = conn.execute(
                """
                INSERT INTO conversation_notes (
                    conversation_id,
                    company_id,
                    author_user_id,
                    note,
                    mentioned_user_ids_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state["id"],
                    company_id,
                    author_user_id,
                    clean_note,
                    json.dumps(valid_mentions),
                    utc_now_iso(),
                ),
            )

            note_id = cursor.lastrowid

            self.insert_event(
                conn=conn,
                conversation_id=(
                    state["id"]
                ),
                company_id=company_id,
                actor_user_id=(
                    author_user_id
                ),
                event_type=(
                    "internal_note_added"
                ),
                data={
                    "note_id": note_id,
                },
            )

            conn.commit()

            row = conn.execute(
                """
                SELECT
                    conversation_notes.*,
                    COALESCE(
                        users.full_name,
                        users.email,
                        'Unknown user'
                    ) AS author_name
                FROM conversation_notes
                LEFT JOIN users
                    ON users.id =
                       conversation_notes.author_user_id
                WHERE conversation_notes.id = ?
                """,
                (note_id,),
            ).fetchone()

        note_dict = self._decode_note(row)

        if valid_mentions:
            from backend.services.notification_service import notification_service
            author_name = note_dict.get("author_name") or "A colleague"
            for mentioned_user_id in valid_mentions:
                if mentioned_user_id == author_user_id:
                    continue
                notification_service.create(
                    company_id=company_id,
                    notification_type="conversation_mention",
                    title=f"{author_name} mentioned you in a note",
                    body=clean_note,
                    recipient_user_id=mentioned_user_id,
                    channel=channel,
                    external_user_id=external_user_id,
                    conversation_id=state["id"],
                    actor_user_id=author_user_id,
                    severity="info",
                    data={"note_id": note_id},
                )

        return note_dict

    @staticmethod
    def _decode_note(row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["mentioned_user_ids"] = json.loads(item.get("mentioned_user_ids_json") or "[]")
        except (TypeError, ValueError):
            item["mentioned_user_ids"] = []
        return item

    def list_tags(self, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, color, status, created_at, updated_at
                FROM conversation_tags
                WHERE company_id = ? AND status = 'active'
                ORDER BY name COLLATE NOCASE ASC
                """,
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_tag(
        self,
        company_id: int,
        name: str,
        actor_user_id: int | None,
        color: str | None = None,
    ) -> dict[str, Any]:
        clean = str(name or "").strip()[:50]
        if not clean:
            raise ValueError("Tag name is required.")
        normalized = clean.casefold()
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_tags (
                    company_id, name, normalized_name, color, status,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(company_id, normalized_name)
                DO UPDATE SET status = 'active', updated_at = excluded.updated_at
                """,
                (company_id, clean, normalized, color, actor_user_id, now, now),
            )
            row = conn.execute(
                """SELECT id, name, color, status, created_at, updated_at
                   FROM conversation_tags
                   WHERE company_id = ? AND normalized_name = ?""",
                (company_id, normalized),
            ).fetchone()
            conn.commit()
        return dict(row)

    def update_tag(
        self, company_id: int, tag_id: int, name: str, color: str | None = None
    ) -> dict[str, Any]:
        clean = str(name or "").strip()[:50]
        if not clean:
            raise ValueError("Tag name is required.")
        with db.connect() as conn:
            conn.execute(
                """UPDATE conversation_tags
                   SET name = ?, normalized_name = ?, color = ?, updated_at = ?
                   WHERE id = ? AND company_id = ?""",
                (clean, clean.casefold(), color, utc_now_iso(), tag_id, company_id),
            )
            row = conn.execute(
                "SELECT id, name, color, status, created_at, updated_at FROM conversation_tags WHERE id = ? AND company_id = ?",
                (tag_id, company_id),
            ).fetchone()
            conn.commit()
        if not row:
            raise ValueError("Tag not found.")
        return dict(row)

    @staticmethod
    def serialize_event(
        row,
    ) -> dict[str, Any]:
        event = dict(row)

        raw_data = event.pop(
            "event_data_json",
            None,
        )

        try:
            event["data"] = json.loads(
                raw_data or "{}"
            )

        except (
            json.JSONDecodeError,
            TypeError,
        ):
            event["data"] = {}

        event["actor_name"] = (
            event.get("actor_name")
            or "System"
        )

        return event

    def timeline(
        self,
        company_id: int,
        channel: str,
        external_user_id: str,
    ) -> dict[str, Any]:
        state = self.get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=(
                external_user_id
            ),
        )

        with db.connect() as conn:
            event_rows = conn.execute(
                """
                SELECT
                    conversation_events.*,
                    COALESCE(
                        users.full_name,
                        users.email,
                        'System'
                    ) AS actor_name
                FROM conversation_events
                LEFT JOIN users
                    ON users.id =
                       conversation_events.actor_user_id
                WHERE conversation_id = ?
                  AND conversation_events.company_id = ?
                ORDER BY conversation_events.id DESC
                LIMIT 100
                """,
                (
                    state["id"],
                    company_id,
                ),
            ).fetchall()

            note_rows = conn.execute(
                """
                SELECT
                    conversation_notes.*,
                    COALESCE(
                        users.full_name,
                        users.email,
                        'Unknown user'
                    ) AS author_name
                FROM conversation_notes
                LEFT JOIN users
                    ON users.id =
                       conversation_notes.author_user_id
                WHERE conversation_id = ?
                  AND conversation_notes.company_id = ?
                ORDER BY conversation_notes.id DESC
                LIMIT 100
                """,
                (
                    state["id"],
                    company_id,
                ),
            ).fetchall()

        meaningful_types = {
            "status_changed",
            "priority_changed",
            "department_changed",
            "assignment_changed",
            "customer_alias_changed",
            "folder_changed",
            "conversation_starred",
            "conversation_unstarred",
            "tags_changed",
            "human_takeover",
            "conversation_released",
            "conversation_read",
            "conversation_viewed",
            "conversation_marked_read",
            "conversation_marked_unread",
            "returned_to_ai",
            "automatically_returned_to_ai",
            "employee_replied",
            "ai_replied",
            "internal_note_added",
            "admin_override_assignment",
        }

        events: list[dict[str, Any]] = []
        previous_fingerprint: tuple[str, str] | None = None
        previous_created_at: datetime | None = None

        for row in event_rows:
            event = self.serialize_event(row)
            event_type = str(event.get("event_type") or "")
            data = event.get("data", {})

            if event_type not in meaningful_types:
                continue

            # New-message housekeeping belongs to diagnostics, not the audit timeline.
            if event_type == "folder_changed" and data.get("reason") == "new_customer_message":
                continue

            fingerprint = (
                event_type,
                json.dumps(data, ensure_ascii=False, sort_keys=True),
            )
            created_at = parse_datetime(event.get("created_at"))

            # Collapse only immediately repeated events. Do not erase legitimate
            # repeated actions that happened later in the conversation.
            if (
                fingerprint == previous_fingerprint
                and created_at is not None
                and previous_created_at is not None
                and abs((previous_created_at - created_at).total_seconds()) < 10
            ):
                continue

            events.append(event)
            previous_fingerprint = fingerprint
            previous_created_at = created_at

        return {
            "conversation": state,
            "events": events,
            "notes": [self._decode_note(row) for row in note_rows],
        }


conversation_control_service = (
    ConversationControlService()
)
