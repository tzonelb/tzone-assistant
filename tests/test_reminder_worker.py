"""The reminders worker.

`conversation_reminder_service.due()` existed since the feature shipped, but
nothing in production ever called it: an employee could set a reminder, see it
saved, and it would simply never fire -- the same "a feature that saves and
decides nothing" shape this audit found repeatedly. This file is the
regression test for wiring it up.

Three things `fire_due` has to get right, each pinned by its own test:

* an `auto_send` reminder sends the pre-written message, puts it on the
  conversation's own Timeline attributed to whoever set it, and tells them it
  went out;
* a plain reminder sends nothing and only tells the employee who set it to
  come back to the conversation;
* it is gated exactly like `publish_due_posts` and `process_due_replies`: a
  suspended or lapsed company gets neither the message nor the notification,
  and the reminder is left queued rather than lost.

And `set()`'s side of the wiring: registering the deadline in the work index
so the sweep's indexed lookup (`work_index_service.due_companies`) actually
finds the company, which is what makes any of the above run on a timer at all.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest


def _in_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.conversations  # noqa: F401
    import backend.services.conversation_reminder_service  # noqa: F401
    import backend.workers  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.conversation_reminder_service" in rebound
    assert "backend.services.work_index_service" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def reminders(service):
    from backend.services.conversation_reminder_service import (
        conversation_reminder_service,
    )

    return conversation_reminder_service


@pytest.fixture()
def notifications():
    from backend.services.notification_service import notification_service

    return notification_service


# ------------------------------------------------------------- the work index


def test_setting_a_reminder_registers_it_in_the_work_index(reminders, alpha):
    from backend.services.work_index_service import KIND_REMINDER, work_index_service

    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(3),
        created_by_user_id=5,
    )

    due_companies = work_index_service.due_companies(KIND_REMINDER, now=_in_hours(9))
    assert alpha["id"] in due_companies


def test_a_company_with_no_reminders_is_not_in_the_index(reminders, alpha):
    from backend.services.work_index_service import KIND_REMINDER, work_index_service

    due_companies = work_index_service.due_companies(KIND_REMINDER, now=_in_hours(9))
    assert alpha["id"] not in due_companies


# ------------------------------------------------------------------ firing


def test_an_auto_send_reminder_sends_and_logs_the_message(
    reminders, notifications, alpha, monkeypatch
):
    import backend.services.conversation_reminder_service as reminder_module

    sent = {}

    def fake_send_text(*, channel, recipient_id, company_id, text, buttons=None):
        sent["channel"] = channel
        sent["recipient_id"] = recipient_id
        sent["text"] = text
        return {"ok": True, "response": {"message_id": "wamid.1"}}

    monkeypatch.setattr(reminder_module, "send_text", fake_send_text)

    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(1),
        note="Chase the quote",
        auto_send=True,
        message_text="Following up on your quote!",
        created_by_user_id=7,
    )

    fired = reminders.fire_due(alpha["id"], now=_in_hours(2))

    assert fired == 1
    assert sent == {
        "channel": "messenger",
        "recipient_id": "cust-1",
        "text": "Following up on your quote!",
    }

    # The reminder fires once, like an alarm, not a recurring timer.
    assert reminders.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is None

    from database.manager import database_manager

    with database_manager.tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE channel = ? AND external_user_id = ?"
            " ORDER BY id DESC LIMIT 1",
            ("messenger", "cust-1"),
        ).fetchone()

    assert row["direction"] == "out"
    assert row["body"] == "Following up on your quote!"
    assert row["sender_type"] == "employee"
    assert row["sender_user_id"] == 7

    items = notifications.list_for_user(
        company_id=alpha["id"], user_id=7, notification_type="conversation_reminder"
    )
    assert len(items) == 1
    assert "sent" in items[0]["title"].lower()


def test_a_plain_reminder_sends_nothing_and_only_notifies(
    reminders, notifications, alpha, monkeypatch
):
    import backend.services.conversation_reminder_service as reminder_module

    def fail_if_called(*args, **kwargs):
        raise AssertionError("a plain reminder must never send a message")

    monkeypatch.setattr(reminder_module, "send_text", fail_if_called)

    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(1),
        note="Call back at four",
        created_by_user_id=9,
    )

    fired = reminders.fire_due(alpha["id"], now=_in_hours(2))

    assert fired == 1

    items = notifications.list_for_user(
        company_id=alpha["id"], user_id=9, notification_type="conversation_reminder"
    )
    assert len(items) == 1
    assert items[0]["body"] == "Call back at four"


def test_a_reminder_not_yet_due_does_not_fire(reminders, alpha, monkeypatch):
    import backend.services.conversation_reminder_service as reminder_module

    monkeypatch.setattr(
        reminder_module,
        "send_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )

    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(10),
        created_by_user_id=9,
    )

    assert reminders.fire_due(alpha["id"]) == 0
    assert reminders.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is not None


def test_a_failed_send_still_notifies_and_clears(reminders, notifications, alpha, monkeypatch):
    import backend.services.conversation_reminder_service as reminder_module

    monkeypatch.setattr(
        reminder_module,
        "send_text",
        lambda **kwargs: {"ok": False, "reason": "missing_credentials"},
    )

    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(1),
        auto_send=True,
        message_text="Hi again!",
        created_by_user_id=11,
    )

    fired = reminders.fire_due(alpha["id"], now=_in_hours(2))

    assert fired == 1
    assert reminders.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is None

    items = notifications.list_for_user(
        company_id=alpha["id"], user_id=11, notification_type="conversation_reminder"
    )
    assert len(items) == 1
    assert "failed" in items[0]["title"].lower()


# --------------------------------------------------------------------- gating


def test_a_suspended_company_gets_no_reminder_fired(reminders, alpha, monkeypatch):
    from backend.services.company_gate import company_gate
    import backend.services.conversation_reminder_service as reminder_module

    monkeypatch.setattr(
        reminder_module,
        "send_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )

    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(1),
        created_by_user_id=9,
    )

    monkeypatch.setattr(company_gate, "suspended", lambda company_id: True)

    assert reminders.fire_due(alpha["id"], now=_in_hours(2)) == 0

    # Nothing claimed: the reminder is still there for when the company is
    # reinstated, the same as a queued post or reply.
    assert reminders.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is not None


def test_a_lapsed_company_gets_no_reminder_fired(reminders, alpha, monkeypatch):
    from backend.services.subscription_gate import subscription_gate
    import backend.services.conversation_reminder_service as reminder_module

    monkeypatch.setattr(
        reminder_module,
        "send_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )

    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(1),
        created_by_user_id=9,
    )

    monkeypatch.setattr(subscription_gate, "lapsed", lambda company_id: True)

    assert reminders.fire_due(alpha["id"], now=_in_hours(2)) == 0
    assert reminders.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is not None


def test_reminder_worker_is_wired_into_the_app(reminders):
    """Not just built -- actually scheduled, or this is the same dead feature
    with extra steps."""
    import main

    assert "reminder_worker" in main.__dict__
