"""`appointment_created`, `appointment_completed`, `call_logged`,
`task_completed`, `conversation_closed`: start a Reply Flow the moment
something happens, not on a timer or a sweep.

Every one of these is anchored to a real event a service already records --
an appointment is booked, a call is logged, a task is marked done, a
conversation is closed -- so there is nothing to schedule and nothing to
sweep: the owning service calls `fire` (or `fire_for_conversation`, when it
already has the channel in hand) once, right after its own write commits.

The one real complication none of the other triggers share: an appointment,
a call and a task are each about a *customer* (`customer_id`), not about a
*conversation* on a specific channel the way `conversations` rows are. A
customer can hold more than one channel identity (`customer_identities`),
and nothing in this platform already picks one when more than one exists.
`_resolve_channel` makes that call: the channel of the customer's own most
recently active conversation, falling back to their most recently touched
identity when they have identities but no conversation yet. Documented here
because it is a real judgement call, not a fact read off a column.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services import module_access
from database.manager import database_manager


logger = logging.getLogger(__name__)


def _resolve_channel(company_id: int, customer_id: int) -> tuple[str, str, str] | None:
    """(channel, external_user_id, department) for a customer, or None if
    they hold no channel identity at all -- a customer entered by hand with
    only a phone number and no messaging identity, for instance."""
    with database_manager.tenant(int(company_id)) as conn:
        conversation = conn.execute(
            """
            SELECT channel, external_user_id, department FROM conversations
            WHERE company_id = ? AND customer_id = ?
            ORDER BY last_message_at DESC
            LIMIT 1
            """,
            (int(company_id), int(customer_id)),
        ).fetchone()

        if conversation:
            return (
                conversation["channel"], conversation["external_user_id"],
                conversation["department"] or "",
            )

        identity = conn.execute(
            """
            SELECT channel, external_user_id FROM customer_identities
            WHERE company_id = ? AND customer_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (int(company_id), int(customer_id)),
        ).fetchone()

    if identity:
        return identity["channel"], identity["external_user_id"], ""
    return None


def fire_for_task(
    *, company_id: int, conversation_id: int | None, trigger_type: str
) -> None:
    """The entry point for `task_completed`: a task carries no `customer_id`
    at all (see this module's own docstring on the judgement call the other
    two triggers need; a task needs a different one) -- `conversation_id` is
    the only reliable link back to a channel, and only set at all for a task
    created from a conversation in the first place (`TaskCreate`'s own
    schema). A task typed up from the tasks screen directly, with no
    conversation behind it, has nothing for this trigger to fire into.
    """
    if not conversation_id:
        return

    try:
        if not module_access.module_enabled(int(company_id), "ai_teaching"):
            return
    except Exception:  # noqa: BLE001
        return

    with database_manager.tenant(int(company_id)) as conn:
        conversation = conn.execute(
            "SELECT channel, external_user_id, department FROM conversations "
            "WHERE id = ? AND company_id = ? LIMIT 1",
            (int(conversation_id), int(company_id)),
        ).fetchone()

    if not conversation:
        return

    fire_for_conversation(
        company_id=company_id, channel=conversation["channel"],
        external_user_id=conversation["external_user_id"],
        department=conversation["department"] or "", trigger_type=trigger_type,
    )


def fire_for_customer(
    *, company_id: int, customer_id: int | None, trigger_type: str
) -> None:
    """The entry point for a customer-scoped event: an appointment, a call,
    a task. Resolves the channel itself; does nothing (no flow lookup, no
    database touch beyond the resolve) when `customer_id` is absent, since
    that is the ordinary shape of an appointment booked for a walk-in or a
    call logged against a bare phone number -- not every one of these events
    has a customer record behind it at all.
    """
    if not customer_id:
        return

    try:
        if not module_access.module_enabled(int(company_id), "ai_teaching"):
            return
    except Exception:  # noqa: BLE001
        return

    resolved = _resolve_channel(company_id, customer_id)
    if not resolved:
        return

    channel, external_user_id, department = resolved
    fire_for_conversation(
        company_id=company_id, channel=channel, external_user_id=external_user_id,
        department=department, trigger_type=trigger_type,
    )


def fire_for_conversation(
    *,
    company_id: int,
    channel: str,
    external_user_id: str,
    department: str,
    trigger_type: str,
) -> None:
    """The entry point for a conversation-scoped event (`conversation_closed`)
    -- the channel is already known, so this skips straight to matching a
    flow and starting it.

    Never raises: every caller is a service committing its own, unrelated
    write (an appointment, a call, a closed conversation), and a reply-flow
    failure must not turn into a failed appointment booking.
    """
    try:
        if not module_access.module_enabled(int(company_id), "ai_teaching"):
            return
    except Exception:  # noqa: BLE001
        return

    from backend.services.reply_flow_service import reply_flow_service
    from core.reply_flow_engine import reply_flow_engine

    try:
        flow = reply_flow_service.active_flow_for(
            int(company_id), channel=channel, department=department,
            trigger_type=trigger_type,
        )
        if not flow:
            return

        reply_flow_engine.start_for_trigger(
            company_id=company_id, channel=channel,
            external_user_id=external_user_id, flow=flow,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not start a %s reply flow for company %s",
            trigger_type,
            company_id,
        )
