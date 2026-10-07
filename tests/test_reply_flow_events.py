"""`appointment_created`, `appointment_completed`, `call_logged`,
`task_completed`, `conversation_closed`: Reply Flow triggers fired the
moment the event they are named for actually happens.

Each test below calls the real owning service (`appointment_service.create`,
`conversation_control_service.update_state`, ...) exactly as its own route
does, and asserts a flow actually starts -- not that some internal function
was called, which would prove the plumbing exists without proving it is
connected to anything real.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


def _wire(platform, monkeypatch):
    import backend.services.appointment_service  # noqa: F401
    import backend.services.call_log_service  # noqa: F401
    import backend.services.conversation_control_service  # noqa: F401
    import backend.services.message_service  # noqa: F401
    import backend.services.reply_flow_event_service  # noqa: F401
    import backend.services.reply_flow_service  # noqa: F401
    import backend.services.ticket_service  # noqa: F401
    import core.reply_flow_engine  # noqa: F401
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    import channels.sender as sender_module

    sent = {}

    def fake_send_text(*, channel, recipient_id, company_id, text, buttons=None):
        sent["channel"] = channel
        sent["recipient_id"] = recipient_id
        sent["text"] = text
        return {"ok": True}

    monkeypatch.setattr(sender_module, "send_text", fake_send_text)

    import channels.inbound as inbound

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    return test_manager, sent


def _node(node_id, node_type, config=None):
    return {
        "id": node_id,
        "type": "step",
        "position": {"x": 0, "y": 0},
        "data": {"nodeType": node_type, "label": "", "config": config or {}},
    }


def _make_flow(company_id, *, trigger_type):
    from backend.services.reply_flow_service import reply_flow_service

    flow = reply_flow_service.create(company_id=company_id, name="Event flow")
    return reply_flow_service.update(
        company_id=company_id, flow_id=flow["id"], name="Event flow",
        status="active", channels=[], departments=[], reply_modes=[],
        trigger_type=trigger_type, trigger_config={},
        nodes=[_node("g", "greeting", {"text": f"Fired: {trigger_type}"})],
        edges=[],
    )


def _customer_with_conversation(company_id, *, user_id="cust-event"):
    """A real customer, identity and conversation, the same way a genuine
    inbound message creates them -- not hand-rolled SQL."""
    from channels.inbound import process_inbound_event
    from database.manager import database_manager

    process_inbound_event(
        company_id=company_id,
        event={
            "channel": "messenger", "user_id": user_id, "text": "hi",
            "message_id": f"mid-{user_id}",
        },
    )
    with database_manager.tenant(company_id) as conn:
        row = conn.execute(
            "SELECT customer_id FROM conversations WHERE company_id = ? "
            "AND channel = 'messenger' AND external_user_id = ?",
            (company_id, user_id),
        ).fetchone()
    return row["customer_id"]


# --------------------------------------------------------- conversation_closed


def test_conversation_closed_fires_on_a_real_close(platform, alpha, monkeypatch):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="conversation_closed")
    _customer_with_conversation(alpha["id"])

    conversation_control_service.update_state(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-event",
        actor_user_id=1, status="closed",
    )

    assert sent.get("text") == "Fired: conversation_closed"


def test_other_status_changes_do_not_fire_conversation_closed(
    platform, alpha, monkeypatch
):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="conversation_closed")
    _customer_with_conversation(alpha["id"])

    conversation_control_service.update_state(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-event",
        actor_user_id=1, status="resolved",
    )

    assert sent == {}


# ------------------------------------------------------------ appointment_*


@pytest.fixture()
def staffed(platform):
    """One real employee every appointment test books, the same fixture
    shape `test_appointments.py` already establishes this needs."""
    from database.manager import utc_now_iso

    now = utc_now_iso()
    staff_id = 9001

    with platform["manager"].control() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, full_name, "
            "status, created_at, updated_at) VALUES (?, ?, 'x', 'Staff', "
            "'active', ?, ?)",
            (staff_id, "staff-event@example.com", now, now),
        )
        for company in platform["companies"].values():
            role = conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = 'agent'",
                (company["id"],),
            ).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO company_users (company_id, user_id, "
                "role_id, status, created_at) VALUES (?, ?, ?, 'active', ?)",
                (company["id"], staff_id, int(role["id"]), now),
            )
        conn.commit()

    return staff_id


def _in_hours(hours: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def test_appointment_created_fires_for_a_known_customer(
    platform, alpha, staffed, monkeypatch
):
    from backend.services.appointment_service import appointment_service

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="appointment_created")
    customer_id = _customer_with_conversation(alpha["id"])

    appointment_service.create(
        company_id=alpha["id"], staff_user_id=staffed,
        starts_at=_in_hours(5), ends_at=_in_hours(6), title="Consult",
        customer_id=customer_id,
    )

    assert sent.get("text") == "Fired: appointment_created"
    assert sent.get("recipient_id") == "cust-event"


def test_appointment_created_with_no_customer_id_does_nothing(
    platform, alpha, staffed, monkeypatch
):
    from backend.services.appointment_service import appointment_service

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="appointment_created")

    appointment_service.create(
        company_id=alpha["id"], staff_user_id=staffed,
        starts_at=_in_hours(5), ends_at=_in_hours(6), title="Walk-in",
    )

    assert sent == {}


def test_appointment_completed_fires_only_on_the_transition(
    platform, alpha, staffed, monkeypatch
):
    from backend.services.appointment_service import appointment_service

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="appointment_completed")
    customer_id = _customer_with_conversation(alpha["id"])

    appt = appointment_service.create(
        company_id=alpha["id"], staff_user_id=staffed,
        starts_at=_in_hours(1), ends_at=_in_hours(2), title="Consult",
        customer_id=customer_id,
    )
    assert sent == {}, "appointment_completed must not fire on creation"

    appointment_service.set_status(
        company_id=alpha["id"], appointment_id=appt["id"], status="confirmed",
    )
    assert sent == {}, "appointment_completed must not fire on an unrelated status"

    appointment_service.set_status(
        company_id=alpha["id"], appointment_id=appt["id"], status="completed",
    )
    assert sent.get("text") == "Fired: appointment_completed"

    sent.clear()
    appointment_service.set_status(
        company_id=alpha["id"], appointment_id=appt["id"], status="completed",
    )
    assert sent == {}, "re-setting the same status must not re-fire"


# ---------------------------------------------------------------- call_logged


def test_call_logged_fires_for_a_known_customer(platform, alpha, monkeypatch):
    from backend.services.call_log_service import call_log_service

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="call_logged")
    customer_id = _customer_with_conversation(alpha["id"])

    call_log_service.create_call_log(
        company_id=alpha["id"], direction="inbound", customer_id=customer_id,
    )

    assert sent.get("text") == "Fired: call_logged"


def test_call_logged_with_only_a_phone_number_does_nothing(
    platform, alpha, monkeypatch
):
    """No customer record behind a bare phone number -- nothing to resolve
    a channel from."""
    from backend.services.call_log_service import call_log_service

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="call_logged")

    call_log_service.create_call_log(
        company_id=alpha["id"], direction="inbound", phone_number="+1 555 0100",
    )

    assert sent == {}


# -------------------------------------------------------------- task_completed


def test_task_completed_fires_for_a_task_linked_to_a_conversation(
    platform, alpha, monkeypatch
):
    from backend.services.ticket_service import ticket_service

    _, sent = _wire(platform, monkeypatch)
    from database.manager import database_manager

    _make_flow(alpha["id"], trigger_type="task_completed")
    _customer_with_conversation(alpha["id"])

    with database_manager.tenant(alpha["id"]) as conn:
        conversation_id = conn.execute(
            "SELECT id FROM conversations WHERE company_id = ? "
            "AND external_user_id = 'cust-event'",
            (alpha["id"],),
        ).fetchone()["id"]

    task = ticket_service.create_task(
        company_id=alpha["id"],
        data={"title": "Follow up", "conversation_id": conversation_id},
    )
    assert sent == {}, "task_completed must not fire on creation"

    ticket_service.change_status(
        company_id=alpha["id"], task_id=task["id"], status="resolved",
    )

    assert sent.get("text") == "Fired: task_completed"


def test_a_task_with_no_conversation_does_nothing(platform, alpha, monkeypatch):
    from backend.services.ticket_service import ticket_service

    _, sent = _wire(platform, monkeypatch)
    _make_flow(alpha["id"], trigger_type="task_completed")

    task = ticket_service.create_task(
        company_id=alpha["id"], data={"title": "Standalone task"},
    )
    ticket_service.change_status(
        company_id=alpha["id"], task_id=task["id"], status="resolved",
    )

    assert sent == {}
