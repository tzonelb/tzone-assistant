"""Tests for the control-plane index of outstanding background work.

The platform is being taken to a thousand companies sending and receiving at the
same time. The old sweeps could not survive that: `_run_for_every_company`
opened every company's encrypted database, in sequence, every two seconds,
whether or not any of them had anything to do. Sweep latency grew with the
number of companies, so the two-second cadence became a fiction and replies went
out late.

`company_work_index` in the control database answers "which companies have work"
without opening anything encrypted. These tests are about the two ways that can
go wrong:

* it opens companies it did not need to — wasteful, and the thing being fixed;
* it fails to open a company that *does* have work — silent, and the reason the
  index is biased everywhere toward being too eager rather than too clever.

The second failure is what most of this file is about. It has no symptom: no
error, no log line, just a customer who never gets an answer.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture()
def scheduling(platform, monkeypatch):
    """Point every module at the test platform, and count database opens.

    Counting is the point of several tests below: "the sweep did not open that
    company" is only worth asserting if it is observed rather than inferred, so
    `database_manager.tenant` is wrapped and every company it is called for is
    recorded.
    """
    import database.manager as manager_module

    # `backend.workers` rather than `main`: the sweeps moved out of the
    # application module, which now holds only what the app *is*. Imported
    # before the rebinding sweep below so that its own `database_manager` is
    # the live singleton the sweep looks for.
    import backend.workers  # noqa: F401
    import backend.services.pending_reply_service  # noqa: F401
    import backend.services.scheduler_service  # noqa: F401
    import backend.services.work_index_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.workers" in rebound
    assert "backend.services.work_index_service" in rebound

    opened: list[int] = []
    lock = threading.Lock()
    real_tenant = test_manager.tenant

    def counting_tenant(company_id: int):
        with lock:
            opened.append(int(company_id))
        return real_tenant(company_id)

    monkeypatch.setattr(test_manager, "tenant", counting_tenant)

    from backend.services.pending_reply_service import pending_reply_service
    from backend.services.scheduler_service import scheduler_service
    from backend.services.work_index_service import work_index_service

    from backend import workers

    def sweep(kind, work) -> None:
        """Run exactly one tick of the real sweep."""
        asyncio.run(workers._sweep("test sweep", kind, work))

    def clear() -> None:
        with lock:
            opened.clear()

    return SimpleNamespace(
        manager=test_manager,
        opened=opened,
        replies=pending_reply_service,
        posts=scheduler_service,
        index=work_index_service,
        sweep=sweep,
        clear=clear,
    )


def _iso(seconds_from_now: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    ).isoformat()


def _make_due(scheduling, company, external_user_id: str | None = None) -> None:
    """Bring a queued batch's delivery time forward instead of sleeping for it.

    `enqueue` clamps its delay to a minimum, so a freshly queued batch is never
    immediately due. The index is re-derived afterwards exactly as a sweep would
    re-derive it, so the test is still exercising the real path.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    sql = "UPDATE pending_replies SET deliver_after = '2000-01-01T00:00:00+00:00'"
    params: tuple = ()

    if external_user_id is not None:
        sql += " WHERE external_user_id = ?"
        params = (external_user_id,)

    with scheduling.manager.tenant(company["id"]) as conn:
        conn.execute(sql, params)
        conn.commit()

    scheduling.clear()
    scheduling.index.refresh(company["id"], (KIND_PENDING_REPLY,))
    scheduling.clear()


def _queue_reply(scheduling, company, external_user_id: str = "cust-1") -> None:
    scheduling.replies.enqueue(
        company_id=company["id"],
        channel="messenger",
        external_user_id=external_user_id,
        message="hello",
        delay_seconds=0,
    )


# ----------------------------------------------------------------------
# What the sweep opens
# ----------------------------------------------------------------------


def test_a_company_with_nothing_due_is_never_opened(scheduling, alpha, beta):
    """The defect, stated as an assertion rather than as a hope.

    At a thousand companies the old sweep opened a thousand encrypted databases
    every two seconds to discover that almost none of them had anything to do.
    An idle company must now cost nothing at all — not a cheap open, not a
    fast one: none.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, beta)
    _make_due(scheduling, beta)

    scheduling.clear()
    scheduling.sweep(KIND_PENDING_REPLY, scheduling.replies.claim_due)

    assert alpha["id"] not in scheduling.opened, (
        "a company with an empty queue was opened by the sweep"
    )


def test_a_company_with_work_due_is_opened_and_its_work_runs(scheduling, alpha):
    """The other half. An index that opens nothing is not an optimisation."""
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, alpha)
    _make_due(scheduling, alpha)

    claimed: list[dict] = []

    def work(company_id: int) -> None:
        claimed.extend(scheduling.replies.claim_due(company_id))

    scheduling.clear()
    scheduling.sweep(KIND_PENDING_REPLY, work)

    assert alpha["id"] in scheduling.opened
    assert len(claimed) == 1
    assert claimed[0]["company_id"] == alpha["id"]
    assert claimed[0]["messages"] == ["hello"]


def test_only_the_companies_with_work_are_opened(scheduling, platform):
    """The cost of a sweep follows the work, not the size of the platform.

    Two companies is enough to state the property; `tools/sweep_benchmark.py`
    measures it at a realistic company count, where provisioning hundreds of
    encrypted databases takes long enough that it does not belong in a suite
    that runs on every commit.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    alpha = platform["companies"]["alpha"]
    beta = platform["companies"]["beta"]

    _queue_reply(scheduling, alpha)
    _make_due(scheduling, alpha)

    scheduling.clear()
    scheduling.sweep(KIND_PENDING_REPLY, scheduling.replies.claim_due)

    assert set(scheduling.opened) == {alpha["id"]}
    assert beta["id"] not in scheduling.opened


# ----------------------------------------------------------------------
# Nothing is lost
# ----------------------------------------------------------------------


def test_work_queued_during_a_sweep_is_picked_up_by_the_next_one(scheduling, alpha):
    """A message that lands while its own company is being swept.

    The sweep clears a company's entry when it finds the queue empty. A message
    arriving in that window must not be cleared with it — that batch would then
    wait for the hourly reconcile, and the customer for an answer.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, alpha, "early")
    _make_due(scheduling, alpha, "early")

    def work(company_id: int) -> None:
        for batch in scheduling.replies.claim_due(company_id):
            scheduling.replies.complete(company_id, batch["id"])

        # The customer writes again while the worker is still in this company.
        _queue_reply(scheduling, alpha, "late")

    scheduling.sweep(KIND_PENDING_REPLY, work)

    assert scheduling.index.due_companies(
        KIND_PENDING_REPLY, now=_iso(600)
    ) == [alpha["id"]], "the batch queued mid-sweep was dropped from the index"

    _make_due(scheduling, alpha, "late")
    scheduling.clear()

    claimed: list[dict] = []
    scheduling.sweep(
        KIND_PENDING_REPLY,
        lambda company_id: claimed.extend(scheduling.replies.claim_due(company_id)),
    )

    assert [batch["external_user_id"] for batch in claimed] == ["late"]


def test_work_queued_between_the_sweeps_read_and_its_write_is_not_erased(
    scheduling, alpha, monkeypatch
):
    """The race the `revision` column exists for.

    A sweep decides a company is finished by reading its queue and then writing
    that conclusion to the control plane. Between those two moments a webhook
    can queue a batch. Without the guard the sweep's write wins and deletes an
    entry for work that really exists — the one failure this index must never
    have, because it is completely silent.

    The interleaving is forced here rather than hoped for: a real one needs two
    threads to meet inside a few microseconds.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, alpha, "first")
    _make_due(scheduling, alpha, "first")

    original_write_back = scheduling.index._write_back
    interleaved = {"done": False}

    def write_back_after_a_message_arrives(company_id, due, before):
        if not interleaved["done"]:
            interleaved["done"] = True
            # The sweep has read an empty queue; this arrives before it writes.
            _queue_reply(scheduling, alpha, "raced")
        return original_write_back(company_id, due, before)

    def work(company_id: int) -> None:
        for batch in scheduling.replies.claim_due(company_id):
            scheduling.replies.complete(company_id, batch["id"])

    monkeypatch.setattr(
        scheduling.index, "_write_back", write_back_after_a_message_arrives
    )
    scheduling.sweep(KIND_PENDING_REPLY, work)

    assert interleaved["done"], "the interleaving under test never happened"
    assert scheduling.index.snapshot(alpha["id"]).get(KIND_PENDING_REPLY), (
        "the sweep erased an entry for a batch queued while it was deciding"
    )


def test_a_queue_written_without_an_index_is_found_by_reconciliation(
    scheduling, alpha
):
    """Restart, and upgrade from a release without this index.

    `pending_replies` is a table precisely so a deploy does not discard the
    customers still waiting. That guarantee only survives if the index can be
    rebuilt from the tables rather than remembered, so this writes a batch the
    way an older release would have — with no index entry at all — and requires
    the reconcile to find it.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, alpha, "orphan")
    _make_due(scheduling, alpha, "orphan")

    with scheduling.manager.control() as conn:
        conn.execute("DELETE FROM company_work_index")
        conn.commit()

    assert scheduling.index.due_companies(KIND_PENDING_REPLY) == []

    summary = scheduling.index.reconcile_all()

    assert summary["with_work"] == 1
    assert scheduling.index.due_companies(KIND_PENDING_REPLY) == [alpha["id"]]


def test_a_failing_company_is_still_re_derived(scheduling, alpha):
    """A company whose work raises must not fall out of the index, and must not
    be swept for ever on a deadline nothing can clear. The entry is re-derived
    from its own tables either way."""
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, alpha)
    _make_due(scheduling, alpha)

    def work(company_id: int) -> None:
        raise RuntimeError("the provider is down")

    scheduling.sweep(KIND_PENDING_REPLY, work)

    # The batch is untouched, so the company is still due — the failure did not
    # cost it its place.
    assert scheduling.index.due_companies(KIND_PENDING_REPLY) == [alpha["id"]]


# ----------------------------------------------------------------------
# The index and the tenant tables agree
# ----------------------------------------------------------------------


def test_the_index_matches_the_queue_through_enqueue_claim_complete_and_fail(
    scheduling, alpha
):
    """The whole lifecycle, checked against the table it describes.

    An index that disagrees with the queue is worse than the sweep it replaced:
    the disagreement does not show up as an error, it shows up as work that
    never runs.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    company_id = alpha["id"]

    def entry() -> dict | None:
        return scheduling.index.snapshot(company_id).get(KIND_PENDING_REPLY)

    def earliest_in_queue() -> str | None:
        with scheduling.manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT MIN(deliver_after) AS due FROM pending_replies"
            ).fetchone()
        return row["due"] if row else None

    assert entry() is None, "nothing queued, nothing indexed"

    # enqueue -------------------------------------------------------------
    _queue_reply(scheduling, alpha, "cust")
    assert entry() is not None
    assert entry()["due_at"] == earliest_in_queue()

    # claim ---------------------------------------------------------------
    _make_due(scheduling, alpha, "cust")
    claimed = scheduling.replies.claim_due(company_id)
    assert len(claimed) == 1

    scheduling.index.refresh(company_id, (KIND_PENDING_REPLY,))
    assert entry()["due_at"] == earliest_in_queue(), (
        "a leased batch must stay indexed — it can still fail and need retrying"
    )

    # fail ----------------------------------------------------------------
    assert scheduling.replies.fail(company_id, claimed[0]["id"], "boom", 30)
    scheduling.index.refresh(company_id, (KIND_PENDING_REPLY,))
    assert entry()["due_at"] == earliest_in_queue()

    # complete ------------------------------------------------------------
    scheduling.replies.complete(company_id, claimed[0]["id"])
    scheduling.index.refresh(company_id, (KIND_PENDING_REPLY,))
    assert earliest_in_queue() is None
    assert entry() is None, "an empty queue must leave no entry behind"


def test_a_batch_pushed_back_keeps_its_earlier_place_until_a_sweep_looks(
    scheduling, alpha
):
    """The deliberate bias, made visible.

    A customer who keeps typing pushes their batch's delivery time out. The
    index is not allowed to follow it out, because a writer that can move a
    deadline later can move it past the point where anyone looks. It stays
    early, the next sweep finds nothing to do, and it is that sweep — which has
    just read the table — that writes the later time.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, alpha, "chatty")
    first = scheduling.index.snapshot(alpha["id"])[KIND_PENDING_REPLY]["due_at"]

    _queue_reply(scheduling, alpha, "chatty")
    second = scheduling.index.snapshot(alpha["id"])[KIND_PENDING_REPLY]["due_at"]

    assert second == first, "a later deadline overwrote an earlier one"

    scheduling.index.refresh(alpha["id"], (KIND_PENDING_REPLY,))
    corrected = scheduling.index.snapshot(alpha["id"])[KIND_PENDING_REPLY]["due_at"]

    assert corrected > first, "the sweep did not correct the eager entry"


# ----------------------------------------------------------------------
# Scheduled posts
# ----------------------------------------------------------------------


def test_a_post_enters_the_index_when_it_is_approved_and_not_before(
    scheduling, alpha
):
    """A draft is never claimed, so a company holding one is not work. Approval
    is the moment it becomes work, and the moment the sweep has to know."""
    from backend.services.work_index_service import KIND_SCHEDULED_POST

    post = scheduling.posts.create_post(
        company_id=alpha["id"],
        channel="messenger",
        body="Hello",
        scheduled_for=_iso(-60),
        created_by_user_id=1,
    )

    assert scheduling.index.due_companies(KIND_SCHEDULED_POST) == []

    scheduling.posts.approve(
        company_id=alpha["id"], post_id=post["id"], approver_user_id=2
    )

    assert scheduling.index.due_companies(KIND_SCHEDULED_POST) == [alpha["id"]]


def test_a_published_post_leaves_the_index(scheduling, alpha):
    """Otherwise the company is opened every thirty seconds for ever."""
    from backend.services.work_index_service import KIND_SCHEDULED_POST

    post = scheduling.posts.create_post(
        company_id=alpha["id"],
        channel="messenger",
        body="Hello",
        scheduled_for=_iso(-60),
        created_by_user_id=1,
    )
    scheduling.posts.approve(
        company_id=alpha["id"], post_id=post["id"], approver_user_id=2
    )

    def work(company_id: int) -> None:
        for claimed in scheduling.posts.claim_due(company_id):
            scheduling.posts.mark_published(
                company_id=company_id,
                post_id=claimed["id"],
                provider_post_id="PROVIDER_1",
            )

    scheduling.sweep(KIND_SCHEDULED_POST, work)

    assert scheduling.index.due_companies(KIND_SCHEDULED_POST) == []
    assert scheduling.index.snapshot(alpha["id"]).get(KIND_SCHEDULED_POST) is None

    scheduling.clear()
    scheduling.sweep(KIND_SCHEDULED_POST, work)

    assert scheduling.opened == [], "a finished company was opened again"


def test_moving_a_post_earlier_moves_the_company_with_it(scheduling, alpha):
    """Rescheduling an approved post is a new deadline, not a note on an old
    one."""
    from backend.services.work_index_service import KIND_SCHEDULED_POST

    post = scheduling.posts.create_post(
        company_id=alpha["id"],
        channel="messenger",
        body="Hello",
        scheduled_for=_iso(3600),
        created_by_user_id=1,
    )
    scheduling.posts.approve(
        company_id=alpha["id"], post_id=post["id"], approver_user_id=2
    )

    assert scheduling.index.due_companies(KIND_SCHEDULED_POST) == []

    scheduling.posts.update_post(
        company_id=alpha["id"],
        post_id=post["id"],
        values={"scheduled_for": _iso(-30)},
    )

    assert scheduling.index.due_companies(KIND_SCHEDULED_POST) == [alpha["id"]]


# ----------------------------------------------------------------------
# Takeovers
# ----------------------------------------------------------------------


def test_a_takeover_registers_its_own_expiry(scheduling, alpha, beta):
    """The takeover sweep looks time-based, which is why it used to open every
    company every ten seconds. It is not: an expiry exists only because an
    employee took a conversation over, and that action writes an exact deadline.
    Same shape, same index — and a company where nobody has taken anything over
    does no work at all.
    """
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.work_index_service import KIND_TAKEOVER

    conversation_control_service.set_ai_mode(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-takeover",
        handled_by_ai=False,
        actor_user_id=1,
    )

    entry = scheduling.index.snapshot(alpha["id"]).get(KIND_TAKEOVER)

    assert entry is not None, "a takeover that nothing will expire"
    assert scheduling.index.snapshot(beta["id"]).get(KIND_TAKEOVER) is None

    # Not due yet: the timer has minutes to run, so the sweep leaves it alone.
    assert scheduling.index.due_companies(KIND_TAKEOVER) == []
    assert scheduling.index.due_companies(KIND_TAKEOVER, now=_iso(3600)) == [
        alpha["id"]
    ]


def test_an_expired_takeover_leaves_the_index(scheduling, alpha):
    """Once the conversation is back with the assistant there is nothing left to
    expire, and the company drops out of the sweep."""
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.work_index_service import KIND_TAKEOVER

    conversation_control_service.set_ai_mode(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-takeover",
        handled_by_ai=False,
        actor_user_id=1,
    )

    with scheduling.manager.tenant(alpha["id"]) as conn:
        conn.execute(
            "UPDATE conversations SET takeover_expires_at = '2000-01-01T00:00:00+00:00'"
        )
        conn.commit()

    scheduling.index.refresh(alpha["id"], (KIND_TAKEOVER,))
    assert scheduling.index.due_companies(KIND_TAKEOVER) == [alpha["id"]]

    scheduling.sweep(
        KIND_TAKEOVER, conversation_control_service.expire_overdue_takeovers
    )

    assert scheduling.index.due_companies(KIND_TAKEOVER) == []


# ----------------------------------------------------------------------
# Suspended companies
# ----------------------------------------------------------------------


def test_a_suspended_company_is_not_swept_but_keeps_its_place(
    scheduling, alpha, beta
):
    """Decision: a suspended company is not swept, and does not lose its work.

    `database_manager.list_company_ids` filters to active companies, so the
    sweeps already skipped suspended ones before this change. The index does the
    same, deliberately: whether a suspended company should still have its
    replies delivered is a product question about what suspension means, and
    answering it here would smuggle a behaviour change into a performance fix.

    What the index does add is that nothing prunes a suspended company's rows —
    no sweep opens it, so no sweep clears it. Its queued work keeps its place
    and is picked up the moment the company is reactivated, rather than having
    to wait for the next hourly reconcile to be rediscovered.
    """
    from backend.services.work_index_service import KIND_PENDING_REPLY

    _queue_reply(scheduling, beta)
    _make_due(scheduling, beta)

    assert scheduling.index.due_companies(KIND_PENDING_REPLY) == [beta["id"]]

    with scheduling.manager.control() as conn:
        conn.execute(
            "UPDATE companies SET status = 'suspended' WHERE id = ?", (beta["id"],)
        )
        conn.commit()

    scheduling.clear()
    scheduling.sweep(KIND_PENDING_REPLY, scheduling.replies.claim_due)

    assert scheduling.opened == [], "a suspended company was swept"
    assert scheduling.index.snapshot(beta["id"]).get(KIND_PENDING_REPLY), (
        "a suspended company lost its queued work from the index"
    )

    with scheduling.manager.control() as conn:
        conn.execute(
            "UPDATE companies SET status = 'active' WHERE id = ?", (beta["id"],)
        )
        conn.commit()

    claimed: list[dict] = []
    scheduling.sweep(
        KIND_PENDING_REPLY,
        lambda company_id: claimed.extend(scheduling.replies.claim_due(company_id)),
    )

    assert len(claimed) == 1, "reactivating a company did not resume its work"


# ----------------------------------------------------------------------
# Bounded concurrency
# ----------------------------------------------------------------------


def test_companies_are_swept_concurrently_but_within_the_bound(
    scheduling, platform, monkeypatch
):
    """A hundred companies with work must not be a hundred round trips — and
    must not be a hundred at once either.

    The bound is what keeps a sweep from taking the whole thread pool the API
    shares with it, and from putting one unbounded model call per company in
    flight at the same moment.
    """
    from config.settings import config

    monkeypatch.setattr(config, "SWEEP_MAX_CONCURRENT_COMPANIES", 3)

    company_ids = [company["id"] for company in platform["companies"].values()] * 6

    live = 0
    peak = 0
    lock = threading.Lock()

    def work(company_id: int) -> None:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.02)
        with lock:
            live -= 1

    from backend import workers

    asyncio.run(workers._run_for_companies("bounded", company_ids, work))

    assert peak > 1, "the sweep is still strictly one company at a time"
    assert peak <= 3, f"the sweep ran {peak} companies at once, over its bound"


def test_one_companys_failure_does_not_stop_the_others(scheduling, platform):
    """Kept from the sweep this replaces: a company whose database cannot be
    opened must not cost every other company its turn."""
    company_ids = [company["id"] for company in platform["companies"].values()]
    served: list[int] = []

    def work(company_id: int) -> None:
        if company_id == company_ids[0]:
            raise RuntimeError("this database will not open")
        served.append(company_id)

    from backend import workers

    asyncio.run(workers._run_for_companies("isolating", company_ids, work))

    assert served == company_ids[1:]
