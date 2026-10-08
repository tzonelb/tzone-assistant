"""`appointment_reminder`: start a Reply Flow ahead of a scheduled appointment.

The builder lets a company configure how many minutes before the appointment
the reminder should fire (`minutes_before`), per flow. That rules out
`work_index_service`'s indexed-deadline model the way `customer_no_reply`/
`team_no_reply` already ruled it out (see `reply_flow_silence_service`'s own
docstring): "the next due moment" depends on which flow's own threshold
applies, and a company can run more than one `appointment_reminder` flow
scoped to different channels/departments with different thresholds. So this
is a plain periodic sweep too (`backend.workers.reply_flow_appointment_
reminder_worker`), matched to the one flow that actually applies to each
appointment's own resolved channel/department via `reply_flow_service
.active_flow_for` -- the same lookup the live message path uses -- rather
than reimplementing its scope-matching rules here.

An appointment's `starts_at` never moves once booked, unlike a conversation's
`last_message_at`, so a reminder is a true one-shot:
`reply_flow_appointment_reminders_fired` is a flat per-appointment flag, not
a value compared against something that can change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.company_gate import company_gate
from backend.services.subscription_gate import subscription_gate
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)

TRIGGER_TYPE = "appointment_reminder"

# An appointment still ahead of the business, worth reminding anyone about.
_UPCOMING_STATUS = ("scheduled", "confirmed")

# Mirrors the builder's own placeholder for this trigger's one config field
# (reply_flow_service.TRIGGER_TYPES) -- a flow saved before the field was
# filled in gets the same default a blank form would have suggested, rather
# than never firing at all.
_DEFAULT_MINUTES = 60.0


def _minutes_before(flow: dict[str, Any]) -> float:
    config = flow.get("trigger_config") or {}
    try:
        minutes = float(config.get("minutes_before"))
    except (TypeError, ValueError):
        return _DEFAULT_MINUTES
    return minutes if minutes > 0 else _DEFAULT_MINUTES


def _already_fired(conn, appointment_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM reply_flow_appointment_reminders_fired
        WHERE appointment_id = ?
        """,
        (int(appointment_id),),
    ).fetchone()
    return row is not None


def _record_fired(conn, *, company_id: int, appointment_id: int) -> None:
    conn.execute(
        """
        INSERT INTO reply_flow_appointment_reminders_fired (
            company_id, appointment_id, created_at
        )
        VALUES (?, ?, ?)
        ON CONFLICT(appointment_id) DO NOTHING
        """,
        (int(company_id), int(appointment_id), utc_now_iso()),
    )


def fire_due(company_id: int, now: str | None = None) -> int:
    """Start an `appointment_reminder` flow for every upcoming appointment
    that has entered its own flow's reminder window.

    Gated the same as every other worker that can put a message in front of
    a customer on this company's behalf.
    """
    if subscription_gate.lapsed(company_id) or company_gate.suspended(company_id):
        return 0

    # Imported here, not at module scope: both are already module-scope
    # importers of `core.reply_flow_engine`, so an import up there would be
    # circular the same way `reply_flow_resume_service`'s is.
    from backend.services.reply_flow_service import reply_flow_service
    from core.reply_flow_engine import reply_flow_engine

    flows = reply_flow_service.active_flows_for_triggers(company_id, (TRIGGER_TYPE,))
    if not flows:
        # The common case -- most companies configure no reminder flow at
        # all -- costs one cheap read of this company's own flow list and
        # nothing more.
        return 0

    moment = (
        datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    widest_window = max(_minutes_before(flow) for flow in flows)
    horizon = (moment + timedelta(minutes=widest_window)).isoformat()

    placeholders = ",".join("?" * len(_UPCOMING_STATUS))
    with database_manager.tenant(int(company_id)) as conn:
        appointments = conn.execute(
            f"""
            SELECT id, customer_id, starts_at
            FROM appointments
            WHERE company_id = ?
              AND status IN ({placeholders})
              AND customer_id IS NOT NULL
              AND starts_at > ?
              AND starts_at <= ?
            ORDER BY starts_at
            """,
            (int(company_id), *_UPCOMING_STATUS, moment.isoformat(), horizon),
        ).fetchall()

    if not appointments:
        return 0

    from backend.services.reply_flow_event_service import resolve_channel_for_customer

    fired = 0

    for row in appointments:
        try:
            starts_at = datetime.fromisoformat(
                str(row["starts_at"]).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=timezone.utc)

        resolved = resolve_channel_for_customer(company_id, row["customer_id"])
        if not resolved:
            continue
        channel, external_user_id, department = resolved

        flow = reply_flow_service.active_flow_for(
            company_id, channel=channel, department=department,
            trigger_type=TRIGGER_TYPE,
        )
        if not flow:
            continue

        if starts_at - moment > timedelta(minutes=_minutes_before(flow)):
            continue

        with database_manager.tenant(int(company_id)) as conn:
            if _already_fired(conn, row["id"]):
                continue

        # Checked, not assumed: a conversation another flow already occupies
        # must stay un-recorded, or this reminder would never be reconsidered
        # on a later sweep still inside its own window.
        if reply_flow_engine.has_live_run(
            company_id=company_id, channel=channel, external_user_id=external_user_id,
        ):
            continue

        with database_manager.tenant(int(company_id)) as conn:
            _record_fired(conn, company_id=company_id, appointment_id=row["id"])
            conn.commit()

        try:
            started = reply_flow_engine.start_for_trigger(
                company_id=company_id, channel=channel,
                external_user_id=external_user_id, flow=flow,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not start an appointment_reminder reply flow for "
                "company %s, appointment %s",
                company_id, row["id"],
            )
            continue

        if started:
            fired += 1

    return fired
