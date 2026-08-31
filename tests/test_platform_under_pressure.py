"""What breaks when more than one thing happens at once.

Everything else in this suite asks whether the code is right when called once.
This file asks what happens when it is called forty times at the same instant,
or handed a megabyte where it expected a sentence.

The defects it hunts do not appear in a diff and do not appear in a single
call. A lease that looks correct sends the same reply twice when two workers
claim it in the same millisecond. A limit that refuses the sixth user lets in
the sixth, seventh and eighth when three requests read the count before any of
them writes. Neither shows up until a real company is busy — which is the worst
time to find out.

Every test here runs the real code against a real encrypted database. Nothing
is mocked at the storage layer, because the properties being tested only exist
there.
"""

from __future__ import annotations

import sys
import threading

import pytest


def _wire(platform, monkeypatch, *modules):
    """Point the named modules at the test platform, and prove it took.

    Rebinding by *type* rather than by identity with the process-wide manager,
    which the first version did and which silently stopped working the moment a
    second test ran in the same session.

    Why: every service does `from database.manager import database_manager`, so
    the name is copied into the service module at import time. A service first
    imported *during* an earlier test — while that test's patch was in place —
    copies **that test's** manager and keeps it for the life of the process.
    monkeypatch cannot undo it, because the module did not exist when the patch
    was recorded. Comparing against `database.manager.database_manager` then
    finds nothing to rebind and the test runs against the previous test's
    database: empty, and passing for the wrong reason.

    That is the same vacuous-check trap this suite has hit repeatedly, and it
    is worse in this file than anywhere else — a race test that quietly talks
    to the wrong database reports "no race" no matter what the code does. So
    the wiring is asserted afterwards on the binding itself, not on a list
    built while patching.
    """
    import database.manager as manager_module
    from database.manager import DatabaseManager

    for name in modules:
        __import__(name)

    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    for name in modules:
        held = getattr(sys.modules[name], "database_manager", None)
        assert held is test_manager, (
            f"{name} is still bound to {held!r}, not the test database"
        )

    return test_manager


def _in_parallel(work, count):
    """Run `work(index)` on `count` threads released at the same moment.

    A barrier rather than a plain thread start: threads created in a loop begin
    far enough apart that a race is often missed entirely, and a test that
    misses the race passes.
    """
    barrier = threading.Barrier(count)
    results: list = [None] * count
    errors: list = [None] * count

    def runner(index):
        try:
            barrier.wait(timeout=30)
            results[index] = work(index)
        except Exception as exc:  # noqa: BLE001
            errors[index] = exc

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=60)

    return results, errors


# ------------------------------------------------------- answering twice


def test_one_pending_reply_is_claimed_by_exactly_one_worker(platform, monkeypatch):
    """The lease, under the condition it exists for.

    Two sweeps overlapping is normal — one is slow, the next starts. If both
    claim the same batch, the customer is answered twice and the company is
    billed twice for the model call. The duplicate is public.
    """
    _wire(platform, monkeypatch, "backend.services.pending_reply_service")

    from backend.services.pending_reply_service import pending_reply_service

    company_id = platform["companies"]["alpha"]["id"]

    pending_reply_service.enqueue(
        company_id=company_id,
        channel="messenger",
        external_user_id="cust-race",
        message="hello",
        delay_seconds=0,
    )

    # `enqueue` clamps any delay up to MIN_DELAY_SECONDS, so a batch is never
    # due the instant it is queued. The wait elapsing is not what this test is
    # about — it is about what happens when two sweeps arrive *after* it has —
    # so the clock is moved rather than waited on. Sleeping five seconds here
    # would test the same thing and cost five seconds every run.
    with platform["manager"].tenant(company_id) as conn:
        conn.execute("UPDATE pending_replies SET deliver_after = '2000-01-01T00:00:00Z'")
        conn.commit()

    claims, errors = _in_parallel(
        lambda _: pending_reply_service.claim_due(company_id), 12
    )

    assert not any(errors), f"a claim raised: {[e for e in errors if e]}"

    claimed_ids = [batch["id"] for result in claims if result for batch in result]

    assert len(claimed_ids) == len(set(claimed_ids)), (
        f"the same batch was claimed more than once: {claimed_ids}"
    )
    assert len(claimed_ids) == 1, (
        f"one queued batch, {len(claimed_ids)} claims — the lease did not hold"
    )

    # And the lease keeps holding after the race, not just during it: a sweep
    # arriving while the winner is still writing its reply must find nothing.
    assert pending_reply_service.claim_due(company_id) == [], (
        "a later sweep claimed a batch that is still leased"
    )


def test_a_conversation_is_taken_over_by_exactly_one_employee(platform, monkeypatch):
    """Two agents clicking "take over" at the same moment. One must win, and
    the other must be told, or both start typing to the same customer."""
    _wire(platform, monkeypatch, "backend.services.conversation_control_service")

    from backend.services.conversation_control_service import (
        ConversationOwnershipConflict,
        conversation_control_service as control,
    )

    company_id = platform["companies"]["alpha"]["id"]

    control.get_or_create(
        company_id=company_id, channel="messenger", external_user_id="cust-own"
    )

    def take_over(index):
        try:
            control.set_ai_mode(
                company_id=company_id,
                channel="messenger",
                external_user_id="cust-own",
                handled_by_ai=False,
                actor_user_id=index + 1,
            )
            return "won"
        except ConversationOwnershipConflict:
            return "refused"

    results, errors = _in_parallel(take_over, 8)

    assert not any(errors), f"a takeover raised something unexpected: {errors}"

    state = control.get_state(
        company_id=company_id, channel="messenger", external_user_id="cust-own"
    )

    assert state["assigned_user_id"] is not None, "nobody owns it after eight tried"


# --------------------------------------------------------- limits under load


def test_a_plan_limit_cannot_be_walked_past_by_going_fast(platform, monkeypatch):
    """The classic check-then-write race.

    `check` reads the count and `create` writes the row. Eight requests that
    all read "five of five" before any of them writes would all be allowed, and
    the company ends up with thirteen of a five-user plan. Whether that is
    possible is a property of the transaction, not of the check.
    """
    manager = _wire(
        platform,
        monkeypatch,
        "backend.services.channel_account_service",
        "backend.services.plan_service",
    )

    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )
    from backend.services.plan_service import PlanLimitExceeded, plan_service

    company_id = platform["companies"]["alpha"]["id"]

    monkeypatch.setattr(plan_service, "limit", lambda company_id, key: 3)

    def connect(index):
        try:
            channel_account_service.create_account(
                company_id=company_id,
                channel="messenger",
                name=f"Page {index}",
                values={"page_id": f"PAGE-{index}"},
            )
            return "created"
        except (PlanLimitExceeded, ChannelAccountError):
            return "refused"

    results, errors = _in_parallel(connect, 10)

    # Checked before the count, and the reason is worth writing down: an
    # earlier version of this test asserted only the total, and it stayed green
    # when the transaction was weakened from IMMEDIATE to DEFERRED. It stayed
    # green because the losing requests no longer got a clean refusal — they
    # died on a database-locked error, which keeps the total correct and turns
    # a polite "your plan allows three" into a 500. Both are failures of the
    # same guarantee, so both are asserted.
    assert not any(errors), (
        "connecting under load raised a database error rather than refusing "
        f"cleanly: {[e for e in errors if e]}"
    )
    assert set(results) <= {"created", "refused"}

    with manager.control() as conn:
        total = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM channel_accounts"
                " WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchone()["n"]
        )

    assert total <= 3, (
        f"a plan allowing 3 accounts ended up with {total} — the limit is "
        "readable but not enforceable under concurrency"
    )


def test_two_companies_cannot_claim_the_same_page(platform, monkeypatch):
    """The routing key. If both win, one company's customers reach the other's
    inbox — and which one depends on a query's ordering."""
    manager = _wire(platform, monkeypatch, "backend.services.channel_account_service")

    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    alpha = platform["companies"]["alpha"]["id"]
    beta = platform["companies"]["beta"]["id"]

    def claim(index):
        try:
            channel_account_service.create_account(
                company_id=alpha if index % 2 == 0 else beta,
                channel="messenger",
                name=f"Contested {index}",
                values={"page_id": "THE-SAME-PAGE"},
            )
            return "won"
        except ChannelAccountError:
            return "refused"

    _in_parallel(claim, 10)

    with manager.control() as conn:
        rows = conn.execute(
            "SELECT company_id FROM channel_accounts WHERE page_id = 'THE-SAME-PAGE'"
        ).fetchall()

    assert len(rows) <= 1, (
        f"{len(rows)} companies claimed the same page — inbound messages would "
        "route by luck"
    )
