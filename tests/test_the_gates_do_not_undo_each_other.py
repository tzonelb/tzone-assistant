"""Three separate reasons a company may be stopped, and none of them may lift
another.

The platform can refuse to act for a company for three unrelated reasons: the
module is switched off, the bill is unpaid, or an operator suspended it. Each
has its own gate, its own cache and its own reason string, which is right —
they answer different questions and drive different words.

The risk in having three is composition. Clearing one reason must not clear the
others: reinstating a suspended company whose subscription also lapsed must not
bring its assistant back, and renewing the subscription of a suspended company
must not either. A gate that reads "may this company operate" and finds one
green light is exactly how a company that is off gets switched on.

Each test here clears one reason while another still stands, and asserts the
company is still stopped.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from database.manager import DatabaseManager, utc_now_iso


def _wire(platform, monkeypatch):
    """Point every already-imported module at this test's manager, by type."""
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    return test_manager


@pytest.fixture(autouse=True)
def _clean_gates():
    """Module-level caches outlive a test. Empty all three at both ends."""
    from backend.services.company_gate import company_gate
    from backend.services.module_gate import module_gate
    from backend.services.subscription_gate import subscription_gate

    for gate in (company_gate, module_gate, subscription_gate):
        gate.invalidate()

    yield

    for gate in (company_gate, module_gate, subscription_gate):
        gate.invalidate()


def _set_status(platform, company_id, status):
    from backend.services.company_gate import company_gate

    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE companies SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now_iso(), int(company_id)),
        )
        conn.commit()

    company_gate.invalidate(company_id)


def _subscribe(platform, company_id, *, expires_in_days):
    """Give the company a subscription with a chosen expiry.

    Column-for-column the same insert as
    `tests/test_a_lapsed_subscription_stops_the_company.py`, deliberately: a
    second, differently-shaped seeding of the same table is how two tests come
    to disagree about what a lapsed company looks like.
    """
    from backend.services.subscription_gate import subscription_gate

    now = utc_now_iso()
    expires = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    ).isoformat()

    with platform["manager"].control() as conn:
        plan = conn.execute("SELECT id FROM plans LIMIT 1").fetchone()

        assert plan, "the platform has no plans to subscribe to"

        conn.execute(
            "UPDATE subscriptions SET status = 'replaced' WHERE company_id = ?",
            (int(company_id),),
        )
        conn.execute(
            """
            INSERT INTO subscriptions (
                company_id, plan_id, status, starts_at, expires_at,
                grace_period_until, auto_renew, created_at, updated_at
            )
            VALUES (?, ?, 'active', ?, ?, NULL, 0, ?, ?)
            """,
            (int(company_id), int(plan["id"]), now, expires, now, now),
        )
        conn.commit()

    subscription_gate.invalidate(company_id)


def _deliver(company_id, text):
    from channels.inbound import process_inbound_event

    return process_inbound_event(
        company_id=company_id,
        event={
            "channel": "messenger",
            "user_id": "cust-compose",
            "text": text,
            "message_id": f"mid-{text}",
        },
    )


@pytest.fixture()
def watched(platform, alpha, monkeypatch):
    """An inbound path whose one outward action is recorded rather than taken."""
    import channels.inbound as inbound

    _wire(platform, monkeypatch)

    scheduled: list = []
    monkeypatch.setattr(
        inbound,
        "schedule_smart_reply",
        lambda **kwargs: scheduled.append(kwargs) or {"queued": True},
    )

    # Positive control, once, here: everything below asserts an absence, and an
    # absence proves nothing unless the presence is demonstrated first.
    _deliver(alpha["id"], "control")

    assert len(scheduled) == 1, (
        "positive control: a healthy company did not schedule a reply, so no "
        "assertion in this file would mean anything"
    )

    scheduled.clear()

    return scheduled


def test_reinstating_does_not_pay_the_bill(platform, alpha, watched):
    """Suspended and unpaid. Lifting the suspension leaves the bill unpaid."""
    _subscribe(platform, alpha["id"], expires_in_days=-1)
    _set_status(platform, alpha["id"], "suspended")

    _deliver(alpha["id"], "both")

    assert not watched, "a company that is both suspended and unpaid answered"

    _set_status(platform, alpha["id"], "active")

    result = _deliver(alpha["id"], "reinstated")

    assert not watched, (
        "lifting the suspension brought the assistant back for a company whose "
        "subscription had lapsed"
    )
    assert result.get("reason") == "subscription_lapsed", result


def test_paying_the_bill_does_not_lift_the_suspension(platform, alpha, watched):
    """Unpaid and suspended. Renewing leaves the operator's decision standing."""
    _subscribe(platform, alpha["id"], expires_in_days=-1)
    _set_status(platform, alpha["id"], "suspended")

    _deliver(alpha["id"], "both")

    assert not watched

    _subscribe(platform, alpha["id"], expires_in_days=30)

    result = _deliver(alpha["id"], "renewed")

    assert not watched, (
        "renewing the subscription overrode an operator's suspension"
    )
    assert result.get("reason") == "company_suspended", result


def test_clearing_both_brings_the_company_back(platform, alpha, watched):
    """The other direction, so the tests above cannot pass by the company
    simply never working again."""
    _subscribe(platform, alpha["id"], expires_in_days=-1)
    _set_status(platform, alpha["id"], "suspended")

    _deliver(alpha["id"], "stopped")

    assert not watched

    _subscribe(platform, alpha["id"], expires_in_days=30)
    _set_status(platform, alpha["id"], "active")

    _deliver(alpha["id"], "back")

    assert len(watched) == 1, (
        "clearing every reason left the company stopped anyway"
    )


def test_a_suspended_company_is_stopped_whatever_its_modules_say(
    platform, alpha, watched
):
    """Modules are per-company switches the owner sets; suspension is the
    operator's. Every module being on must not speak for the operator."""
    from backend.services.module_gate import module_gate

    assert module_gate.enabled(alpha["id"], "conversations"), (
        "positive control: conversations was already off, so this test would "
        "pass without the suspension gate doing anything"
    )

    _set_status(platform, alpha["id"], "suspended")

    result = _deliver(alpha["id"], "modules all on")

    assert not watched, "every module being on overrode the suspension"
    assert result.get("reason") == "company_suspended", result
