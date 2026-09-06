"""Follow-ups an employee sets on a conversation.

Three decisions are worth pinning here.

A reminder can carry a message the platform sends to the customer, so setting
one is `conversations.reply` rather than `conversations.view`: it is a reply
scheduled instead of typed, and someone who may only read the inbox must not be
able to schedule one.

A time in the past is refused instead of firing at once. It is almost always a
timezone mistake, and the failure mode -- a message arriving at the customer the
instant somebody mis-typed a date -- is not one to discover in production.

There is one reminder per conversation. Setting a second replaces the first,
because that is what "remind me at" means to the person clicking it; the
alternative is a pile of forgotten reminders nobody can see or cancel.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest


PASSWORD = "AgentPass123456"


def _in_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.conversations  # noqa: F401
    import backend.services.conversation_reminder_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.conversation_reminder_service" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def reminders(service):
    from backend.services.conversation_reminder_service import (
        conversation_reminder_service,
    )

    return conversation_reminder_service


# --------------------------------------------------------------- the service


def test_a_reminder_is_stored_and_read_back(reminders, alpha):
    when = _in_hours(3)
    saved = reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=when,
        note="Chase the quote",
        created_by_user_id=5,
    )

    assert saved["note"] == "Chase the quote"
    assert saved["auto_send"] == 0

    found = reminders.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    )
    assert found["remind_at"] == saved["remind_at"]


def test_setting_a_second_reminder_replaces_the_first(reminders, alpha):
    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(3),
        note="First",
    )
    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(5),
        note="Second",
    )

    due = reminders.due(company_id=alpha["id"], now=_in_hours(9))
    assert len(due) == 1, "a conversation ended up with two live reminders"
    assert due[0]["note"] == "Second"


def test_a_time_in_the_past_is_refused(reminders, alpha):
    from backend.services.conversation_reminder_service import ReminderError

    with pytest.raises(ReminderError):
        reminders.set(
            company_id=alpha["id"],
            channel="messenger",
            external_user_id="cust-1",
            remind_at=_in_hours(-1),
        )


def test_a_promise_to_send_needs_something_to_send(reminders, alpha):
    """auto_send with no message would leave the sweep unable to act and the
    employee believing a message will go out."""
    from backend.services.conversation_reminder_service import ReminderError

    with pytest.raises(ReminderError):
        reminders.set(
            company_id=alpha["id"],
            channel="messenger",
            external_user_id="cust-1",
            remind_at=_in_hours(2),
            auto_send=True,
        )


def test_only_reminders_whose_time_has_come_are_due(reminders, alpha):
    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="soon",
        remind_at=_in_hours(1),
    )
    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="later",
        remind_at=_in_hours(10),
    )

    due = reminders.due(company_id=alpha["id"], now=_in_hours(2))
    assert [row["external_user_id"] for row in due] == ["soon"]


def test_clearing_says_whether_there_was_one(reminders, alpha):
    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        remind_at=_in_hours(3),
    )

    assert reminders.clear(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is True
    assert reminders.clear(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is False


def test_one_companys_reminders_never_reach_another(reminders, alpha, beta):
    """The same channel and customer id exist in both companies and mean
    different people. Each database answers only for its own."""
    reminders.set(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="shared-id",
        remind_at=_in_hours(3),
        note="Alpha's customer",
    )

    assert reminders.get(
        company_id=beta["id"], channel="messenger", external_user_id="shared-id"
    ) is None
    assert reminders.due(company_id=beta["id"], now=_in_hours(9)) == []
