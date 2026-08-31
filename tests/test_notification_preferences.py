"""Three preferences that were stored in every company and read by nothing.

`new_customer_message`, `handover` and `ai_error` are
seeded into every company's database. Nothing consulted them — and two of them
were worse than unread, because the notifications they offered to switch off
did not exist:

* a human taking a conversation off the assistant was written as a conversation
  event, which is the history of one conversation, not something anybody is
  told;
* the assistant failing to answer a customer left a `diagnostic_events` row,
  which nobody watches and which is cleared after fourteen days. The team's
  first hint that the assistant was failing was a customer asking why they had
  been ignored.

So a company could turn off a bell that never rang, and turn on one that was
never wired.

The gate lives inside `notification_service.create`, keyed by notification
type, rather than at each call site. A gate that has to be remembered is a gate
the next notification forgets — which is exactly how these three came to be
offered without existing.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.services.company_settings_service  # noqa: F401
    import backend.services.notification_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.notification_service" in rebound
    assert "backend.services.company_settings_service" in rebound

    from backend.services.notification_service import notification_service

    return notification_service


def _set(company_id, **values):
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        company_id, "notifications", values, None
    )


def _count(service, company_id, notification_type=None):
    from database.manager import database_manager

    with database_manager.tenant(company_id) as conn:
        rows = conn.execute(
            "SELECT notification_type FROM notifications WHERE company_id = ?",
            (company_id,),
        ).fetchall()

    if notification_type:
        rows = [r for r in rows if r["notification_type"] == notification_type]

    return len(rows)


def _raise(service, company_id, notification_type, **extra):
    return service.create(
        company_id=company_id,
        notification_type=notification_type,
        title="Something happened",
        **extra,
    )


# --------------------------------------------------------------- the default


@pytest.mark.parametrize(
    "notification_type",
    ["customer_message", "handover", "ai_error"],
)
def test_a_company_that_changed_nothing_still_gets_every_bell(
    wired, alpha, notification_type
):
    """All three default to on. Implementing a preference must not quietly
    switch anything off for a company that never opened the screen."""
    _raise(wired, alpha["id"], notification_type)

    assert _count(wired, alpha["id"], notification_type) == 1


# ------------------------------------------------------------ each preference


@pytest.mark.parametrize(
    ("preference", "notification_type"),
    [
        ("new_customer_message", "customer_message"),
        ("handover", "handover"),
        ("ai_error", "ai_error"),
    ],
)
def test_switching_a_preference_off_stops_that_notification(
    wired, alpha, preference, notification_type
):
    _set(alpha["id"], **{preference: False})

    _raise(wired, alpha["id"], notification_type)

    assert _count(wired, alpha["id"], notification_type) == 0


@pytest.mark.parametrize(
    ("preference", "notification_type"),
    [
        ("new_customer_message", "customer_message"),
        ("handover", "handover"),
        ("ai_error", "ai_error"),
    ],
)
def test_switching_one_off_leaves_the_others_alone(
    wired, alpha, preference, notification_type
):
    """Three switches, three decisions. One that silenced all of them would
    look like it worked from any single test."""
    _set(alpha["id"], **{preference: False})

    others = [
        other
        for other in ("customer_message", "handover", "ai_error")
        if other != notification_type
    ]

    for other in others:
        _raise(wired, alpha["id"], other)

    for other in others:
        assert _count(wired, alpha["id"], other) == 1, (
            f"switching off {preference} also silenced {other}"
        )


def test_a_preference_reaches_only_the_company_that_set_it(wired, alpha, beta):
    _set(alpha["id"], ai_error=False)

    _raise(wired, alpha["id"], "ai_error")
    _raise(wired, beta["id"], "ai_error")

    assert _count(wired, alpha["id"], "ai_error") == 0
    assert _count(wired, beta["id"], "ai_error") == 1


def test_switching_one_back_on_starts_it_again(wired, alpha):
    """A one-way switch would pass every test above."""
    _set(alpha["id"], handover=False)
    _raise(wired, alpha["id"], "handover")

    _set(alpha["id"], handover=True)
    _raise(wired, alpha["id"], "handover")

    assert _count(wired, alpha["id"], "handover") == 1


# ------------------------------------------------------- what has no preference


def test_a_direct_mention_has_no_preference_and_always_arrives(wired, alpha):
    """A colleague typed somebody's name to get their attention. That is
    addressed to a person, not a category of event, and no preference offers to
    suppress it — so it must not be silenced by one that does not name it."""
    _set(
        alpha["id"],
        new_customer_message=False,
        handover=False,
        ai_error=False,
    )

    _raise(wired, alpha["id"], "team_mention")

    assert _count(wired, alpha["id"], "team_mention") == 1


# ------------------------------------------------------------- the module gate


def test_the_module_switch_still_wins(wired, alpha, monkeypatch):
    """The operator's switch is not the company's preference. A company whose
    Notifications module is off cannot open the screen these rows appear on, so
    writing them accumulates a pile nobody can clear."""
    import backend.services.module_gate as gate_module

    monkeypatch.setattr(
        gate_module.module_gate, "enabled", lambda company_id, module: False
    )

    _raise(wired, alpha["id"], "team_mention")

    assert _count(wired, alpha["id"]) == 0


def test_an_unreadable_preference_does_not_silence_the_bell(
    wired, alpha, monkeypatch
):
    """Fails open, deliberately. The alternative is a company that silently
    stops being told its assistant is failing because a database was busy."""
    import backend.services.company_settings_service as settings_module

    def _broken(*args, **kwargs):
        raise RuntimeError("the tenant database is busy")

    monkeypatch.setattr(
        settings_module.company_settings_service, "get_section", _broken
    )

    _raise(wired, alpha["id"], "ai_error")

    assert _count(wired, alpha["id"], "ai_error") == 1


# ------------------------------------------------- the two that had to be built
#
# `handover` and `ai_error` were preferences for notifications that did not
# exist. Testing the preference alone would pass with the notification still
# unwired, which is the state this whole change was correcting — so both call
# sites are asserted, not just the gate.


def test_taking_a_conversation_over_tells_the_team(wired, platform, alpha, monkeypatch):
    """A colleague taking a conversation off the assistant was written as a
    conversation event — the history of one conversation, not something anybody
    is told."""
    import backend.services.conversation_control_service as control_module

    control = control_module.conversation_control_service

    control.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    )

    control.set_ai_mode(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        handled_by_ai=False,
        actor_user_id=1,
    )

    assert _count(wired, alpha["id"], "handover") == 1


def test_handing_a_conversation_back_does_not_ring_the_bell(wired, alpha):
    """Returning a conversation to the assistant is not a hand-over. Ringing
    for both would make the bell mean "somebody touched this"."""
    from backend.services.conversation_control_service import (
        conversation_control_service as control,
    )

    control.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-2"
    )
    control.set_ai_mode(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-2",
        handled_by_ai=True,
        actor_user_id=1,
    )

    assert _count(wired, alpha["id"], "handover") == 0


def test_the_assistant_failing_tells_the_team(wired, alpha):
    """Until now the only trace was a `diagnostic_events` row, which nobody
    watches and which clears after fourteen days — so the team's first hint was
    a customer asking why they had been ignored."""
    import channels.meta.smart_reply as smart_reply

    smart_reply._notify_failure(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-3",
        error="TimeoutError",
    )

    assert _count(wired, alpha["id"], "ai_error") == 1


def test_one_outage_does_not_ring_the_bell_once_per_conversation(wired, alpha):
    """A provider outage hits every conversation at once, and a hundred
    identical entries is how a team learns to ignore the bell."""
    import channels.meta.smart_reply as smart_reply

    for _ in range(5):
        smart_reply._notify_failure(
            company_id=alpha["id"],
            channel="messenger",
            external_user_id="cust-4",
            error="TimeoutError",
        )

    assert _count(wired, alpha["id"], "ai_error") == 1


def test_the_failure_body_carries_no_exception_message(wired, alpha):
    """An exception from a provider or a database can carry a token, a
    customer's text, or a row of somebody's contact details — and this lands in
    a list the whole company can read."""
    import channels.meta.smart_reply as smart_reply
    from database.manager import database_manager

    smart_reply._notify_failure(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-5",
        error="TimeoutError",
    )

    with database_manager.tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT title, body FROM notifications WHERE notification_type = 'ai_error'"
        ).fetchone()

    assert "TimeoutError" in row["body"]
    assert "token" not in row["body"].lower()
