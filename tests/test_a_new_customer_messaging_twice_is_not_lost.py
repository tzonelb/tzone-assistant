"""Two messages at once from the same new customer must not lose one.

`upsert_from_channel` runs on the inbound path, on every message, from every
channel. It looked the customer up and inserted them if they were new — as two
separate statements, so two messages arriving at the same instant from someone
not yet known both found nothing and both inserted.

The unique index on `(company_id, channel, external_user_id)` caught the
second, which kept the data correct. But the `IntegrityError` came back out of
the service, and `channels/meta/webhook._process_events` catches per event,
logs `event_failed`, and moves on. So the message was dropped: not stored, not
answered, not notified, and nobody told. Sending "hi" and then the actual
question is the ordinary way for a customer to trigger it.

The measurement that found it also shows why one run proves nothing: the first
attempt raced twelve deliveries and reported zero errors, purely on
scheduling. The tests below race repeatedly for that reason.
"""

from __future__ import annotations

import threading

import pytest

from database.manager import utc_now_iso

# Module scope, before any fixture patches `database_manager`.
from backend.services.customer_service import customer_service  # noqa: E402


@pytest.fixture()
def wired(platform, alpha, monkeypatch):
    import sys

    from database.manager import DatabaseManager

    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    return {"manager": test_manager, "company_id": alpha["id"]}


def _arrive_together(company_id, channel, external_id, racers):
    """Deliver `racers` messages from one person at the same instant."""
    barrier = threading.Barrier(racers)
    failures = []

    def racer(index):
        barrier.wait()

        try:
            customer_service.upsert_from_channel(
                company_id=company_id,
                channel=channel,
                external_user_id=external_id,
                display_name="Simultaneous Customer",
            )
        except Exception as exc:  # noqa: BLE001 - anything escaping is the defect
            failures.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(racers)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=60)

    assert not any(t.is_alive() for t in threads), "a delivery never finished"

    return failures


def _identity_rows(manager, company_id, channel, external_id):
    with manager.tenant(company_id) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM customer_identities
                WHERE channel = ? AND external_user_id = ?
                """,
                (channel, external_id),
            ).fetchone()["n"]
        )


@pytest.mark.parametrize("attempt", range(4))
def test_simultaneous_first_contact_does_not_raise(wired, attempt):
    """Raced several times: one clean run here is scheduling, not evidence."""
    failures = _arrive_together(
        wired["company_id"], "messenger", f"psid-race-{attempt}", racers=12
    )

    assert not failures, (
        "a message from a new customer was lost — the inbound path logs this "
        f"and moves on, so nobody is ever told: {failures[:3]}"
    )


def test_simultaneous_first_contact_creates_exactly_one_customer(wired):
    _arrive_together(wired["company_id"], "messenger", "psid-single", racers=12)

    rows = _identity_rows(
        wired["manager"], wired["company_id"], "messenger", "psid-single"
    )

    assert rows == 1, (
        f"one person messaging twelve times at once became {rows} identities — "
        "their history is split and the team sees a stranger"
    )


def test_an_ordinary_first_contact_still_records_the_customer(wired):
    """The control. Both tests above pass on a platform that records nobody."""
    customer = customer_service.upsert_from_channel(
        company_id=wired["company_id"],
        channel="messenger",
        external_user_id="psid-ordinary",
        display_name="Ordinary Customer",
    )

    assert customer and customer.get("id"), "an ordinary first contact was not recorded"
    assert (
        _identity_rows(
            wired["manager"], wired["company_id"], "messenger", "psid-ordinary"
        )
        == 1
    )


def test_a_returning_customer_is_still_the_same_person(wired):
    """The other half: the transaction must not turn every message into a new
    record either."""
    for _ in range(5):
        customer_service.upsert_from_channel(
            company_id=wired["company_id"],
            channel="messenger",
            external_user_id="psid-returning",
            display_name="Returning Customer",
        )

    assert (
        _identity_rows(
            wired["manager"], wired["company_id"], "messenger", "psid-returning"
        )
        == 1
    )
