"""A retention window nothing runs is a comment.

`DiagnosticsService.RETENTION_DAYS = 14` has been in the source since the
service was written. The only code that applied it was a button in the
developer console — `POST /api/developer/diagnostics/cleanup` — so for every
company nobody pressed it for, the number meant nothing and the table grew
without end.

That table is the fastest-filling one on the platform, and not because of any
attack. Nine `diagnostics_service.record` calls sit on the path of a single
inbound message: seven in `channels/meta/smart_reply.py` and two in
`channels/inbound.py`. A company handling a thousand customer messages a month
writes tens of thousands of diagnostic rows a month into its own encrypted
database, and before this nothing ever removed one.

The fix is not a new policy. Fourteen days was already decided and written
down; the periodic sweep that already visits every company to prune the
activity log now applies it, which only makes the existing number true.

This file checks the two halves that can each fail on their own: that the
sweep reaches the table at all, and that it keeps what is inside the window.
Deleting everything would pass a test that only looked for rows going away.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    import backend.services.activity_service  # noqa: F401
    import backend.services.diagnostics_service  # noqa: F401

    # `main` is imported here, before the sweep below, and not inside the
    # tests. Importing it while the patch is active would be the hazard
    # documented in `DatabaseManager.after_release`'s neighbour,
    # `tests/test_platform_under_pressure.py`: every module `main` pulls in for
    # the first time copies whatever `database_manager` currently is, and
    # monkeypatch cannot restore a module that did not exist when the patch was
    # recorded. Those modules would then hold this test's temporary database
    # for the life of the process, and every later test file in the run would
    # be talking to a directory that no longer exists.
    #
    # That is not a hypothetical either — it happened on the first run of this
    # file and took 84 tests in two unrelated files down with it.
    import backend.workers  # noqa: F401
    import main  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    for name in (
        "backend.services.activity_service",
        "backend.services.diagnostics_service",
        "backend.workers",
    ):
        assert getattr(sys.modules[name], "database_manager", None) is test_manager, (
            f"{name} is not talking to the test database"
        )

    return test_manager


def _seed_events(manager, company_id, *, days_old: int, count: int, kind: str):
    when = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()

    with manager.tenant(company_id) as conn:
        conn.executemany(
            """
            INSERT INTO diagnostic_events (
                company_id, event_type, channel, created_at
            )
            VALUES (?, ?, 'messenger', ?)
            """,
            [(company_id, kind, when) for _ in range(count)],
        )
        conn.commit()


def _count(manager, company_id, kind=None):
    with manager.tenant(company_id) as conn:
        if kind:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM diagnostic_events WHERE event_type = ?",
                (kind,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM diagnostic_events"
            ).fetchone()

    return int(row["n"])


def test_the_periodic_sweep_prunes_diagnostics(wired, platform, monkeypatch):
    """The sweep the application really runs, not the service method directly.

    Calling `diagnostics_service.cleanup` in a test would prove the cleanup
    works — which was never in doubt. What was missing is anything calling it.
    """
    from backend import workers

    monkeypatch.setattr(workers, "database_manager", wired)

    alpha = platform["companies"]["alpha"]["id"]

    _seed_events(wired, alpha, days_old=90, count=40, kind="old")
    _seed_events(wired, alpha, days_old=1, count=5, kind="recent")

    assert _count(wired, alpha) == 45

    workers._prune_activity_logs()

    assert _count(wired, alpha, "old") == 0, (
        "the periodic sweep does not reach diagnostic_events — the declared "
        "fourteen-day retention is applied by nothing but a button"
    )


def test_the_sweep_keeps_what_is_inside_the_window(wired, platform, monkeypatch):
    """The other half. A sweep that emptied the table would satisfy the test
    above and destroy the only record of why a reply went wrong — which is
    needed most in the days right after it happens."""
    from backend import workers

    monkeypatch.setattr(workers, "database_manager", wired)

    alpha = platform["companies"]["alpha"]["id"]

    _seed_events(wired, alpha, days_old=90, count=10, kind="old")
    _seed_events(wired, alpha, days_old=2, count=7, kind="recent")

    workers._prune_activity_logs()

    assert _count(wired, alpha, "recent") == 7, (
        "the sweep removed events inside the retention window"
    )


def test_every_company_is_swept_not_only_the_first(wired, platform, monkeypatch):
    from backend import workers

    monkeypatch.setattr(workers, "database_manager", wired)

    alpha = platform["companies"]["alpha"]["id"]
    beta = platform["companies"]["beta"]["id"]

    for company_id in (alpha, beta):
        _seed_events(wired, company_id, days_old=90, count=6, kind="old")

    workers._prune_activity_logs()

    assert _count(wired, alpha) == 0
    assert _count(wired, beta) == 0, (
        "only the first company was swept — the loop stops early or the "
        "company list is short"
    )


def test_one_companys_failure_does_not_stop_the_others(
    wired, platform, monkeypatch
):
    """A sweep that aborts on the first unreadable database leaves every
    company after it in the list unpruned, for ever, and says so nowhere."""
    from backend import workers
    from backend.services.diagnostics_service import diagnostics_service

    monkeypatch.setattr(workers, "database_manager", wired)

    alpha = platform["companies"]["alpha"]["id"]
    beta = platform["companies"]["beta"]["id"]

    for company_id in (alpha, beta):
        _seed_events(wired, company_id, days_old=90, count=4, kind="old")

    real_cleanup = diagnostics_service.cleanup

    def explode_for_alpha(*, company_id, **kwargs):
        if int(company_id) == alpha:
            raise RuntimeError("that database will not open")
        return real_cleanup(company_id=company_id, **kwargs)

    monkeypatch.setattr(diagnostics_service, "cleanup", explode_for_alpha)

    workers._prune_activity_logs()

    assert _count(wired, beta) == 0, (
        "one company's failure stopped the sweep before it reached the rest"
    )


# ------------------------------------------------- one row per customer message


def _seed_notifications(manager, company_id, *, days_old, count, read, title):
    from datetime import datetime, timedelta, timezone

    when = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()

    with manager.tenant(company_id) as conn:
        conn.executemany(
            """
            INSERT INTO notifications (
                company_id, notification_type, title, body, is_read, created_at
            )
            VALUES (?, 'customer_message', ?, 'A customer wrote in.', ?, ?)
            """,
            [(company_id, title, 1 if read else 0, when) for _ in range(count)],
        )
        conn.commit()


def _notifications(manager, company_id, title=None):
    with manager.tenant(company_id) as conn:
        if title:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM notifications WHERE title = ?", (title,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()

    return int(row["n"])


def test_old_read_notifications_are_dropped(wired, platform, monkeypatch):
    """Measured, not guessed: one notification row per inbound customer
    message, and nothing removed any of them.

    Deleting these loses nothing that is not kept elsewhere. A notification
    points at a message that stays in `messages`; what goes is the marker
    saying "this was new once", which after three months describes nothing
    anybody will act on.
    """
    from backend import workers

    monkeypatch.setattr(workers, "database_manager", wired)

    alpha = platform["companies"]["alpha"]["id"]

    _seed_notifications(wired, alpha, days_old=200, count=30, read=True, title="ancient")
    _seed_notifications(wired, alpha, days_old=3, count=4, read=True, title="recent")

    workers._prune_activity_logs()

    assert _notifications(wired, alpha, "ancient") == 0, (
        "read notifications are kept for ever — one row per customer message, "
        "growing without end"
    )
    assert _notifications(wired, alpha, "recent") == 4, (
        "notifications inside the window were deleted"
    )


def test_an_unread_notification_is_kept_much_longer(wired, platform, monkeypatch):
    """Unread is the state that still means something — somebody was away.

    A sweep that treated both the same would clear the pile a returning
    employee came back for, which is the one thing the bell is for.
    """
    from backend import workers

    monkeypatch.setattr(workers, "database_manager", wired)

    alpha = platform["companies"]["alpha"]["id"]

    _seed_notifications(
        wired, alpha, days_old=200, count=6, read=False, title="waiting"
    )

    workers._prune_activity_logs()

    assert _notifications(wired, alpha, "waiting") == 6, (
        "an unread notification from six months ago was deleted — that is the "
        "pile somebody returning from leave comes back to"
    )


def test_even_unread_notifications_have_a_ceiling(wired, platform, monkeypatch):
    """Otherwise a company that never opens the bell grows without bound, which
    is the case this retention was written for."""
    from backend import workers

    monkeypatch.setattr(workers, "database_manager", wired)

    alpha = platform["companies"]["alpha"]["id"]

    _seed_notifications(
        wired, alpha, days_old=800, count=9, read=False, title="forgotten"
    )

    workers._prune_activity_logs()

    assert _notifications(wired, alpha, "forgotten") == 0, (
        "unread notifications have no ceiling at all"
    )
