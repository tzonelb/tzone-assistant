"""When the subscription ends, the company stops working until it renews.

The owner's decision, taken explicitly. Before it, an expired subscription did
nothing at all: `plan_service.is_active` was computed, displayed on a screen,
and consulted by no code that could refuse anything. A company could stop
paying and carry on for ever, and the operator's only lever was suspension —
a much heavier act that reads to a customer as an accusation rather than an
invoice.

"Stops working" is four separate decisions, and this file holds each one
separately because getting any of them backwards is its own kind of harm:

* the screens refuse, with `402` and not `403`
* the assistant stops answering customers — the part that makes it real
* customers' messages are still saved, because a customer owes nobody anything
* the owner can still sign in and reach the subscription screen, because a
  company locked out of the page explaining the lock cannot act on it

And the half that is easy to forget: renewing brings everything back, at once,
without waiting for a cache to expire.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import (
        auth, catalogue, conversations, customers, dashboard, knowledge,
        team_chat, tickets,
    )
    from backend.services.module_access import require_active_subscription
    from backend.services.subscription_gate import subscription_gate

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    assert (
        getattr(sys.modules["backend.services.auth_service"], "database_manager", None)
        is test_manager
    )

    # The gate caches per company for thirty seconds. Across tests that would
    # carry one test's answer into the next.
    subscription_gate.invalidate()

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    paid = [Depends(require_active_subscription)]

    # Mirrors main.py: every module router carries the gate, the dashboard does
    # not. Registered here rather than importing `main` so that this file tests
    # the rule and not one particular wiring of it — the wiring itself is
    # checked separately, below, against main.py's own source.
    app.include_router(auth.router)
    app.include_router(dashboard.router)

    for module in (catalogue, conversations, customers, knowledge, team_chat):
        app.include_router(module.router, dependencies=paid)

    app.include_router(tickets.tasks_router, dependencies=paid)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def owner(platform, alpha, app_client):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="owner@alpha.example.com", password=PASSWORD, full_name="Alpha Owner"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (alpha["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (alpha["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    response = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "owner@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


def _subscribe(platform, company_id, *, expires_in_days, grace_days=None):
    """Give the company a subscription with a chosen expiry."""
    from database.manager import utc_now_iso

    now = utc_now_iso()
    expires = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    ).isoformat()
    grace = (
        (datetime.now(timezone.utc) + timedelta(days=grace_days)).isoformat()
        if grace_days is not None
        else None
    )

    with platform["manager"].control() as conn:
        plan = conn.execute("SELECT id FROM plans LIMIT 1").fetchone()

        assert plan, "the platform has no plans to subscribe to"

        conn.execute(
            "UPDATE subscriptions SET status = 'replaced' WHERE company_id = ?",
            (company_id,),
        )
        conn.execute(
            """
            INSERT INTO subscriptions (
                company_id, plan_id, status, starts_at, expires_at,
                grace_period_until, auto_renew, created_at, updated_at
            )
            VALUES (?, ?, 'active', ?, ?, ?, 0, ?, ?)
            """,
            (company_id, int(plan["id"]), now, expires, grace, now, now),
        )
        conn.commit()

    from backend.services.subscription_gate import subscription_gate

    subscription_gate.invalidate(company_id)


# Every kind of screen, so the answer cannot depend on which one was tried.
SCREENS = [
    ("GET", "/api/catalogue/products", None),
    ("GET", "/api/knowledge", None),
    ("GET", "/api/customers", None),
    ("GET", "/api/tasks", None),
    ("GET", "/api/team-chat/channels", None),
    ("POST", "/api/knowledge", {"title": "New fact", "content_ar": "شي"}),
]


def _try_screens(app_client, owner):
    return {
        f"{method} {path}": app_client.request(
            method, path, headers=owner["headers"], json=payload
        ).status_code
        for method, path, payload in SCREENS
    }


# ------------------------------------------------------------------ while paid


def test_a_paid_company_works_normally(app_client, owner, platform, alpha):
    """The control, and the only thing standing between this file and a change
    that breaks every company on the platform."""
    _subscribe(platform, alpha["id"], expires_in_days=30)

    results = _try_screens(app_client, owner)

    assert all(code in (200, 201) for code in results.values()), (
        f"a paying company cannot use its own platform: {results}"
    )


def test_a_company_inside_its_grace_period_still_works(
    app_client, owner, platform, alpha
):
    """The operator's lever for a late payment, and it already existed —
    `grace_period_until` was read by `is_active` before any of this. Nothing
    here invents a second grace period, and this checks the existing one was
    not walked past."""
    _subscribe(platform, alpha["id"], expires_in_days=-3, grace_days=7)

    results = _try_screens(app_client, owner)

    assert all(code in (200, 201) for code in results.values()), (
        f"a company inside its grace period was cut off: {results}"
    )


def test_a_subscription_with_no_expiry_date_never_lapses(
    app_client, owner, platform, alpha
):
    """The console's own form says a blank expiry means it does not expire, and
    a previous version of `is_active` returned False for exactly that case."""
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        plan = conn.execute("SELECT id FROM plans LIMIT 1").fetchone()
        conn.execute(
            "UPDATE subscriptions SET status = 'replaced' WHERE company_id = ?",
            (alpha["id"],),
        )
        conn.execute(
            """
            INSERT INTO subscriptions (
                company_id, plan_id, status, starts_at, expires_at,
                auto_renew, created_at, updated_at
            )
            VALUES (?, ?, 'active', ?, NULL, 0, ?, ?)
            """,
            (alpha["id"], int(plan["id"]), now, now, now),
        )
        conn.commit()

    from backend.services.subscription_gate import subscription_gate

    subscription_gate.invalidate(alpha["id"])

    results = _try_screens(app_client, owner)

    assert all(code in (200, 201) for code in results.values()), (
        f"a company set up never to expire was cut off: {results}"
    )


# ---------------------------------------------------------------- once lapsed


def test_every_screen_refuses_once_the_subscription_has_ended(
    app_client, owner, platform, alpha
):
    _subscribe(platform, alpha["id"], expires_in_days=-1)

    results = _try_screens(app_client, owner)

    still_open = {name: code for name, code in results.items() if code < 400}

    assert not still_open, (
        f"a lapsed company can still use: {still_open}"
    )


def test_the_refusal_says_payment_and_not_forbidden(
    app_client, owner, platform, alpha
):
    """`403` tells somebody they are not allowed. This company is allowed and
    has not paid, and the employee reading the message is usually not the
    person who pays."""
    _subscribe(platform, alpha["id"], expires_in_days=-1)

    response = app_client.get("/api/catalogue/products", headers=owner["headers"])

    assert response.status_code == 402, (
        f"the refusal came back as {response.status_code}, not 402 Payment "
        f"Required:\n{response.text}"
    )
    assert "subscription" in response.text.lower()


def test_the_owner_can_still_sign_in_and_see_why(
    app_client, owner, platform, alpha
):
    """A company locked out of the page that explains the lock cannot act on
    it, and prompting an action is the whole point of the pause."""
    _subscribe(platform, alpha["id"], expires_in_days=-1)

    signed_in = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "owner@alpha.example.com",
            "password": PASSWORD,
        },
    )

    assert signed_in.status_code == 200, (
        f"a lapsed company cannot even sign in:\n{signed_in.text}"
    )

    subscription = app_client.get(
        "/api/dashboard/subscription", headers=owner["headers"]
    )

    assert subscription.status_code == 200, (
        f"the subscription screen is paused too, so nobody can find out why "
        f"or renew:\n{subscription.text}"
    )
    assert subscription.json().get("active") is False


# -------------------------------------------------------------- the assistant


def _inbound(company_id, text="Hello"):
    from channels.inbound import process_inbound_event

    return process_inbound_event(
        company_id=company_id,
        event={
            "channel": "messenger",
            "user_id": "cust-1",
            "text": text,
            "message_id": f"mid-{text}",
        },
    )


def test_the_assistant_stops_answering(platform, alpha, monkeypatch):
    """The part that makes the policy real.

    Screens nobody can open is an inconvenience. An assistant that keeps
    replying is the service still being delivered, for free, with no reason to
    renew.
    """
    import sys

    from database.manager import DatabaseManager

    import database.manager as manager_module
    import channels.inbound  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    scheduled: list = []

    import channels.inbound as inbound

    monkeypatch.setattr(
        inbound,
        "schedule_smart_reply",
        lambda **kwargs: scheduled.append(kwargs) or {"queued": True},
    )

    _subscribe(platform, alpha["id"], expires_in_days=-1)

    result = _inbound(alpha["id"])

    assert not scheduled, "the assistant was asked to reply for a lapsed company"
    assert result.get("reason") == "subscription_lapsed", result


def test_the_customers_message_is_still_saved(platform, alpha, monkeypatch):
    """A customer owes nobody anything, and a company that renews on Thursday
    must find Tuesday's messages waiting rather than a hole."""
    import sys

    from database.manager import DatabaseManager

    import database.manager as manager_module
    import channels.inbound as inbound

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    _subscribe(platform, alpha["id"], expires_in_days=-1)

    _inbound(alpha["id"], text="I would like to order")

    with platform["manager"].tenant(alpha["id"]) as conn:
        rows = conn.execute(
            "SELECT body FROM messages WHERE direction = 'in'"
        ).fetchall()

    assert [row["body"] for row in rows] == ["I would like to order"], (
        "a customer's message was dropped because their supplier had not paid"
    )


def test_the_assistant_answers_again_after_renewal(platform, alpha, monkeypatch):
    import sys

    from database.manager import DatabaseManager

    import database.manager as manager_module
    import channels.inbound as inbound

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    scheduled: list = []
    monkeypatch.setattr(
        inbound,
        "schedule_smart_reply",
        lambda **kwargs: scheduled.append(kwargs) or {"queued": True},
    )

    _subscribe(platform, alpha["id"], expires_in_days=-1)
    _inbound(alpha["id"], text="first")

    assert not scheduled

    _subscribe(platform, alpha["id"], expires_in_days=30)
    _inbound(alpha["id"], text="second")

    assert scheduled, "renewing did not bring the assistant back"


# ------------------------------------------------------------------- renewal


def test_renewing_takes_effect_immediately(app_client, owner, platform, alpha):
    """Not after the cache expires.

    Thirty seconds is exactly when an operator is watching the screen, having
    just told a customer they are back on. "I renewed it and nothing happened"
    is the support call this prevents.
    """
    from backend.services.platform_service import platform_service

    _subscribe(platform, alpha["id"], expires_in_days=-1)

    assert (
        app_client.get(
            "/api/catalogue/products", headers=owner["headers"]
        ).status_code
        == 402
    )

    with platform["manager"].control() as conn:
        plan_code = conn.execute("SELECT code FROM plans LIMIT 1").fetchone()["code"]

    platform_service.assign_plan(
        company_id=alpha["id"],
        plan_code=plan_code,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        actor_user_id=None,
    )

    assert (
        app_client.get(
            "/api/catalogue/products", headers=owner["headers"]
        ).status_code
        == 200
    ), "the workspace stayed paused after the operator renewed it"


# ------------------------------------------------------- the wiring itself


def test_every_module_router_carries_the_gate():
    """The rule, checked against main.py rather than against this file's app.

    The gates live at `include_router` so a router added later cannot forget
    them. That only holds while every registration goes through the helper —
    one `include_router(..., dependencies=_module(...))` written out by hand
    would be a module that never asks about the bill, and it would look exactly
    like the others.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "main.py").read_text()

    registrations = re.findall(
        r"app\.include_router\(\s*([a-z_]+)\.[a-z_]*router,\s*dependencies=(\w+)\(",
        source,
    )

    assert registrations, "no gated router registrations found — has main.py moved?"

    ungated = [
        f"{name} via {helper}"
        for name, helper in registrations
        if helper not in {"_module", "_module_unpaid_too"}
    ]

    assert not ungated, f"a router is registered with an unknown helper: {ungated}"

    exempt = [name for name, helper in registrations if helper == "_module_unpaid_too"]

    assert exempt == ["dashboard"], (
        f"the only router a lapsed company may keep is the dashboard, which "
        f"carries the subscription screen. Currently exempt: {exempt}"
    )
