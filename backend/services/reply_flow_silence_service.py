"""`customer_no_reply` / `team_no_reply`: start a Reply Flow on silence, not
on a message.

Every other trigger this platform fires is anchored to something that
happened -- a message arrived, a wait the engine itself scheduled elapsed. A
silence trigger has no anchor: "the customer has not replied for 30 minutes"
stays true for every minute after the 30th, for a conversation that might sit
quiet for a year. Two things follow from that, and both shape this file:

* There is no single deadline to register the way
  `reply_flow_resume_service` registers one in `work_index_service` --
  `work_index_service`'s whole model is "the next moment something is due",
  and a silence condition that is already true and stays true has no next
  moment. So this is swept on a plain timer instead
  (`backend.workers.reply_flow_silence_worker`), the same shape
  `email_poll_worker` sweeps every connected mailbox on a timer rather than
  waiting for a scheduled deadline, for the same reason: nothing registers
  in advance when a condition becomes true on its own.
* A condition that stays true must only fire once, not once per sweep --
  `reply_flow_silence_fired` is that memory, keyed to the exact
  `last_message_at` that earned the fire. The flow's own proactive message is
  itself an outbound message, which moves `last_message_at` forward the
  moment it sends, so a conversation that goes quiet again after that
  naturally becomes eligible again on its own; nothing here has to reset it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.company_gate import company_gate
from backend.services.subscription_gate import subscription_gate
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)

TRIGGER_TYPES = ("customer_no_reply", "team_no_reply")

# Mirrors the builder's own placeholder for each trigger's one config field
# (reply_flow_service.TRIGGER_TYPES) -- a flow saved before the field was
# filled in gets the same default a blank form would have suggested, rather
# than never firing at all.
_DEFAULT_MINUTES = 60.0


def _threshold_minutes(flow: dict[str, Any]) -> float:
    config = flow.get("trigger_config") or {}
    key = (
        "minutes_of_silence"
        if flow.get("trigger_type") == "customer_no_reply"
        else "minutes_waiting"
    )
    try:
        minutes = float(config.get(key))
    except (TypeError, ValueError):
        return _DEFAULT_MINUTES
    return minutes if minutes > 0 else _DEFAULT_MINUTES


def _flow_matches_scope(flow: dict[str, Any], channel: str, department: str) -> bool:
    """Same rule `reply_flow_service.active_flow_for` applies: an empty
    scope matches everything, a non-empty one must contain this value."""
    channels = [c.lower() for c in (flow.get("channels") or [])]
    departments = [d.lower() for d in (flow.get("departments") or [])]

    if channels and str(channel or "").strip().lower() not in channels:
        return False
    if departments and str(department or "").strip().lower() not in departments:
        return False
    return True


def _already_fired(
    conn, *, channel: str, external_user_id: str, trigger_type: str, last_message_at: str
) -> bool:
    row = conn.execute(
        """
        SELECT fired_for_message_at FROM reply_flow_silence_fired
        WHERE channel = ? AND external_user_id = ? AND trigger_type = ?
        """,
        (str(channel), str(external_user_id), trigger_type),
    ).fetchone()
    return bool(row) and row["fired_for_message_at"] == last_message_at


def _record_fired(
    conn, *, company_id: int, channel: str, external_user_id: str,
    trigger_type: str, last_message_at: str,
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO reply_flow_silence_fired (
            company_id, channel, external_user_id, trigger_type,
            fired_for_message_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel, external_user_id, trigger_type) DO UPDATE SET
            fired_for_message_at = excluded.fired_for_message_at,
            created_at = excluded.created_at
        """,
        (
            int(company_id), str(channel), str(external_user_id), trigger_type,
            last_message_at, now,
        ),
    )


def fire_due(company_id: int, now: str | None = None) -> int:
    """Start a flow for every conversation a `customer_no_reply` /
    `team_no_reply` flow's own threshold says has gone quiet long enough.

    Gated the same as every other worker that can put a message in front of
    a customer on this company's behalf (see `conversation_reminder_service
    .fire_due`'s own docstring for the fuller reasoning).
    """
    if subscription_gate.lapsed(company_id) or company_gate.suspended(company_id):
        return 0

    # Imported here, not at module scope: `reply_flow_service` is already a
    # module-scope import of `core.reply_flow_engine`, so an import up there
    # would be circular the same way `reply_flow_resume_service`'s is.
    from backend.services.reply_flow_service import reply_flow_service
    from core.reply_flow_engine import reply_flow_engine

    flows = reply_flow_service.active_flows_for_triggers(company_id, TRIGGER_TYPES)
    if not flows:
        # The common case -- most companies configure no silence trigger at
        # all -- costs one cheap read of this company's own flow list and
        # nothing more.
        return 0

    moment = (
        datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    with database_manager.tenant(int(company_id)) as conn:
        conversations = conn.execute(
            """
            SELECT channel, external_user_id, department, last_message_at,
                   last_message_direction, needs_human
            FROM conversations
            WHERE company_id = ?
              AND last_message_at IS NOT NULL
              AND last_message_direction IS NOT NULL
            """,
            (int(company_id),),
        ).fetchall()

    fired = 0

    for flow in flows:
        trigger_type = flow.get("trigger_type")
        threshold = timedelta(minutes=_threshold_minutes(flow))
        want_direction = "out" if trigger_type == "customer_no_reply" else "in"

        for row in conversations:
            if row["last_message_direction"] != want_direction:
                continue
            if trigger_type == "team_no_reply" and not row["needs_human"]:
                # Waiting on a human specifically -- not every inbound
                # message left unanswered means that; the AI may simply not
                # have replied yet within the same second.
                continue
            if not _flow_matches_scope(flow, row["channel"], row["department"]):
                continue

            try:
                last_at = datetime.fromisoformat(
                    str(row["last_message_at"]).replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)

            if moment - last_at < threshold:
                continue

            with database_manager.tenant(int(company_id)) as conn:
                if _already_fired(
                    conn, channel=row["channel"],
                    external_user_id=row["external_user_id"],
                    trigger_type=trigger_type,
                    last_message_at=row["last_message_at"],
                ):
                    continue

            # Checked, not assumed: a conversation another flow already
            # occupies must stay un-recorded here, or it would never be
            # reconsidered once that flow ends -- `start_for_trigger`'s own
            # return says whether it actually attempted to start one.
            if reply_flow_engine.has_live_run(
                company_id=company_id, channel=row["channel"],
                external_user_id=row["external_user_id"],
            ):
                continue

            with database_manager.tenant(int(company_id)) as conn:
                _record_fired(
                    conn, company_id=company_id, channel=row["channel"],
                    external_user_id=row["external_user_id"],
                    trigger_type=trigger_type,
                    last_message_at=row["last_message_at"],
                )
                conn.commit()

            try:
                started = reply_flow_engine.start_for_trigger(
                    company_id=company_id,
                    channel=row["channel"],
                    external_user_id=row["external_user_id"],
                    flow=flow,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Could not start a %s reply flow for company %s",
                    trigger_type,
                    company_id,
                )
                continue

            if started:
                fired += 1

    return fired
