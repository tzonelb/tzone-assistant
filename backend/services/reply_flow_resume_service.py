"""The durable half of a Reply Flow's `timeout_followup` wait.

`core/reply_flow_engine.py` keeps a flow's place in `user_session["reply_flow"]`
-- pure in-process memory, gone on a restart (see `core/session.py`'s own
docstring). That is fine for an ordinary step, which only ever waits for the
customer's next message: if the process restarts, the flow simply loses its
place the same way the rest of the session does, and resumes from the start on
the customer's next word.

A `timeout_followup` step is different. It waits for *time*, not a message, and
the whole point is that it must still fire if nobody says anything -- including
across a restart. So the moment the engine reaches one, it writes the wait here,
to the company's own durable database, keyed the same way
`conversation_reminders` is: one live wait per conversation, a second one
replacing the first rather than queuing behind it, because a flow has one place
in its graph per conversation.

`backend/workers.py`'s `reply_flow_resume_worker` is this table's sweep, the
same shape as `reminder_worker` over `conversation_reminders`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.company_gate import company_gate
from backend.services.subscription_gate import subscription_gate
from backend.services.work_index_service import (
    KIND_REPLY_FLOW_RESUME,
    work_index_service,
)
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)

# A generous ceiling matching conversation_reminder_service's MAX_MESSAGE: the
# variables a flow collects are short answers (an order id, a name), not
# documents, so a graph that has grown a pathological amount of state is a bug
# to cap, not a size to accommodate.
MAX_VARIABLES_JSON = 20_000


def schedule(
    *,
    company_id: int,
    channel: str,
    external_user_id: str,
    flow_id: int,
    node_id: str,
    variables: dict[str, Any],
    wait_minutes: Any,
) -> None:
    """Record that this conversation's flow is waiting at `node_id`.

    A non-positive or unreadable `wait_minutes` falls back to 60 -- the
    builder's own placeholder for this field -- rather than scheduling a wait
    of zero (which would fire before the sweep could ever find it due later
    than "now") or refusing the step outright, which would stall every flow
    that reaches it.
    """
    try:
        minutes = float(wait_minutes)
    except (TypeError, ValueError):
        minutes = 60.0
    if minutes <= 0:
        minutes = 60.0

    fire_at = (
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ).isoformat()

    payload = json.dumps(variables or {})
    if len(payload) > MAX_VARIABLES_JSON:
        # The graph's own state has grown unreasonably large; better to drop
        # the wait than to fail the customer's current turn over it.
        logger.error(
            "Reply flow variables too large to schedule a timeout for "
            "company %s, channel %s",
            company_id,
            channel,
        )
        return

    now = utc_now_iso()

    with database_manager.tenant(int(company_id)) as conn:
        conn.execute(
            """
            INSERT INTO reply_flow_pending_resumes (
                company_id, channel, external_user_id, flow_id, node_id,
                variables_json, fire_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, external_user_id) DO UPDATE SET
                flow_id = excluded.flow_id,
                node_id = excluded.node_id,
                variables_json = excluded.variables_json,
                fire_at = excluded.fire_at,
                created_at = excluded.created_at
            """,
            (
                int(company_id),
                str(channel),
                str(external_user_id),
                int(flow_id),
                str(node_id),
                payload,
                fire_at,
                now,
            ),
        )

        # Before the commit, on purpose -- see work_index_service's own
        # docstring, rule 2: a control-plane failure here must abort the
        # schedule rather than commit a wait no sweep will ever be told about.
        work_index_service.note(int(company_id), KIND_REPLY_FLOW_RESUME, fire_at)

        conn.commit()


def get(*, company_id: int, channel: str, external_user_id: str) -> dict[str, Any] | None:
    """The pending wait for one conversation, whether or not it is due yet."""
    with database_manager.tenant(int(company_id)) as conn:
        row = conn.execute(
            """
            SELECT * FROM reply_flow_pending_resumes
            WHERE channel = ? AND external_user_id = ?
            LIMIT 1
            """,
            (str(channel), str(external_user_id)),
        ).fetchone()

    return dict(row) if row else None


def cancel(*, company_id: int, channel: str, external_user_id: str) -> bool:
    """Drop a pending wait -- the customer replied, or the flow moved on."""
    with database_manager.tenant(int(company_id)) as conn:
        cursor = conn.execute(
            """
            DELETE FROM reply_flow_pending_resumes
            WHERE channel = ? AND external_user_id = ?
            """,
            (str(channel), str(external_user_id)),
        )
        conn.commit()

    return bool(cursor.rowcount)


def due(*, company_id: int, now: str | None = None) -> list[dict[str, Any]]:
    """Waits whose time has arrived, oldest first."""
    moment = now or utc_now_iso()

    with database_manager.tenant(int(company_id)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM reply_flow_pending_resumes
            WHERE fire_at <= ?
            ORDER BY fire_at ASC
            """,
            (moment,),
        ).fetchall()

    return [dict(row) for row in rows]


def fire_due(company_id: int, now: str | None = None) -> int:
    """Resume every flow whose `timeout_followup` wait has elapsed.

    Gated the same as every other worker that can put a message in front of a
    customer on this company's behalf (see `conversation_reminder_service
    .fire_due`'s own docstring for the fuller reasoning). A lapsed or
    suspended company gets no proactive follow-up sent; the wait stays queued
    until the sweep runs again after the company is reinstated.
    """
    if subscription_gate.lapsed(company_id) or company_gate.suspended(company_id):
        return 0

    # Imported here, not at module scope: `reply_flow_engine` already imports
    # from `backend.services` at module scope (`reply_flow_service`), so an
    # import up there would be circular.
    from core.reply_flow_engine import reply_flow_engine

    fired = 0

    for pending in due(company_id=company_id, now=now):
        try:
            reply_flow_engine.resume_after_timeout(
                company_id=company_id,
                channel=pending["channel"],
                external_user_id=pending["external_user_id"],
                flow_id=pending["flow_id"],
                resume_node_id=pending["node_id"],
                variables=json.loads(pending["variables_json"] or "{}"),
            )
            fired += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not resume reply flow wait %s for company %s",
                pending.get("id"),
                company_id,
            )
        finally:
            # A wait fires once. Cleared even when something above raised, so
            # a transient failure cannot turn one wait into a retry storm that
            # opens this company again every sweep forever.
            cancel(
                company_id=company_id,
                channel=pending["channel"],
                external_user_id=pending["external_user_id"],
            )

    return fired
