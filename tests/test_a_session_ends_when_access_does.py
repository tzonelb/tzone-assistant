"""A token minted before access was taken away must stop working.

Every check in this codebase that decides whether somebody may do a thing runs
at the moment they ask. Whether they are still *allowed to be here at all* is
decided once, at sign-in, and then carried in a token that lives for hours.
Between those two facts is the gap this file is about.

The realistic case is not exotic. Somebody is let go on Tuesday morning. Their
manager opens Roles & Permissions and switches their membership to disabled —
which is what that screen is for, and what an administrator believes they have
just done. The employee's browser is still open, and the token in it was minted
at nine o'clock.

Three separate things can be taken away, and they are not the same thing:

* the **platform account** (`users.status`) — the person, everywhere
* the **membership** (`company_users.status`) — the person, in this company
* the **company** (`companies.status`) — everybody, at once

Only the first is checked when a token is validated. The other two are checked
by `require_permission`, which is on almost every route — but "almost" is the
whole question, because the routes that carry no permission are exactly the
ones with nothing to check: a person's own notifications.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import (
        auth, catalogue, conversations, customers, notifications, team_chat,
    )

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

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, catalogue, conversations, customers, notifications,
                   team_chat):
        app.include_router(module.router)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def leaver(platform, alpha, app_client):
    """An employee with a valid session, about to lose their access."""
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="leaver@alpha.example.com", password=PASSWORD, full_name="The Leaver"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'manager'",
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
            "email": "leaver@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


# Every kind of route the token could still be used on: one behind a view
# permission, one behind a manage permission, and one behind no permission at
# all. The third is the interesting one — nothing about it consults the
# membership, because there is no permission for it to consult.
DOORS = [
    ("GET", "/api/customers", None, "a read behind customers.view"),
    ("GET", "/api/catalogue/products", None, "a read behind catalogue.view"),
    (
        "POST",
        "/api/catalogue/products",
        {"name": "Written after leaving", "price": 1, "status": "active"},
        "a write behind catalogue.manage",
    ),
    ("GET", "/api/notifications", None, "a read behind no permission at all"),
    ("POST", "/api/notifications/read-all", {}, "a write behind no permission at all"),
]


def _try_every_door(app_client, headers):
    """What the token can still do. Returns the doors that opened."""
    open_doors = []

    for method, path, payload, label in DOORS:
        response = app_client.request(method, path, headers=headers, json=payload)

        if response.status_code in (200, 201, 204):
            open_doors.append(f"{label} ({method} {path})")

    return open_doors


def test_the_doors_are_open_while_the_employee_is_employed(app_client, leaver):
    """The control.

    Every test below asserts that doors are shut. If they were shut for some
    unrelated reason — a wrong path, a missing permission — those tests would
    pass while proving nothing at all.
    """
    open_doors = _try_every_door(app_client, leaver["headers"])

    assert len(open_doors) == len(DOORS), (
        "an ordinary employee cannot use all the doors this file tests, so the "
        f"refusals below would prove nothing. Open: {open_doors}"
    )


def test_disabling_the_membership_closes_every_door(
    app_client, leaver, platform, alpha
):
    """The Tuesday-morning case, and the one an administrator believes they
    have just handled."""
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE company_users SET status = 'disabled'"
            " WHERE user_id = ? AND company_id = ?",
            (leaver["user_id"], alpha["id"]),
        )
        conn.commit()

    open_doors = _try_every_door(app_client, leaver["headers"])

    assert not open_doors, (
        "A disabled employee's existing token still works on:\n  "
        + "\n  ".join(open_doors)
        + "\n\nThe administrator switched their membership off and believes "
        "they are out; the browser they left open says otherwise."
    )


def test_disabling_the_platform_account_closes_every_door(
    app_client, leaver, platform
):
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE users SET status = 'disabled' WHERE id = ?", (leaver["user_id"],)
        )
        conn.commit()

    open_doors = _try_every_door(app_client, leaver["headers"])

    assert not open_doors, (
        "A disabled account's token still works on:\n  " + "\n  ".join(open_doors)
    )


def test_removing_the_membership_entirely_closes_every_door(
    app_client, leaver, platform, alpha
):
    """Deleting the row rather than disabling it — the other way an
    administrator might do the same thing."""
    with platform["manager"].control() as conn:
        conn.execute(
            "DELETE FROM company_users WHERE user_id = ? AND company_id = ?",
            (leaver["user_id"], alpha["id"]),
        )
        conn.commit()

    open_doors = _try_every_door(app_client, leaver["headers"])

    assert not open_doors, (
        "A token whose membership no longer exists still works on:\n  "
        + "\n  ".join(open_doors)
    )


def test_suspending_the_company_closes_every_door(app_client, leaver, platform, alpha):
    """The operator's own lever.

    Suspension is what the console does to a company that has stopped paying or
    is under investigation. If the people already signed in keep working, it
    suspends nothing until their tokens happen to expire.
    """
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE companies SET status = 'suspended' WHERE id = ?", (alpha["id"],)
        )
        conn.commit()

    open_doors = _try_every_door(app_client, leaver["headers"])

    assert not open_doors, (
        "A suspended company's employees keep working on:\n  "
        + "\n  ".join(open_doors)
        + "\n\nSuspension takes effect only when each token expires."
    )
