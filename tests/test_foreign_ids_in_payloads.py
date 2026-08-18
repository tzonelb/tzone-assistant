"""An id in a request body that points at another company's row.

This is the shape of the one cross-tenant leak this platform has actually had.
`channel_accounts.branch_id` was written straight from the payload, ids are
global in the control database, and three joins matched on the id alone — so
another company's branch *name* came back on this company's screen. The check
that would have stopped it was sitting in the same argument list, applied to
`department_id` and not to `branch_id`.

Path ids are covered by `test_no_module_leaks_across_companies.py`. This file
is about the other door, which is easier to miss: an id the caller *supplies*
for a field, rather than one that names the thing being fetched. A payload
field is not obviously an ownership question the way a URL is.

Only ids that resolve against the **control** database can be wrong this way.
An id pointing into the company's own encrypted file — `category_id`,
`customer_id`, `conversation_id` — cannot name another company's row, because
the query runs against a different file. Those are excluded deliberately, not
forgotten. What is left is the real list:

    appointments.staff_user_id   -> users / company_users
    appointments.branch_id       -> branches
    tasks.assigned_user_id       -> users / company_users
    team_chat member user ids    -> users / company_users
    scheduler.channel_account_id -> channel_accounts
    ai_teaching.channel_account_id -> channel_accounts
    roles: role_id, branch_id    -> roles, branches

Two separate questions are asked of each, because they fail differently and
the second is the one that bites:

1. Is the write refused?
2. If it is stored anyway, does the other company's **name** come back?

A stored foreign id with no name leaking is bad data. A name coming back is a
leak. Both are reported, and the assertions say which is which.
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
        ai_teaching, appointments, auth, channels, roles, scheduler,
        team_chat, tickets,
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

    for module in (auth, ai_teaching, appointments, channels, roles,
                   scheduler, team_chat):
        app.include_router(module.router)

    app.include_router(tickets.tasks_router)

    return TestClient(app, raise_server_exceptions=False)


def _employee(platform, company, app_client, email, name):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(email=email, password=PASSWORD, full_name=name)

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (company["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    return user_id


def _sign_in(app_client, company, email):
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
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def alpha_owner(platform, alpha, app_client):
    _employee(platform, alpha, app_client, "owner@alpha.example.com", "Alpha Owner")
    return _sign_in(app_client, alpha, "owner@alpha.example.com")


@pytest.fixture()
def beta_employee(platform, beta, app_client):
    """A real, active employee of Beta. Their name is the thing that must not
    surface anywhere in Alpha."""
    user_id = _employee(
        platform, beta, app_client, "spy@beta.example.com", "Beta Secret Employee"
    )
    return user_id


@pytest.fixture()
def beta_branch(platform, beta):
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        cursor = conn.execute(
            """
            INSERT INTO branches (company_id, name, code, status, created_at, updated_at)
            VALUES (?, 'Beta Secret Warehouse', 'BSW', 'active', ?, ?)
            """,
            (beta["id"], now, now),
        )
        conn.commit()

    return int(cursor.lastrowid)


@pytest.fixture()
def beta_channel_account(platform, beta):
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        cursor = conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id, status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'Beta Secret Page', 'BETA-PAGE-9', 'active', ?, ?)
            """,
            (beta["id"], now, now),
        )
        conn.commit()

    return int(cursor.lastrowid)


SECRET = "Beta Secret"


def _has_secret(payload) -> bool:
    return SECRET in str(payload)


# --------------------------------------------------------------------- tasks


def test_a_task_cannot_be_assigned_to_another_companys_employee(
    app_client, alpha_owner, beta_employee
):
    created = app_client.post(
        "/api/tasks",
        headers=alpha_owner,
        json={"title": "Spy task", "assigned_user_id": beta_employee},
    )

    if created.status_code in (200, 201):
        listing = app_client.get("/api/tasks", headers=alpha_owner)

        assert not _has_secret(listing.json()), (
            "CROSS-TENANT LEAK: Beta's employee name came back on Alpha's task "
            f"list\n{listing.text}"
        )

        pytest.fail(
            "A task was assigned to an employee of another company. The name "
            "does not leak, but the row points at a stranger and the "
            "assignee filter will never match anyone."
        )

    assert created.status_code in (400, 403, 404, 409, 422), created.text


# -------------------------------------------------------------- appointments


def test_an_appointment_cannot_be_booked_for_another_companys_staff(
    app_client, alpha_owner, beta_employee
):
    created = app_client.post(
        "/api/appointments",
        headers=alpha_owner,
        json={
            "staff_user_id": beta_employee,
            "starts_at": "2030-01-01T10:00:00Z",
            "ends_at": "2030-01-01T11:00:00Z",
            "title": "Spy appointment",
        },
    )

    if created.status_code in (200, 201):
        listing = app_client.get("/api/appointments", headers=alpha_owner)

        assert not _has_secret(listing.json()), (
            "CROSS-TENANT LEAK: Beta's employee name came back on Alpha's "
            f"appointment list\n{listing.text}"
        )

        pytest.fail(
            "An appointment was booked against an employee of another company. "
            "The double-booking guarantee is computed per company, so this "
            "staff member has a calendar in a company they do not work for."
        )

    assert created.status_code in (400, 403, 404, 409, 422), created.text


def test_an_appointment_cannot_name_another_companys_branch(
    app_client, alpha_owner, platform, alpha, beta_branch
):
    staff = _employee(
        platform, alpha, app_client, "staff@alpha.example.com", "Alpha Staff"
    )

    created = app_client.post(
        "/api/appointments",
        headers=alpha_owner,
        json={
            "staff_user_id": staff,
            "starts_at": "2030-02-01T10:00:00Z",
            "ends_at": "2030-02-01T11:00:00Z",
            "title": "Branch appointment",
            "branch_id": beta_branch,
        },
    )

    if created.status_code in (200, 201):
        listing = app_client.get("/api/appointments", headers=alpha_owner)

        assert not _has_secret(listing.json()), (
            "CROSS-TENANT LEAK: Beta's branch name came back on Alpha's "
            f"appointment list\n{listing.text}"
        )

        pytest.fail(
            "An appointment stored a branch id belonging to another company — "
            "the same defect that was closed for channel accounts and team "
            "memberships."
        )

    assert created.status_code in (400, 403, 404, 409, 422), created.text


# ----------------------------------------------------------------- team chat


def test_another_companys_employee_cannot_be_added_to_a_chat_channel(
    app_client, alpha_owner, beta_employee
):
    channel = app_client.post(
        "/api/team-chat/channels",
        headers=alpha_owner,
        json={"name": "alpha-room", "topic": "internal"},
    )
    assert channel.status_code in (200, 201), channel.text

    channel_id = channel.json().get("id") or channel.json()["channel"]["id"]

    added = app_client.post(
        f"/api/team-chat/channels/{channel_id}/members",
        headers=alpha_owner,
        json={"user_id": beta_employee},
    )

    if added.status_code in (200, 201):
        members = app_client.get(
            f"/api/team-chat/channels/{channel_id}/members", headers=alpha_owner
        )

        assert not _has_secret(members.json()), (
            "CROSS-TENANT LEAK: Beta's employee name appears in Alpha's chat "
            f"channel member list\n{members.text}"
        )

        pytest.fail(
            "An employee of another company was added to this company's chat "
            "channel."
        )

    assert added.status_code in (400, 403, 404, 409, 422), added.text


def test_a_chat_channel_cannot_be_created_with_another_companys_employee(
    app_client, alpha_owner, beta_employee
):
    created = app_client.post(
        "/api/team-chat/channels",
        headers=alpha_owner,
        json={
            "name": "alpha-private",
            "topic": "internal",
            "is_private": True,
            "member_user_ids": [beta_employee],
        },
    )

    if created.status_code in (200, 201):
        channel_id = created.json().get("id") or created.json()["channel"]["id"]
        members = app_client.get(
            f"/api/team-chat/channels/{channel_id}/members", headers=alpha_owner
        )

        assert not _has_secret(members.json()), (
            "CROSS-TENANT LEAK: Beta's employee name appears in a channel "
            f"Alpha created\n{members.text}"
        )

        pytest.fail("A channel was created with a member from another company.")

    assert created.status_code in (400, 403, 404, 409, 422), created.text


# ----------------------------------------------------------------- scheduler


def test_a_post_cannot_be_scheduled_on_another_companys_page(
    app_client, alpha_owner, beta_channel_account
):
    """The worst of this family if it were open: the post would publish
    publicly on a page Alpha does not own."""
    created = app_client.post(
        "/api/scheduler",
        headers=alpha_owner,
        json={
            "channel": "messenger",
            "body": "Posted by a stranger",
            "scheduled_for": "2030-03-01T10:00:00Z",
            "channel_account_id": beta_channel_account,
        },
    )

    assert created.status_code not in (200, 201), (
        "CROSS-TENANT WRITE: Alpha scheduled a post onto Beta's page\n"
        f"{created.text}"
    )
    assert created.status_code in (400, 403, 404, 409, 422), created.text


# --------------------------------------------------------------- ai teaching


def test_a_bot_profile_cannot_be_bound_to_another_companys_page(
    app_client, alpha_owner, beta_channel_account
):
    created = app_client.post(
        "/api/ai-teaching/profiles",
        headers=alpha_owner,
        json={"name": "Spy persona", "channel_account_id": beta_channel_account},
    )

    assert created.status_code not in (200, 201), (
        "CROSS-TENANT WRITE: Alpha bound a bot profile to Beta's page\n"
        f"{created.text}"
    )


# --------------------------------------------------------------------- roles


def test_a_member_cannot_be_given_another_companys_role(
    app_client, alpha_owner, platform, beta
):
    with platform["manager"].control() as conn:
        beta_role = int(
            conn.execute(
                "SELECT id FROM roles WHERE company_id = ? LIMIT 1", (beta["id"],)
            ).fetchone()["id"]
        )

    created = app_client.post(
        "/api/admin/access/users",
        headers=alpha_owner,
        json={
            "email": "newhire@alpha.example.com",
            "password": "AnotherPass123!",
            "full_name": "New Hire",
            "role_id": beta_role,
        },
    )

    assert created.status_code not in (200, 201), (
        "CROSS-TENANT WRITE: Alpha gave its employee a role defined by Beta\n"
        f"{created.text}"
    )


# ------------------------------------------------------- the other half


"""Everything above proves an id from another company is refused. A check that
refused every id would pass all of it and break the four features involved, so
each fix is paired with the case it must still allow. This is the half that is
easy to skip and the half that a user notices."""


def test_a_task_can_still_be_assigned_to_a_colleague(
    app_client, alpha_owner, platform, alpha
):
    colleague = _employee(
        platform, alpha, app_client, "colleague@alpha.example.com", "Alpha Colleague"
    )

    created = app_client.post(
        "/api/tasks",
        headers=alpha_owner,
        json={"title": "Real task", "assigned_user_id": colleague},
    )

    assert created.status_code in (200, 201), created.text


def test_an_appointment_can_still_be_booked_for_our_own_staff(
    app_client, alpha_owner, platform, alpha
):
    staff = _employee(
        platform, alpha, app_client, "dentist@alpha.example.com", "Alpha Dentist"
    )

    created = app_client.post(
        "/api/appointments",
        headers=alpha_owner,
        json={
            "staff_user_id": staff,
            "starts_at": "2030-04-01T10:00:00Z",
            "ends_at": "2030-04-01T11:00:00Z",
            "title": "Real appointment",
        },
    )

    assert created.status_code in (200, 201), created.text


def test_an_appointment_can_still_name_our_own_branch(
    app_client, alpha_owner, platform, alpha
):
    from database.manager import utc_now_iso

    staff = _employee(
        platform, alpha, app_client, "nurse@alpha.example.com", "Alpha Nurse"
    )
    now = utc_now_iso()

    with platform["manager"].control() as conn:
        cursor = conn.execute(
            """
            INSERT INTO branches (company_id, name, code, status, created_at, updated_at)
            VALUES (?, 'Alpha Downtown', 'ADT', 'active', ?, ?)
            """,
            (alpha["id"], now, now),
        )
        conn.commit()

    created = app_client.post(
        "/api/appointments",
        headers=alpha_owner,
        json={
            "staff_user_id": staff,
            "starts_at": "2030-05-01T10:00:00Z",
            "ends_at": "2030-05-01T11:00:00Z",
            "title": "Branch appointment",
            "branch_id": int(cursor.lastrowid),
        },
    )

    assert created.status_code in (200, 201), created.text


def test_an_appointment_without_a_branch_is_still_allowed(
    app_client, alpha_owner, platform, alpha
):
    """Optional means optional. Most companies have one location and never
    touch this field."""
    staff = _employee(
        platform, alpha, app_client, "solo@alpha.example.com", "Alpha Solo"
    )

    created = app_client.post(
        "/api/appointments",
        headers=alpha_owner,
        json={
            "staff_user_id": staff,
            "starts_at": "2030-06-01T10:00:00Z",
            "ends_at": "2030-06-01T11:00:00Z",
            "title": "No branch",
        },
    )

    assert created.status_code in (200, 201), created.text


def test_a_channel_can_still_be_created_with_colleagues(
    app_client, alpha_owner, platform, alpha
):
    first = _employee(
        platform, alpha, app_client, "one@alpha.example.com", "Alpha One"
    )
    second = _employee(
        platform, alpha, app_client, "two@alpha.example.com", "Alpha Two"
    )

    created = app_client.post(
        "/api/team-chat/channels",
        headers=alpha_owner,
        json={
            "name": "alpha-team",
            "topic": "internal",
            "is_private": True,
            "member_user_ids": [first, second],
        },
    )

    assert created.status_code in (200, 201), created.text

    channel_id = created.json().get("id") or created.json()["channel"]["id"]
    members = app_client.get(
        f"/api/team-chat/channels/{channel_id}/members", headers=alpha_owner
    )

    ids = {int(item["user_id"]) for item in members.json()["items"]}

    assert {first, second} <= ids, (
        f"the invited colleagues are not in the channel: {members.text}"
    )


def test_a_channel_with_no_invitees_still_works(app_client, alpha_owner):
    created = app_client.post(
        "/api/team-chat/channels",
        headers=alpha_owner,
        json={"name": "alpha-solo", "topic": "notes"},
    )

    assert created.status_code in (200, 201), created.text
