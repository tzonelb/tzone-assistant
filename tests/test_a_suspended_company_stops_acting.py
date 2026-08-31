"""Suspension is the operator's heaviest lever, so it must do at least as much
as the lightest one.

It already worked at the front door: `set_company_status` revokes every live
session, and `auth_service` joins on `companies.status = 'active'`, so the team
is signed out and cannot sign back in.

It stopped there. A customer messaging the suspended company's page still
routed to it — `resolve_account_for_channel` filters on the channel account's
status, never on the company's — and the assistant answered. The screens went
dark and the service carried on, which is backwards: an *unpaid bill* already
stops the assistant, and `subscription_gate` says so in its own docstring —
"an operator who wants a company stopped before it is billed has suspension,
which is immediate and says so". It was neither.

The three things suspension must stop, kept separate here because getting any
one of them backwards is its own kind of harm, and the two it must not:

* the assistant answers nobody
* scheduled posts do not publish
* replies queued before the suspension are not delivered
* but the customer's message is still stored — reinstatement must find it
* and nothing is said to the customer, who is owed no explanation of their
  supplier's account status
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager, utc_now_iso


def _wire(platform, monkeypatch):
    """Point every already-imported module at this test's manager.

    By type, never by identity: a module first imported during an earlier
    monkeypatch holds that test's manager for the life of the process, and
    comparing against a single expected object silently skips it.
    """
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    return test_manager


def _suspend(platform, company_id, status="suspended"):
    from backend.services.company_gate import company_gate

    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE companies SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now_iso(), int(company_id)),
        )
        conn.commit()

    # The tests drive the column directly rather than through
    # `platform_service.set_company_status`, so they have to drop the cache the
    # same way it does. `test_suspending_takes_effect_immediately` is the one
    # that goes through the real path and checks it does this itself.
    company_gate.invalidate(company_id)


def _reinstate(platform, company_id):
    _suspend(platform, company_id, status="active")


@pytest.fixture(autouse=True)
def _clean_gate():
    """A module-level cache outlives a test. Empty it at both ends."""
    from backend.services.company_gate import company_gate

    company_gate.invalidate()
    yield
    company_gate.invalidate()


def _inbound(company_id, text="مرحبا"):
    from channels.inbound import process_inbound_event

    return process_inbound_event(
        company_id=company_id,
        event={
            "channel": "messenger",
            "user_id": "cust-suspend",
            "text": text,
            "message_id": f"mid-{text}",
        },
    )


def test_the_assistant_stops_answering_a_suspended_company(
    platform, alpha, monkeypatch
):
    import channels.inbound as inbound

    _wire(platform, monkeypatch)

    scheduled: list = []
    monkeypatch.setattr(
        inbound,
        "schedule_smart_reply",
        lambda **kwargs: scheduled.append(kwargs) or {"queued": True},
    )

    # Positive control first. Without it, "nothing was scheduled" would pass on
    # a platform that never schedules anything.
    _inbound(alpha["id"], text="before")

    assert len(scheduled) == 1, (
        "positive control: an active company did not schedule a reply, so the "
        "assertion below would prove nothing"
    )

    scheduled.clear()
    _suspend(platform, alpha["id"])

    result = _inbound(alpha["id"], text="after")

    assert not scheduled, "the assistant answered for a suspended company"
    assert result.get("reason") == "company_suspended", result


def test_the_customers_message_is_still_stored(platform, alpha, monkeypatch):
    """Reinstatement must find the messages waiting, not a hole."""
    import channels.inbound as inbound

    test_manager = _wire(platform, monkeypatch)
    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    _suspend(platform, alpha["id"])
    _inbound(alpha["id"], text="I would like to order")

    with test_manager.tenant(alpha["id"]) as conn:
        rows = conn.execute(
            "SELECT body FROM messages WHERE direction = 'in'"
        ).fetchall()

    assert [row["body"] for row in rows] == ["I would like to order"], (
        "a customer's message was dropped because their supplier was suspended"
    )


def test_the_customer_is_told_nothing(platform, alpha, monkeypatch):
    """Silence, not "this business has been suspended" — which would expose the
    owner to their own customers."""
    import channels.inbound as inbound

    _wire(platform, monkeypatch)

    sent: list = []
    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    import channels.sender as sender

    monkeypatch.setattr(sender, "send_text", lambda **kwargs: sent.append(kwargs))

    _suspend(platform, alpha["id"])
    _inbound(alpha["id"], text="hello")

    assert not sent, "the platform explained the suspension to the customer"


def test_the_assistant_answers_again_after_reinstatement(
    platform, alpha, monkeypatch
):
    import channels.inbound as inbound

    _wire(platform, monkeypatch)

    scheduled: list = []
    monkeypatch.setattr(
        inbound,
        "schedule_smart_reply",
        lambda **kwargs: scheduled.append(kwargs) or {"queued": True},
    )

    _suspend(platform, alpha["id"])
    _inbound(alpha["id"], text="while off")

    assert not scheduled

    _reinstate(platform, alpha["id"])
    _inbound(alpha["id"], text="after back")

    assert len(scheduled) == 1, (
        "reinstating the company did not bring its assistant back"
    )


def test_a_suspended_company_does_not_publish_scheduled_posts(
    platform, alpha, monkeypatch
):
    """The consequence that is public: a suspended company still posting to its
    own followers is the platform acting for a company it just switched off, in
    front of an audience."""
    import channels.post_publisher as publisher

    _wire(platform, monkeypatch)

    published: list = []
    monkeypatch.setattr(
        publisher,
        # `ok`, not `success` — `publish_due_posts` reads `ok`, and a stub with
        # the wrong key makes a failed publish look like a refused one, so the
        # gate assertion would pass whether or not the gate existed.
        "publish_post",
        lambda **kwargs: published.append(kwargs) or {"ok": True},
    )

    from backend.services.scheduler_service import scheduler_service

    post = scheduler_service.create_post(
        company_id=alpha["id"],
        channel="messenger",
        body="Open late on Thursday.",
        scheduled_for="2020-01-01T10:00:00Z",
        created_by_user_id=None,
    )
    scheduler_service.approve(
        company_id=alpha["id"], post_id=int(post["id"]), approver_user_id=1
    )

    _suspend(platform, alpha["id"])

    assert publisher.publish_due_posts(alpha["id"]) == 0, (
        "a suspended company published to its followers"
    )
    assert not published

    # The queue survived: reinstating sends it, rather than the suspension
    # having silently thrown the company's campaign away.
    _reinstate(platform, alpha["id"])

    assert publisher.publish_due_posts(alpha["id"]) == 1, (
        "the post was lost rather than held — suspension deleted work the "
        "company had already approved"
    )
    assert published


def test_a_suspended_company_does_not_deliver_replies_already_queued(
    platform, alpha, monkeypatch
):
    """Batches queued in the minutes before the suspension are still sitting
    due, and delivering them would answer customers after the company was
    switched off."""
    import channels.meta.smart_reply as smart_reply

    test_manager = _wire(platform, monkeypatch)

    answered: list = []
    monkeypatch.setattr(
        smart_reply, "_process_batch", lambda batch: answered.append(batch) or True
    )

    from backend.services.pending_reply_service import pending_reply_service

    pending_reply_service.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-queued",
        message="Are you open?",
        delay_seconds=0,
    )

    # `enqueue` clamps the delay to a minimum, so the batch is moved into the
    # past rather than waited out.
    with test_manager.tenant(alpha["id"]) as conn:
        conn.execute(
            "UPDATE pending_replies SET deliver_after = '2000-01-01T00:00:00Z'"
        )
        conn.commit()

    _suspend(platform, alpha["id"])

    assert smart_reply.process_due_replies(alpha["id"]) == 0, (
        "the assistant answered a customer after the company was suspended"
    )
    assert not answered

    # Nothing was claimed, so reinstating answers the customer who was waiting.
    _reinstate(platform, alpha["id"])

    assert smart_reply.process_due_replies(alpha["id"]) == 1, (
        "the queued reply was consumed by the suspension and the customer "
        "never got an answer"
    )


def test_suspending_takes_effect_immediately(platform, alpha, monkeypatch):
    """The gate caches for thirty seconds, which is exactly the half minute an
    operator spends watching the screen after suspending a company.

    This is the one test that goes through `set_company_status` rather than
    driving the column, because dropping that cache is that method's job.
    """
    import channels.inbound as inbound

    _wire(platform, monkeypatch)

    scheduled: list = []
    monkeypatch.setattr(
        inbound,
        "schedule_smart_reply",
        lambda **kwargs: scheduled.append(kwargs) or {"queued": True},
    )

    # Warm the cache with "active" so a stale read would be visible.
    _inbound(alpha["id"], text="warm")

    assert len(scheduled) == 1
    scheduled.clear()

    from backend.services.platform_service import platform_service

    platform_service.set_company_status(alpha["id"], "suspended")

    result = _inbound(alpha["id"], text="right after")

    assert not scheduled, (
        "the assistant kept answering after the operator suspended the "
        "company — set_company_status did not drop the cached status"
    )
    assert result.get("reason") == "company_suspended", result
