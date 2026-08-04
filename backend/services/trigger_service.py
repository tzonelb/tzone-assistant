"""Bot Triggers ("مفعّلات البوت"): company-configurable rules that make
the bot act on real platform events -- a new customer conversation, an
appointment being booked/finished, a task completing, a call being
logged, a customer going silent, the team not replying, an upcoming
appointment reminder.

Two kinds of trigger types:

- EVENT types fire inline, at the moment the event happens, from small
  hooks inside the owning service (conversation_control_service,
  appointment_service, task_service, call_log_service). Hooks call
  fire_event() wrapped so a trigger failure can never break the host
  operation.
- TIME types are evaluated by a background worker (main.py's
  bot_triggers_worker, every 60s) via run_time_checks(), using each
  trigger's delay_minutes.

When a trigger fires it always records a row in bot_trigger_firings
(with a UNIQUE (company_id, dedupe_key) so the same occurrence never
fires twice), optionally creates an internal team notification, and --
for conversation-context triggers on channels with an outbound sender
(messenger/instagram/whatsapp) -- optionally auto-sends the trigger's
message_text to the customer. Telegram has no standalone outbound
sender in this codebase, so telegram conversations get the notification
but no auto-message."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# type -> (kind, needs delay_minutes, human label)
TRIGGER_TYPES: dict[str, dict[str, Any]] = {
    "new_conversation": {
        "kind": "event",
        "needs_delay": False,
        "label": "New conversation started",
    },
    "appointment_booked": {
        "kind": "event",
        "needs_delay": False,
        "label": "Appointment booked",
    },
    "appointment_completed": {
        "kind": "event",
        "needs_delay": False,
        "label": "Appointment completed (follow-up)",
    },
    "task_completed": {
        "kind": "event",
        "needs_delay": False,
        "label": "Task completed",
    },
    "call_logged": {
        "kind": "event",
        "needs_delay": False,
        "label": "Call logged",
    },
    "customer_no_reply": {
        "kind": "time",
        "needs_delay": True,
        "label": "Customer went silent after our reply",
    },
    "team_no_reply": {
        "kind": "time",
        "needs_delay": True,
        "label": "Team has not replied to a waiting customer",
    },
    "appointment_reminder": {
        "kind": "time",
        "needs_delay": True,
        "label": "Appointment reminder (before start)",
    },
}

SENDABLE_CHANNELS = {"messenger", "instagram", "whatsapp"}


class TriggerValidationError(ValueError):
    """Raised for invalid values: unknown trigger_type, a time-based
    trigger without delay_minutes, or an empty name."""


class TriggerService:
    EDITABLE_FIELDS = {
        "name",
        "trigger_type",
        "enabled",
        "delay_minutes",
        "channel",
        "message_text",
        "notify_team",
    }

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _validate(self, values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in values.items():
            if key not in self.EDITABLE_FIELDS:
                continue
            if key in ("name", "channel", "message_text"):
                cleaned[key] = self._clean_text(value)
            elif key == "trigger_type":
                trigger_type = (self._clean_text(value) or "").lower()
                if trigger_type not in TRIGGER_TYPES:
                    raise TriggerValidationError(
                        f"trigger_type must be one of {sorted(TRIGGER_TYPES)}"
                    )
                cleaned[key] = trigger_type
            elif key in ("enabled", "notify_team"):
                cleaned[key] = 1 if value else 0
            elif key == "delay_minutes":
                if value is None or value == "":
                    cleaned[key] = None
                    continue
                try:
                    delay = int(value)
                except (TypeError, ValueError) as exc:
                    raise TriggerValidationError(
                        "delay_minutes must be a whole number"
                    ) from exc
                if delay < 1:
                    raise TriggerValidationError(
                        "delay_minutes must be at least 1"
                    )
                cleaned[key] = delay

        if not partial:
            if not cleaned.get("name"):
                raise TriggerValidationError("name is required")
            if "trigger_type" not in cleaned:
                raise TriggerValidationError("trigger_type is required")

        return cleaned

    @staticmethod
    def _check_delay_requirement(trigger_type: str, delay_minutes: int | None) -> None:
        if TRIGGER_TYPES[trigger_type]["needs_delay"] and not delay_minutes:
            raise TriggerValidationError(
                f"'{trigger_type}' is time-based and requires delay_minutes."
            )

    def list_triggers(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.*,
                    (
                        SELECT COUNT(*) FROM bot_trigger_firings f
                        WHERE f.trigger_id = t.id AND f.company_id = t.company_id
                    ) AS firing_count
                FROM bot_triggers t
                WHERE t.company_id = ?
                ORDER BY t.enabled DESC, t.name COLLATE NOCASE ASC
                """,
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_trigger(self, *, company_id: int, trigger_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM bot_triggers WHERE id = ? AND company_id = ? LIMIT 1",
                (trigger_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Trigger not found")
        return dict(row)

    def create_trigger(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        cleaned = self._validate(values, partial=False)
        trigger_type = cleaned["trigger_type"]
        self._check_delay_requirement(trigger_type, cleaned.get("delay_minutes"))

        now = utc_now_iso()

        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO bot_triggers (
                    company_id, name, trigger_type, enabled, delay_minutes,
                    channel, message_text, notify_team, created_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    cleaned["name"],
                    trigger_type,
                    cleaned.get("enabled", 1),
                    cleaned.get("delay_minutes"),
                    cleaned.get("channel"),
                    cleaned.get("message_text"),
                    cleaned.get("notify_team", 1),
                    actor_user_id,
                    now,
                    now,
                ),
            )
            trigger_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_trigger(company_id=company_id, trigger_id=trigger_id)

    def update_trigger(
        self,
        *,
        company_id: int,
        trigger_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        cleaned = self._validate(values, partial=True)
        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM bot_triggers WHERE id = ? AND company_id = ?",
                (trigger_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Trigger not found")

            if "name" in cleaned and not cleaned["name"]:
                raise TriggerValidationError("name cannot be empty")

            merged_type = cleaned.get("trigger_type", existing["trigger_type"])
            merged_delay = (
                cleaned["delay_minutes"]
                if "delay_minutes" in cleaned
                else existing["delay_minutes"]
            )
            self._check_delay_requirement(merged_type, merged_delay)

            if not cleaned:
                return dict(existing)

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE bot_triggers SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, trigger_id, company_id],
            )
            conn.commit()

        return self.get_trigger(company_id=company_id, trigger_id=trigger_id)

    def delete_trigger(self, *, company_id: int, trigger_id: int) -> bool:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM bot_triggers WHERE id = ? AND company_id = ?",
                (trigger_id, company_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_firings(
        self,
        *,
        company_id: int,
        trigger_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["f.company_id = ?"]
        params: list[Any] = [company_id]

        if trigger_id is not None:
            where.append("f.trigger_id = ?")
            params.append(trigger_id)

        clause = " AND ".join(where)

        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM bot_trigger_firings f WHERE {clause}",
                params,
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                SELECT f.*, t.name AS trigger_name
                FROM bot_trigger_firings f
                LEFT JOIN bot_triggers t ON t.id = f.trigger_id
                WHERE {clause}
                ORDER BY f.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    # ------------------------------------------------------------------
    # Firing
    # ------------------------------------------------------------------

    def _matching_triggers(
        self, conn, company_id: int, trigger_type: str, channel: str | None
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM bot_triggers
            WHERE company_id = ? AND trigger_type = ? AND enabled = 1
            """,
            (company_id, trigger_type),
        ).fetchall()
        matched = []
        for row in rows:
            trigger = dict(row)
            # A trigger with a channel restriction only fires for that
            # channel; channel=None means "all channels" (and events with
            # no channel context, e.g. task_completed, only match
            # unrestricted triggers).
            if trigger["channel"] and trigger["channel"] != (channel or ""):
                continue
            matched.append(trigger)
        return matched

    def _try_send_to_customer(
        self,
        trigger: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Attempt the auto-message. Returns (action, detail)."""
        message = trigger.get("message_text")
        channel = (context.get("channel") or "").lower()
        external_user_id = context.get("external_user_id")

        if not message or not external_user_id or channel not in SENDABLE_CHANNELS:
            return "notification", "no auto-message (no text, no recipient, or unsendable channel)"

        try:
            if channel == "whatsapp":
                from channels.whatsapp.sender import send_whatsapp_text

                result = send_whatsapp_text(to=external_user_id, text=message)
            else:
                from channels.meta.sender import send_meta_text

                result = send_meta_text(
                    recipient_id=external_user_id,
                    text=message,
                    channel=channel,
                    company_id=context.get("company_id"),
                )
            if result and result.get("ok"):
                return "message_sent", f"auto-message sent via {channel}"
            return (
                "send_failed",
                f"send via {channel} failed: {json.dumps(result, ensure_ascii=False)[:300]}",
            )
        except Exception as exc:  # never let a sender error propagate
            return "send_failed", f"send via {channel} raised: {exc}"

    def _record_firing(
        self,
        conn,
        *,
        company_id: int,
        trigger: dict[str, Any],
        dedupe_key: str,
        context: dict[str, Any],
        action: str,
        detail: str,
    ) -> bool:
        """INSERT OR IGNORE against the UNIQUE (company_id, dedupe_key)
        index -- returns False when this occurrence already fired."""
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO bot_trigger_firings (
                company_id, trigger_id, trigger_type, dedupe_key,
                conversation_id, customer_id, reference_id,
                action_taken, detail, fired_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                trigger["id"],
                trigger["trigger_type"],
                dedupe_key,
                context.get("conversation_id"),
                context.get("customer_id"),
                context.get("reference_id"),
                action,
                detail,
                utc_now_iso(),
            ),
        )
        return cursor.rowcount > 0

    def _notify_team(
        self,
        trigger: dict[str, Any],
        company_id: int,
        context: dict[str, Any],
        action: str,
    ) -> None:
        if not trigger.get("notify_team"):
            return
        try:
            from backend.services.notification_service import notification_service

            notification_service.create(
                company_id=company_id,
                notification_type="bot_trigger",
                title=f"Trigger fired: {trigger['name']}",
                body=context.get("summary") or TRIGGER_TYPES[trigger["trigger_type"]]["label"],
                channel=context.get("channel"),
                external_user_id=context.get("external_user_id"),
                conversation_id=context.get("conversation_id"),
                severity="info",
                data={
                    "trigger_id": trigger["id"],
                    "trigger_type": trigger["trigger_type"],
                    "action_taken": action,
                    "reference_id": context.get("reference_id"),
                },
                dedupe_key=f"bot_trigger:{trigger['id']}:{context.get('dedupe_suffix', '')}",
            )
        except Exception as exc:  # notifications must never break the host op
            print("TRIGGER NOTIFY ERROR:", exc)

    def fire_event(
        self,
        *,
        company_id: int,
        trigger_type: str,
        dedupe_suffix: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Fire all matching enabled triggers for an event occurrence.

        `dedupe_suffix` must uniquely identify THIS occurrence (e.g.
        "conv:42", "appt:7:completed") -- combined with the trigger id it
        forms the dedupe key so one occurrence fires each trigger at most
        once, ever. Never raises: every failure is swallowed (and
        printed) so the host operation is untouched."""
        try:
            context = dict(context or {})
            context["company_id"] = company_id
            context["dedupe_suffix"] = dedupe_suffix

            with db.connect() as conn:
                triggers = self._matching_triggers(
                    conn, company_id, trigger_type, context.get("channel")
                )

            for trigger in triggers:
                dedupe_key = f"{trigger['id']}:{dedupe_suffix}"

                action, detail = self._try_send_to_customer(trigger, context)

                with db.connect() as conn:
                    recorded = self._record_firing(
                        conn,
                        company_id=company_id,
                        trigger=trigger,
                        dedupe_key=dedupe_key,
                        context=context,
                        action=action,
                        detail=detail,
                    )
                    conn.commit()

                if recorded:
                    self._notify_team(trigger, company_id, context, action)
        except Exception as exc:
            print("TRIGGER FIRE ERROR:", trigger_type, exc)

    def _fire_time_trigger_occurrence(
        self,
        trigger: dict[str, Any],
        company_id: int,
        dedupe_suffix: str,
        context: dict[str, Any],
    ) -> None:
        """Like fire_event but for one already-matched time trigger.
        Checks the dedupe BEFORE attempting any send so the periodic
        worker doesn't re-send on every tick."""
        dedupe_key = f"{trigger['id']}:{dedupe_suffix}"
        context = dict(context)
        context["company_id"] = company_id
        context["dedupe_suffix"] = dedupe_suffix

        with db.connect() as conn:
            already = conn.execute(
                "SELECT 1 FROM bot_trigger_firings "
                "WHERE company_id = ? AND dedupe_key = ? LIMIT 1",
                (company_id, dedupe_key),
            ).fetchone()
        if already:
            return

        action, detail = self._try_send_to_customer(trigger, context)

        with db.connect() as conn:
            recorded = self._record_firing(
                conn,
                company_id=company_id,
                trigger=trigger,
                dedupe_key=dedupe_key,
                context=context,
                action=action,
                detail=detail,
            )
            conn.commit()

        if recorded:
            self._notify_team(trigger, company_id, context, action)

    def run_time_checks(self) -> None:
        """Evaluate all enabled time-based triggers across all companies.
        Called by main.py's background worker every 60s. Never raises."""
        try:
            with db.connect() as conn:
                triggers = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT * FROM bot_triggers
                        WHERE enabled = 1
                          AND trigger_type IN (
                              'customer_no_reply', 'team_no_reply',
                              'appointment_reminder'
                          )
                          AND delay_minutes IS NOT NULL
                        """
                    ).fetchall()
                ]

            for trigger in triggers:
                try:
                    self._run_one_time_trigger(trigger)
                except Exception as exc:
                    print("TRIGGER TIME CHECK ERROR:", trigger["id"], exc)
        except Exception as exc:
            print("TRIGGER TIME SWEEP ERROR:", exc)

    def _run_one_time_trigger(self, trigger: dict[str, Any]) -> None:
        company_id = trigger["company_id"]
        delay = int(trigger["delay_minutes"])
        trigger_type = trigger["trigger_type"]

        if trigger_type == "team_no_reply":
            # A customer is waiting on a human and nobody has replied for
            # `delay` minutes (last_message_at is set on customer
            # messages; workflow_state 'waiting_human' means the ball is
            # in the team's court).
            with db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, channel, external_user_id, customer_id,
                           last_message_at
                    FROM conversations
                    WHERE company_id = ?
                      AND workflow_state = 'waiting_human'
                      AND last_message_at IS NOT NULL
                      AND datetime(last_message_at)
                          <= datetime('now', ?)
                    LIMIT 200
                    """,
                    (company_id, f"-{delay} minutes"),
                ).fetchall()
            for row in rows:
                if trigger["channel"] and trigger["channel"] != row["channel"]:
                    continue
                self._fire_time_trigger_occurrence(
                    trigger,
                    company_id,
                    f"team_no_reply:conv:{row['id']}:{row['last_message_at']}",
                    {
                        "conversation_id": row["id"],
                        "customer_id": row["customer_id"],
                        "channel": row["channel"],
                        "external_user_id": row["external_user_id"],
                        "summary": (
                            f"Customer waiting {delay}+ minutes with no team reply "
                            f"({row['channel']})"
                        ),
                    },
                )

        elif trigger_type == "customer_no_reply":
            # We (bot or team) acted last and the customer has been silent
            # since: no unread customer messages, and no activity on the
            # conversation for `delay` minutes.
            with db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, channel, external_user_id, customer_id,
                           updated_at
                    FROM conversations
                    WHERE company_id = ?
                      AND status NOT IN ('closed', 'archived')
                      AND COALESCE(unread_count, 0) = 0
                      AND updated_at IS NOT NULL
                      AND datetime(updated_at) <= datetime('now', ?)
                      AND datetime(updated_at) >= datetime('now', '-7 days')
                    LIMIT 200
                    """,
                    (company_id, f"-{delay} minutes"),
                ).fetchall()
            for row in rows:
                if trigger["channel"] and trigger["channel"] != row["channel"]:
                    continue
                self._fire_time_trigger_occurrence(
                    trigger,
                    company_id,
                    f"customer_no_reply:conv:{row['id']}:{row['updated_at']}",
                    {
                        "conversation_id": row["id"],
                        "customer_id": row["customer_id"],
                        "channel": row["channel"],
                        "external_user_id": row["external_user_id"],
                        "summary": (
                            f"Customer silent for {delay}+ minutes after our last "
                            f"reply ({row['channel']})"
                        ),
                    },
                )

        elif trigger_type == "appointment_reminder":
            # An appointment starts within the next `delay` minutes.
            with db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, customer_id, starts_at
                    FROM appointments
                    WHERE company_id = ?
                      AND status = 'scheduled'
                      AND datetime(starts_at) > datetime('now')
                      AND datetime(starts_at) <= datetime('now', ?)
                    LIMIT 200
                    """,
                    (company_id, f"+{delay} minutes"),
                ).fetchall()
            for row in rows:
                self._fire_time_trigger_occurrence(
                    trigger,
                    company_id,
                    f"appointment_reminder:appt:{row['id']}",
                    {
                        "customer_id": row["customer_id"],
                        "reference_id": row["id"],
                        "summary": (
                            f"Appointment '{row['title']}' starts at {row['starts_at']}"
                        ),
                    },
                )


trigger_service = TriggerService()
