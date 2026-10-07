"""`appointment_reminder`: starting a Reply Flow ahead of a scheduled
appointment.

Named in this platform's own catalogue (`reply_flow_service.TRIGGER_TYPES`)
and offered in the flow builder since the feature shipped, but never matched
against anything -- a company could build one of these flows, mark it
active, and it would simply never run (no sweep, no hook, zero callers
repo-wide). This file is the regression test for wiring that up: the
per-flow `minutes_before` window, the one-shot-per-appointment dedup, a
cancelled appointment being left alone, the flow's own channel scope, and
respect for an already-running flow.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from database.manager import DatabaseManager
import database.manager as manager_module


def _in_minutes(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _node(node_id, node_type, config=None):
    return {
        "id": node_id,
        "type": "step",
        "position": {"x": 0, "y": 0},
        "data": {"nodeType": node_type, "label": "", "config": config or {}},
    }


@pytest.fixture()
def wired(platform, monkeypatch):
    """Same reasoning as `test_reply_flow_silence.py`'s own fixture: each
    module here is only ever reached through a local import somewhere in the
    call chain, so it must already be in `sys.modules` before the rebind loop
    runs, or it cannot see them."""
    import backend.services.appointment_service  # noqa: F401
    import backend.services.message_service  # noqa: F401
    import backend.services.reply_flow_appointment_reminder_service  # noqa: F401
    import backend.services.reply_flow_event_service  # noqa: F401
    import backend.services.reply_flow_service  # noqa: F401
    import core.reply_flow_engine  # noqa: F401

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


@pytest.fixture()
def staffed(platform):
    """One real employee every test here books an appointment with."""
    from database.manager import utc_now_iso

    now = utc_now_iso()
    staff_id = 9101

    with platform["manager"].control() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash, full_name, "
            "status, created_at, updated_at) VALUES (?, ?, 'x', 'Staff', "
            "'active', ?, ?)",
            (staff_id, "staff-reminder@example.com", now, now),
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


def _make_flow(company_id, *, minutes_before=60, channels=None):
    from backend.services.reply_flow_service import reply_flow_service

    flow = reply_flow_service.create(company_id=company_id, name="Reminder flow")
    return reply_flow_service.update(
        company_id=company_id, flow_id=flow["id"], name="Reminder flow",
        status="active", channels=channels or [], departments=[], reply_modes=[],
        trigger_type="appointment_reminder",
        trigger_config={"minutes_before": minutes_before},
        nodes=[_node("g", "greeting", {"text": "See you soon!"})],
        edges=[],
    )


def _customer_with_conversation(company_id, *, user_id="cust-reminder"):
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


def _book(alpha, staffed, customer_id, *, starts_in_minutes, status="scheduled"):
    from backend.services.appointment_service import appointment_service

    appt = appointment_service.create(
        company_id=alpha["id"], staff_user_id=staffed,
        starts_at=_in_minutes(starts_in_minutes),
        ends_at=_in_minutes(starts_in_minutes + 30),
        title="Consult", customer_id=customer_id,
    )
    if status == "cancelled":
        appointment_service.cancel(company_id=alpha["id"], appointment_id=appt["id"])
    elif status != "scheduled":
        appointment_service.set_status(
            company_id=alpha["id"], appointment_id=appt["id"], status=status,
        )
    return appt


def test_an_appointment_inside_the_window_starts_the_flow(wired, alpha, staffed):
    from backend.services import reply_flow_appointment_reminder_service as svc

    _, sent = wired
    _make_flow(alpha["id"], minutes_before=60)
    customer_id = _customer_with_conversation(alpha["id"])
    _book(alpha, staffed, customer_id, starts_in_minutes=50)

    assert svc.fire_due(alpha["id"]) == 1
    assert sent.get("text") == "See you soon!"


def test_an_appointment_outside_the_window_is_left_alone(wired, alpha, staffed):
    from backend.services import reply_flow_appointment_reminder_service as svc

    _, sent = wired
    _make_flow(alpha["id"], minutes_before=60)
    customer_id = _customer_with_conversation(alpha["id"])
    _book(alpha, staffed, customer_id, starts_in_minutes=90)

    assert svc.fire_due(alpha["id"]) == 0
    assert sent == {}


def test_the_same_appointment_does_not_fire_twice(wired, alpha, staffed):
    from backend.services import reply_flow_appointment_reminder_service as svc

    _, sent = wired
    _make_flow(alpha["id"], minutes_before=60)
    customer_id = _customer_with_conversation(alpha["id"])
    _book(alpha, staffed, customer_id, starts_in_minutes=50)

    assert svc.fire_due(alpha["id"]) == 1
    sent.clear()
    assert svc.fire_due(alpha["id"]) == 0
    assert sent == {}


def test_a_cancelled_appointment_is_left_alone(wired, alpha, staffed):
    from backend.services import reply_flow_appointment_reminder_service as svc

    _, sent = wired
    _make_flow(alpha["id"], minutes_before=60)
    customer_id = _customer_with_conversation(alpha["id"])
    _book(alpha, staffed, customer_id, starts_in_minutes=50, status="cancelled")

    assert svc.fire_due(alpha["id"]) == 0
    assert sent == {}


def test_an_appointment_with_no_customer_is_left_alone(wired, alpha, staffed):
    from backend.services import reply_flow_appointment_reminder_service as svc
    from backend.services.appointment_service import appointment_service

    _, sent = wired
    _make_flow(alpha["id"], minutes_before=60)
    appointment_service.create(
        company_id=alpha["id"], staff_user_id=staffed,
        starts_at=_in_minutes(50), ends_at=_in_minutes(80), title="Walk-in",
    )

    assert svc.fire_due(alpha["id"]) == 0
    assert sent == {}


def test_channel_scope_is_respected(wired, alpha, staffed):
    from backend.services import reply_flow_appointment_reminder_service as svc

    _, sent = wired
    _make_flow(alpha["id"], minutes_before=60, channels=["whatsapp"])
    customer_id = _customer_with_conversation(alpha["id"])
    _book(alpha, staffed, customer_id, starts_in_minutes=50)

    assert svc.fire_due(alpha["id"]) == 0
    assert sent == {}


def test_a_flow_already_running_is_not_restarted(wired, alpha, staffed):
    from core.reply_flow_engine import reply_flow_engine
    from backend.services import reply_flow_appointment_reminder_service as svc

    _, sent = wired
    _make_flow(alpha["id"], minutes_before=60)
    customer_id = _customer_with_conversation(alpha["id"])
    _book(alpha, staffed, customer_id, starts_in_minutes=50)

    from core.session import SessionManager, session

    key = SessionManager.key("cust-reminder", "messenger", alpha["id"])
    session.create(key)["reply_flow"] = {
        "flow_id": 1, "node_id": "g", "variables": {},
    }

    assert svc.fire_due(alpha["id"]) == 0
    assert sent == {}
    assert reply_flow_engine.has_live_run(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-reminder",
    )


def test_a_company_with_no_reminder_flow_is_a_cheap_no_op(wired, alpha, staffed):
    from backend.services import reply_flow_appointment_reminder_service as svc

    _, sent = wired
    customer_id = _customer_with_conversation(alpha["id"])
    _book(alpha, staffed, customer_id, starts_in_minutes=50)

    assert svc.fire_due(alpha["id"]) == 0
    assert sent == {}
