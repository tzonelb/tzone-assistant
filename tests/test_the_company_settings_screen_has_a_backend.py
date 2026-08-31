"""The three endpoints the restored Company Settings and Settings screens need.

The design's screens were ported without the API behind them once already, and
the symptom was invisible: the page built, the lint passed, and Billing said
"No active subscription yet" to a company that had one — because nothing was
answering. So each of these is tested from the route, not from the service, and
each is tested against *two* companies, because a single-company check cannot
see the failure that actually matters here.

Three surfaces:

* `/api/billing/**` — the company's own view of its plan, and the plan-change
  request that stands in for an online payment nobody has wired up.
* `/api/support-tickets` — a company reporting a platform fault to T-ZONE,
  which is not the same table as `/api/tickets` and must not become it.
* `/api/notification-preferences` — one employee's own delivery choices, which
  must not be settable for anybody else.
"""

from __future__ import annotations

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.billing  # noqa: F401
    import backend.api.routes.notification_preferences  # noqa: F401
    import backend.api.routes.support_tickets  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.billing_service  # noqa: F401
    import backend.services.notification_preference_service  # noqa: F401
    # Imported here rather than inside the one test that uses it, and that is
    # the whole point of doing the imports before `original` is read: a module
    # first imported *inside* the monkeypatched window binds this test's
    # temporary manager at import time, and nothing ever unbinds it — the
    # directory is deleted, the module keeps the handle, and every later test
    # file that expects to rebind it finds a manager it does not recognise.
    import backend.services.notification_service  # noqa: F401
    import backend.services.support_ticket_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.billing_service" in rebound
    assert "backend.services.support_ticket_service" in rebound
    assert "backend.services.notification_preference_service" in rebound
    assert "backend.services.notification_service" in rebound

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import (
        auth,
        billing,
        notification_preferences,
        support_tickets,
    )

    app = FastAPI()

    for module in (auth, billing, support_tickets, notification_preferences):
        app.include_router(module.router)

    return TestClient(app)


def _owner(platform, company, app_client, email):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name="Owner"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (company["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (
                company_id, user_id, role_id, status, created_at
            )
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    response = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


@pytest.fixture()
def alpha_owner(platform, alpha, app_client):
    return _owner(platform, alpha, app_client, "owner@alpha.example.com")


@pytest.fixture()
def beta_owner(platform, beta, app_client):
    return _owner(platform, beta, app_client, "owner@beta.example.com")


def _subscribe(platform, company_id, code="business"):
    """The row `platform_service.assign_plan` writes, without the console."""
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        now = utc_now_iso()
        plan = conn.execute(
            "SELECT id FROM plans WHERE code = ?", (code,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO subscriptions (
                company_id, plan_id, status, starts_at, expires_at,
                auto_renew, created_at, updated_at
            )
            VALUES (?, ?, 'active', ?, NULL, 1, ?, ?)
            """,
            (company_id, int(plan["id"]), now, now, now),
        )
        conn.commit()

    return int(plan["id"])


# ----------------------------------------------------------------- billing


def test_billing_reports_the_plan_a_company_is_actually_on(
    platform, alpha, app_client, alpha_owner
):
    plan_id = _subscribe(platform, alpha["id"])

    response = app_client.get(
        "/api/billing/subscription", headers=alpha_owner["headers"]
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["has_subscription"] is True
    assert body["plan_id"] == plan_id
    assert body["plan_code"] == "business"
    # The price is the reason this endpoint exists separately from
    # /api/dashboard/subscription, which strips it deliberately.
    assert body["price_monthly"] == 49
    # The owner is one active user of the plan's ten.
    assert body["users"] == {"used": 1, "max": 10}


def test_a_company_with_no_plan_is_told_so_rather_than_crashing(
    app_client, alpha_owner
):
    """The empty state is a shape the screen has a branch for, not a 500."""
    response = app_client.get(
        "/api/billing/subscription", headers=alpha_owner["headers"]
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["has_subscription"] is False
    assert body["plan_name"] is None


def test_a_plan_change_is_a_request_and_changes_nothing(
    platform, alpha, app_client, alpha_owner
):
    """The property the whole billing screen rests on.

    Payment is not wired up, so requesting a plan must record a request the
    operator reviews. A company that could move itself onto a larger plan would
    be granting itself the allowances that come with it.
    """
    _subscribe(platform, alpha["id"], code="starter")

    with platform["manager"].control() as conn:
        enterprise = int(
            conn.execute(
                "SELECT id FROM plans WHERE code = 'enterprise'"
            ).fetchone()["id"]
        )

    response = app_client.post(
        "/api/billing/requests",
        headers=alpha_owner["headers"],
        json={"plan_id": enterprise, "note": "Whish transfer #12"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"

    still = app_client.get(
        "/api/billing/subscription", headers=alpha_owner["headers"]
    ).json()
    assert still["plan_code"] == "starter", (
        "requesting a plan moved the company onto it"
    )


def test_requesting_the_same_plan_twice_leaves_one_thing_to_review(
    platform, alpha, app_client, alpha_owner
):
    """An owner clicking twice must not queue the operator two identical rows."""
    plan_id = _subscribe(platform, alpha["id"])

    for _ in range(2):
        assert (
            app_client.post(
                "/api/billing/requests",
                headers=alpha_owner["headers"],
                json={"plan_id": plan_id, "note": "Renewal request"},
            ).status_code
            == 200
        )

    listed = app_client.get(
        "/api/billing/requests", headers=alpha_owner["headers"]
    ).json()["requests"]

    assert len(listed) == 1


def test_a_plan_that_does_not_exist_is_refused(app_client, alpha_owner):
    response = app_client.post(
        "/api/billing/requests",
        headers=alpha_owner["headers"],
        json={"plan_id": 99999, "note": ""},
    )
    assert response.status_code == 400


def test_one_company_never_sees_another_companys_billing_requests(
    platform, alpha, beta, app_client, alpha_owner, beta_owner
):
    plan_id = _subscribe(platform, alpha["id"])
    _subscribe(platform, beta["id"])

    app_client.post(
        "/api/billing/requests",
        headers=alpha_owner["headers"],
        json={"plan_id": plan_id, "note": "alpha only"},
    )

    beta_sees = app_client.get(
        "/api/billing/requests", headers=beta_owner["headers"]
    ).json()["requests"]

    assert beta_sees == [], "beta read alpha's plan-change requests"


# ---------------------------------------------------------- support tickets


def test_a_support_ticket_is_filed_and_read_back(app_client, alpha_owner):
    created = app_client.post(
        "/api/support-tickets",
        headers=alpha_owner["headers"],
        json={
            "subject": "Instagram replies are delayed",
            "description": "Ten minutes on Instagram, immediate on WhatsApp.",
            "priority": "high",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "open"

    listed = app_client.get(
        "/api/support-tickets", headers=alpha_owner["headers"]
    ).json()["tickets"]

    assert [ticket["subject"] for ticket in listed] == [
        "Instagram replies are delayed"
    ]
    assert listed[0]["priority"] == "high"


def test_an_unknown_priority_is_refused_rather_than_quietly_normalised(
    app_client, alpha_owner
):
    """A priority silently rewritten to "normal" reads back on the screen as
    the urgency the reporter chose, and the one person who needed it escalated
    is the one who cannot tell it was not."""
    response = app_client.post(
        "/api/support-tickets",
        headers=alpha_owner["headers"],
        json={"subject": "s", "description": "d", "priority": "catastrophic"},
    )
    assert response.status_code == 400


def test_one_company_never_sees_another_companys_support_tickets(
    app_client, alpha_owner, beta_owner
):
    app_client.post(
        "/api/support-tickets",
        headers=alpha_owner["headers"],
        json={"subject": "alpha only", "description": "d", "priority": "low"},
    )

    beta_sees = app_client.get(
        "/api/support-tickets", headers=beta_owner["headers"]
    ).json()["tickets"]

    assert beta_sees == [], "beta read alpha's tickets to T-ZONE"


# -------------------------------------------------- notification preferences


def test_an_employee_who_has_chosen_nothing_gets_everything(
    app_client, alpha_owner
):
    """Somebody who has never opened the screen keeps what they had before it
    existed."""
    body = app_client.get(
        "/api/notification-preferences", headers=alpha_owner["headers"]
    ).json()

    assert body == {
        "notify_new_message": "all",
        "notify_ai_escalation": True,
        "notify_mentions": True,
        "notify_tasks": True,
    }


def test_a_partial_save_does_not_reset_the_keys_it_did_not_send(
    app_client, alpha_owner
):
    """The screen sends what it drew. A save that reset the rest would silently
    undo a choice made on an earlier visit."""
    app_client.put(
        "/api/notification-preferences",
        headers=alpha_owner["headers"],
        json={"notify_tasks": False},
    )
    app_client.put(
        "/api/notification-preferences",
        headers=alpha_owner["headers"],
        json={"notify_new_message": "none"},
    )

    body = app_client.get(
        "/api/notification-preferences", headers=alpha_owner["headers"]
    ).json()

    assert body["notify_tasks"] is False, "the second save undid the first"
    assert body["notify_new_message"] == "none"
    assert body["notify_mentions"] is True


def test_a_value_the_gate_cannot_obey_is_refused(app_client, alpha_owner):
    response = app_client.put(
        "/api/notification-preferences",
        headers=alpha_owner["headers"],
        json={"notify_new_message": "sometimes"},
    )
    assert response.status_code == 400


def test_one_employees_choices_are_not_anothers(
    app_client, alpha_owner, beta_owner
):
    app_client.put(
        "/api/notification-preferences",
        headers=alpha_owner["headers"],
        json={"notify_mentions": False},
    )

    beta_sees = app_client.get(
        "/api/notification-preferences", headers=beta_owner["headers"]
    ).json()

    assert beta_sees["notify_mentions"] is True


def test_a_muted_category_is_actually_not_delivered(
    platform, alpha, app_client, alpha_owner, monkeypatch
):
    """The half that makes the screen mean something.

    A preference that is stored and never consulted is a switch the employee
    watches themselves set and that changes nothing — which is the exact defect
    `tests/test_notification_preferences.py` was written about for the
    company-level switches.
    """
    from backend.services.notification_service import notification_service

    monkeypatch.setattr(
        "backend.services.module_gate.module_gate.enabled",
        lambda company_id, module: True,
    )

    app_client.put(
        "/api/notification-preferences",
        headers=alpha_owner["headers"],
        json={"notify_tasks": False},
    )

    muted = notification_service.create(
        company_id=alpha["id"],
        notification_type="task_assigned",
        title="A task was assigned to you",
        recipient_user_id=alpha_owner["user_id"],
    )
    assert muted == {}, "a muted category was still written"

    # And a category they left on still arrives, so the gate suppresses rather
    # than silences everything.
    kept = notification_service.create(
        company_id=alpha["id"],
        notification_type="mention",
        title="A colleague mentioned you",
        recipient_user_id=alpha_owner["user_id"],
    )
    assert kept, "an unmuted category was suppressed"
