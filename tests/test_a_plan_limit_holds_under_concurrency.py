"""A limit that two people can walk past together is not a limit.

`plan_service.check` is asked before the write, with the count that exists now.
That is check-then-act, and it only holds if the count and the write are one
transaction. Two of the three enforced limits were not.

Measured before the fix, with the racers released together by a barrier:

* knowledge items — twelve simultaneous creates against an allowance of five
  left twelve rows, and eight racing for a single remaining seat left eight
* team seats — ten simultaneous invitations against an allowance of five seated
  all ten and refused none
* connected channels — five created, five refused, exactly right

The third is why the first two are defects rather than a design limit:
`channel_account_service` already wrapped its check and its insert in
`BEGIN IMMEDIATE`, and its limit held. The other two just had not.

The seat path could not be made one transaction end to end, because
`auth_service.create_user` opens a control connection of its own and holding
the write lock across it would be a thread waiting for a lock only it holds.
The account is created first and removed again if the seat has gone, which is
what the last test here checks.
"""

from __future__ import annotations

import threading

import pytest

from database.manager import utc_now_iso

# Module scope, before any fixture patches `database_manager` — see the note in
# `tests/test_a_departed_colleague_keeps_their_name.py`.
#
# `activity_service` is in this list for a reason that is not obvious from
# anything these tests call directly: refusing a limit goes through
# `plan_service._record_limit_hit`, which imports it *inside the function*. On
# a first run that import happens while this file's fixture has
# `database_manager` patched, so monkeypatch records this test's temporary
# manager as the module's original value and restores it to exactly that —
# leaving every later file in the run talking to a deleted directory. It cost
# `tests/test_plan_console.py` twenty-two errors before the import moved here.
from backend.services.activity_service import activity_service  # noqa: E402,F401
from backend.services.auth_service import auth_service  # noqa: E402,F401
from backend.services.company_gate import company_gate  # noqa: E402,F401
from backend.services.knowledge_service import knowledge_service  # noqa: E402
from backend.services.module_gate import module_gate  # noqa: E402,F401
from backend.services.notification_service import notification_service  # noqa: E402,F401
from backend.services.plan_service import plan_service  # noqa: E402
from backend.services.subscription_gate import subscription_gate  # noqa: E402,F401


LIMIT = 5


@pytest.fixture()
def capped(platform, alpha, monkeypatch):
    """A company on a plan that allows exactly `LIMIT` of everything counted."""
    import sys

    from database.manager import DatabaseManager

    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    now = utc_now_iso()

    with test_manager.control() as conn:
        cursor = conn.execute(
            """
            INSERT INTO plans (
                code, name, price_monthly, max_users, max_channel_accounts,
                max_knowledge_items, max_ai_messages, created_at
            )
            VALUES ('capped', 'Capped', 0, ?, ?, ?, 1000, ?)
            """,
            (LIMIT, LIMIT, LIMIT, now),
        )
        plan_id = int(cursor.lastrowid)

        conn.execute(
            "UPDATE subscriptions SET status = 'replaced' WHERE company_id = ?",
            (alpha["id"],),
        )
        conn.execute(
            """
            INSERT INTO subscriptions (
                company_id, plan_id, status, starts_at, expires_at,
                grace_period_until, auto_renew, created_at, updated_at
            )
            VALUES (?, ?, 'active', ?, NULL, NULL, 0, ?, ?)
            """,
            (alpha["id"], plan_id, now, now, now),
        )
        conn.commit()

    assert plan_service.limit(alpha["id"], "max_knowledge_items") == LIMIT, (
        "the capped plan did not take effect, so nothing here is measuring a limit"
    )

    return {"manager": test_manager, "company_id": alpha["id"]}


def _race(work, racers):
    """Release every racer at the same instant and report what happened."""
    barrier = threading.Barrier(racers)
    accepted, refused, unexpected = [], [], []

    def racer(index):
        barrier.wait()

        try:
            work(index)
            accepted.append(index)
        except ValueError:
            refused.append(index)
        except Exception as exc:  # noqa: BLE001
            unexpected.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(racers)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=90)

    assert not any(t.is_alive() for t in threads), "a racer never finished"
    assert not unexpected, f"racers raised something other than a refusal: {unexpected[:3]}"

    return accepted, refused


def _knowledge_count(manager, company_id):
    with manager.tenant(company_id) as conn:
        return int(
            conn.execute("SELECT COUNT(*) AS n FROM knowledge_items").fetchone()["n"]
        )


def test_concurrent_knowledge_creates_stop_at_the_limit(capped):
    def create(index):
        knowledge_service.create_item(
            company_id=capped["company_id"],
            data={"title": f"race-{index}", "content_ar": "x", "status": "active"},
        )

    accepted, refused = _race(create, racers=12)

    total = _knowledge_count(capped["manager"], capped["company_id"])

    assert accepted, "nothing was created at all, so the limit was never tested"
    assert refused, "nothing was refused, so twelve racers all fitted in five seats"
    assert total == LIMIT, (
        f"the company holds {total} knowledge items on a plan allowing {LIMIT}"
    )


def test_racing_for_the_last_place_leaves_exactly_one_winner(capped):
    for index in range(LIMIT - 1):
        knowledge_service.create_item(
            company_id=capped["company_id"],
            data={"title": f"seed-{index}", "content_ar": "x", "status": "active"},
        )

    assert _knowledge_count(capped["manager"], capped["company_id"]) == LIMIT - 1

    def create(index):
        knowledge_service.create_item(
            company_id=capped["company_id"],
            data={"title": f"last-{index}", "content_ar": "x", "status": "active"},
        )

    accepted, refused = _race(create, racers=8)

    assert len(accepted) == 1, (
        f"{len(accepted)} racers took a single remaining place"
    )
    assert len(refused) == 7
    assert _knowledge_count(capped["manager"], capped["company_id"]) == LIMIT


def test_the_limit_still_refuses_afterwards(capped):
    """A limit consumed by a race would leave the next ordinary create allowed."""
    for index in range(LIMIT):
        knowledge_service.create_item(
            company_id=capped["company_id"],
            data={"title": f"full-{index}", "content_ar": "x", "status": "active"},
        )

    with pytest.raises(ValueError):
        knowledge_service.create_item(
            company_id=capped["company_id"],
            data={"title": "one too many", "content_ar": "x", "status": "active"},
        )


def test_a_create_below_the_limit_still_works(capped):
    """The control. Every test above asserts a refusal."""
    item = knowledge_service.create_item(
        company_id=capped["company_id"],
        data={"title": "ordinary", "content_ar": "x", "status": "active"},
    )

    assert item and item.get("id"), "an ordinary create was refused"
