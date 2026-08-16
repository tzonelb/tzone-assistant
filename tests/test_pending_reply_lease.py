"""Tests for the lease on a pending reply batch.

One batch produces one reply, and one model charge. The lease is what
guarantees it: a worker claims a batch, generates the reply, and releases it
when the work is done or has failed.

`enqueue` used to set `locked_until = NULL` on every append, which released a
lease somebody was holding. A customer who kept typing while the assistant was
working freed the batch, the next sweep claimed the same one, and the customer
received two replies to a single conversation — with the platform paying the
model twice for it.

The failure is quiet: nothing errors, nothing is logged, and it only happens
when a message lands inside the seconds a reply takes to generate. That is
exactly the kind of defect that survives without a test.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.pending_reply_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.pending_reply_service" in rebound

    from backend.services.pending_reply_service import pending_reply_service

    return pending_reply_service


def _make_due(platform, company, external_user_id: str) -> None:
    """Bring a batch's delivery time forward instead of sleeping for it.

    `enqueue` clamps its delay to `MIN_DELAY_SECONDS`, so a freshly queued batch
    is never immediately due. Waiting five seconds per test would make this file
    slow enough that somebody eventually deletes it.
    """
    with platform["manager"].tenant(company["id"]) as conn:
        conn.execute(
            """
            UPDATE pending_replies
            SET deliver_after = '2000-01-01T00:00:00+00:00'
            WHERE external_user_id = ?
            """,
            (external_user_id,),
        )
        conn.commit()


def _batch(platform, company, external_user_id: str) -> dict:
    with platform["manager"].tenant(company["id"]) as conn:
        row = conn.execute(
            "SELECT * FROM pending_replies WHERE external_user_id = ? LIMIT 1",
            (external_user_id,),
        ).fetchone()

    return dict(row) if row else {}


def test_a_new_message_does_not_release_a_batch_a_worker_is_holding(
    service, platform, alpha
):
    """The defect: a message arriving mid-generation freed somebody's lease."""
    service.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        message="first",
        delay_seconds=0,
    )
    _make_due(platform, alpha, "cust-1")

    claimed = service.claim_due(alpha["id"])
    assert len(claimed) == 1, "a due batch should be claimable once"

    # The customer keeps typing while the assistant is still working.
    service.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        message="second",
        delay_seconds=0,
    )
    _make_due(platform, alpha, "cust-1")

    assert service.claim_due(alpha["id"]) == [], (
        "a second worker claimed a batch that was already being processed"
    )


def test_the_late_message_still_joins_the_batch(service, platform, alpha):
    """Holding the lease must not cost the customer their sentence. The point is
    one reply, not a dropped message."""
    service.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-2",
        message="first",
        delay_seconds=0,
    )
    _make_due(platform, alpha, "cust-2")
    service.claim_due(alpha["id"])

    service.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-2",
        message="second",
        delay_seconds=0,
    )

    assert "second" in _batch(platform, alpha, "cust-2")["messages_json"]


def test_a_failed_batch_is_released_and_retried(service, platform, alpha):
    """`fail` releases the lease deliberately, and that path has to keep
    working — otherwise a transient error would strand the batch until its
    lease aged out."""
    service.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-3",
        message="hello",
        delay_seconds=0,
    )
    _make_due(platform, alpha, "cust-3")

    claimed = service.claim_due(alpha["id"])
    assert claimed

    service.fail(alpha["id"], claimed[0]["id"], "transient", retry_in_seconds=0)
    _make_due(platform, alpha, "cust-3")

    assert service.claim_due(alpha["id"]), "a failed batch should be retried"


def test_a_completed_batch_is_gone(service, platform, alpha):
    """The other deliberate release. A completed batch is deleted, so it cannot
    be claimed again by anyone."""
    service.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-4",
        message="hello",
        delay_seconds=0,
    )
    _make_due(platform, alpha, "cust-4")

    claimed = service.claim_due(alpha["id"])
    service.complete(alpha["id"], claimed[0]["id"])

    assert _batch(platform, alpha, "cust-4") == {}
    assert service.claim_due(alpha["id"]) == []
