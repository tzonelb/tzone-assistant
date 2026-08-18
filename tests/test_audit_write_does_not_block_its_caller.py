"""An audit write must not wait on the transaction that caused it.

Found by pressure, not by reading. `create_account` opens the control database
with `BEGIN IMMEDIATE` and checks the plan limit *inside* that transaction —
correctly, because the check and the insert have to be atomic or two requests
racing both pass a limit of one. The refusal is then mirrored to the control
plane as a security event, and the mirror opened a **second** connection to the
control database and tried to write to it.

That connection waited for a lock held by its own caller, on its own thread.
SQLite cannot see that and does not try: it blocks until `busy_timeout`
expires. Measured on an idle machine with one request and no concurrency:

    SECONDS FOR ONE REFUSAL: 15.06
    security mirror rows in control audit_log: 0

Fifteen seconds, and then the security record was lost anyway — `_mirror`
catches its own failure so that recording a refusal can never change the
refusal. It changed it completely, and silently.

Under load it was worse than slow. The stalled transaction holds the control
database's write lock for the full fifteen seconds, and the control database is
where sessions, users and channel accounts live — so one company hitting its
plan limit stalled writes for every company on the platform. That is the whole
finding: a bookkeeping write, wrapped in a "this can never matter" handler,
able to stop the platform.

The fix is `DatabaseManager.after_release`: work queued from inside an open
transaction runs when the thread has closed every database, not before. Queued
rather than joined to the caller's transaction on purpose — the transaction
that records a refusal is precisely the one that gets rolled back, and the
record has to outlive it. This file asserts both halves: it is fast, and it is
still there afterwards.
"""

from __future__ import annotations

import sys
import time

import pytest


# Comfortably above a real refusal (~10ms) and far below the 15s busy_timeout
# that the defect burned. A regression cannot land between the two.
BUDGET_SECONDS = 3.0


@pytest.fixture()
def wired(platform, monkeypatch):
    from database.manager import DatabaseManager

    import backend.services.channel_account_service  # noqa: F401
    import backend.services.plan_service  # noqa: F401

    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.plan_service import plan_service

    # One connected channel allowed, so the second attempt is refused and the
    # refusal is what gets recorded.
    monkeypatch.setattr(plan_service, "limit", lambda company_id, key: 1)

    from backend.services.channel_account_service import channel_account_service

    return channel_account_service


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


def _refuse(service, platform):
    from backend.services.channel_account_service import ChannelAccountError

    service.create_account(
        company_id=_alpha(platform),
        channel="messenger",
        name="First",
        values={"page_id": "PAGE-1"},
    )

    start = time.monotonic()

    with pytest.raises(ChannelAccountError):
        service.create_account(
            company_id=_alpha(platform),
            channel="messenger",
            name="Second",
            values={"page_id": "PAGE-2"},
        )

    return time.monotonic() - start


def test_a_refused_connection_comes_back_immediately(wired, platform):
    elapsed = _refuse(wired, platform)

    assert elapsed < BUDGET_SECONDS, (
        f"refusing one channel took {elapsed:.2f}s. The audit write is waiting "
        "on the transaction that queued it — see after_release."
    )


def test_the_refusal_reaches_the_operator(wired, platform):
    """The half a timing test alone would miss.

    Deleting the mirror would make the timing test pass and leave an operator
    unable to see that a company is repeatedly hitting a limit.
    """
    _refuse(wired, platform)

    with platform["manager"].control() as conn:
        rows = conn.execute(
            "SELECT company_id FROM audit_log WHERE action = 'platform.plan_limit_hit'"
        ).fetchall()

    assert len(rows) == 1, "the plan-limit refusal never reached the control plane"
    assert int(rows[0]["company_id"]) == _alpha(platform)


def test_the_refusal_reaches_the_company_owner(wired, platform):
    _refuse(wired, platform)

    with platform["manager"].tenant(_alpha(platform)) as conn:
        rows = conn.execute(
            "SELECT summary FROM activity_log"
            " WHERE action = 'platform.plan_limit_hit'"
        ).fetchall()

    assert len(rows) == 1, "the company's own log has no record of the refusal"
    assert "1" in str(rows[0]["summary"])


def test_a_record_queued_inside_a_transaction_survives_its_rollback(platform):
    """Why the work is deferred rather than joined to the caller.

    Sharing the caller's connection would have fixed the stall too, and would
    have thrown the record away every time — a refusal rolls back, and the
    record of the refusal is the thing worth keeping.
    """
    manager = platform["manager"]
    company_id = _alpha(platform)
    ran: list[str] = []

    with manager.control() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE companies SET name = 'Rolled Back' WHERE id = ?", (company_id,)
        )
        manager.after_release(lambda: ran.append("yes"))

        assert ran == [], "the callback ran while the transaction was still open"

        conn.rollback()

    assert ran == ["yes"], "the callback never ran after the transaction closed"

    with manager.control() as conn:
        name = conn.execute(
            "SELECT name FROM companies WHERE id = ?", (company_id,)
        ).fetchone()["name"]

    assert name != "Rolled Back", "the fixture's rollback did not take"


def test_deferred_work_that_fails_does_not_reach_the_caller(platform):
    """The guarantee the original code claimed and did not have."""
    manager = platform["manager"]

    def explode():
        raise RuntimeError("bookkeeping blew up")

    with manager.control() as conn:
        conn.execute("SELECT 1")
        manager.after_release(explode)

    # Reaching here at all is the assertion: the exception was contained.
    assert True


def test_work_queued_with_nothing_open_runs_at_once(platform):
    """Otherwise every audit row on the ordinary path would be queued behind
    a release that never comes."""
    ran: list[str] = []

    platform["manager"].after_release(lambda: ran.append("yes"))

    assert ran == ["yes"]
