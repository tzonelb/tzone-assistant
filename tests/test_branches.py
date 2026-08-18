"""Branches a company can actually create.

The table audit found `branches` read in four places and written in none. Two
screens already rendered the list — a branch selector on every team member, and
a branch field when connecting a channel — and both were permanently empty,
because no endpoint, service or CLI command could insert a row. An owner could
open the dropdown, find one entry saying "All branches", and have no way to
add a second.

`tests/test_branch_ownership.py` covers the security half of the same finding:
a branch id belonging to another company. This file covers the half that made
the feature unreachable.

A company with one location needs none of this, which is why the field stays
optional everywhere it appears. A company with three shops needs to say which
one an employee works at and which page belongs to which, and until now it
could not.
"""

from __future__ import annotations

import sys

import pytest


ADMIN_PASSWORD = "AdminPass123456"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.roles  # noqa: F401
    import backend.services.auth_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.api.routes.roles" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, roles

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(roles.router)

    return TestClient(app)


def _token(client, service, company, email, role):
    user_id = service.create_user(email, ADMIN_PASSWORD, "Person")
    service.assign_user_to_company(user_id, company["id"], role)

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": ADMIN_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return response.json()["access_token"]


@pytest.fixture()
def owner(client, service, alpha):
    return _token(client, service, alpha, "owner@alpha.example.com", "owner")


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _create(client, token, name="Downtown", **extra):
    return client.post(
        "/api/admin/access/branches",
        headers=_headers(token),
        json={"name": name, **extra},
    )


# ------------------------------------------------------------------- creating


def test_a_company_can_create_a_branch(client, owner):
    created = _create(client, owner, "Downtown", code="DT", phone="+961 1 000000")

    assert created.status_code == 200, created.text

    listed = client.get(
        "/api/admin/access/branches", headers=_headers(owner)
    ).json()["branches"]

    assert [branch["name"] for branch in listed] == ["Downtown"]
    assert listed[0]["code"] == "DT"


def test_a_created_branch_appears_in_the_dropdown_the_screen_reads(client, owner):
    """The whole point of the finding. The Roles screen has always rendered
    `data.branches`; the list was empty because nothing could fill it."""
    _create(client, owner, "Downtown")

    overview = client.get(
        "/api/admin/access/overview", headers=_headers(owner)
    ).json()

    assert [branch["name"] for branch in overview["branches"]] == ["Downtown"]


def test_a_second_branch_with_the_same_name_is_refused(client, owner):
    """Two branches called "Main" in one company cannot be told apart in a
    dropdown afterwards."""
    _create(client, owner, "Main")
    duplicate = _create(client, owner, "  main  ")

    assert duplicate.status_code == 409


def test_two_companies_may_both_have_a_branch_called_main(client, service, alpha, beta):
    """The name is unique within a company, not across the platform. Anything
    else would let one company's naming reserve a word for everybody."""
    alpha_token = _token(client, service, alpha, "a@alpha.example.com", "owner")
    beta_token = _token(client, service, beta, "b@beta.example.com", "owner")

    assert _create(client, alpha_token, "Main").status_code == 200
    assert _create(client, beta_token, "Main").status_code == 200


# -------------------------------------------------------------------- editing


def test_a_branch_can_be_renamed(client, owner):
    branch_id = _create(client, owner, "Downtown").json()["branch_id"]

    updated = client.patch(
        f"/api/admin/access/branches/{branch_id}",
        headers=_headers(owner),
        json={"name": "Downtown East"},
    )

    assert updated.status_code == 200, updated.text

    listed = client.get(
        "/api/admin/access/branches", headers=_headers(owner)
    ).json()["branches"]

    assert listed[0]["name"] == "Downtown East"


def test_a_branch_can_be_disabled_without_being_deleted(client, owner):
    """A shop that closed for the season still owns its history."""
    branch_id = _create(client, owner, "Seaside").json()["branch_id"]

    client.patch(
        f"/api/admin/access/branches/{branch_id}",
        headers=_headers(owner),
        json={"status": "disabled"},
    )

    listed = client.get(
        "/api/admin/access/branches", headers=_headers(owner)
    ).json()["branches"]

    assert listed[0]["status"] == "disabled"


# ------------------------------------------------------------------- deleting


def test_deleting_a_branch_releases_the_team_members_in_it(
    client, service, owner, alpha
):
    """`company_users` declares the foreign key that does this, so the test is
    of the database's behaviour rather than of this endpoint's — which is why
    it is here: nothing else asserts that `PRAGMA foreign_keys` is on, and with
    it off this silently stops working."""
    branch_id = _create(client, owner, "Downtown").json()["branch_id"]

    role_id = next(
        role["id"]
        for role in client.get(
            "/api/admin/access/overview", headers=_headers(owner)
        ).json()["roles"]
        if role["code"] == "agent"
    )

    created = client.post(
        "/api/admin/access/users",
        headers=_headers(owner),
        json={
            "full_name": "New Person",
            "email": "new@alpha.example.com",
            "password": ADMIN_PASSWORD,
            "phone": None,
            "role_id": role_id,
            "branch_id": branch_id,
        },
    )
    assert created.status_code == 200, created.text

    assert client.delete(
        f"/api/admin/access/branches/{branch_id}", headers=_headers(owner)
    ).status_code == 200

    team = client.get(
        "/api/admin/access/overview", headers=_headers(owner)
    ).json()["users"]
    person = next(p for p in team if p["email"] == "new@alpha.example.com")

    assert person["branch_id"] is None


def test_deleting_a_branch_releases_the_channel_accounts_in_it(
    client, owner, alpha, platform
):
    """The half nothing else does.

    `channel_accounts.branch_id` declares no foreign key, so without the
    explicit release a connected page keeps pointing at a row that is gone —
    and the id can be handed to a different branch later, at which point the
    page belongs to somewhere it was never assigned.

    The first version of this file only tested `company_users`, which the
    database releases by itself, so the statement that does the real work could
    be deleted with every test still passing. Found by mutation.
    """
    import sys

    from backend.services.channel_account_service import channel_account_service

    branch_id = _create(client, owner, "Downtown").json()["branch_id"]

    account = channel_account_service.create_account(
        company_id=alpha["id"],
        channel="messenger",
        name="Alpha page",
        values={"branch_id": branch_id, "page_id": "PAGE-A"},
    )

    assert account["branch_id"] == branch_id

    assert client.delete(
        f"/api/admin/access/branches/{branch_id}", headers=_headers(owner)
    ).status_code == 200

    after = channel_account_service.get_account(alpha["id"], account["id"])

    assert after["branch_id"] is None, (
        "a connected page still points at a branch that no longer exists"
    )


# ------------------------------------------------------------------ isolation


def test_a_company_cannot_see_another_companys_branches(
    client, service, alpha, beta
):
    alpha_token = _token(client, service, alpha, "a@alpha.example.com", "owner")
    beta_token = _token(client, service, beta, "b@beta.example.com", "owner")

    _create(client, beta_token, "Beta Secret Warehouse")

    listed = client.get(
        "/api/admin/access/branches", headers=_headers(alpha_token)
    ).json()["branches"]

    assert listed == []


def test_a_company_cannot_edit_another_companys_branch(
    client, service, alpha, beta
):
    """Matched with the company rather than on the id alone. Fetching on the id
    and checking afterwards would still have read the other company's row."""
    alpha_token = _token(client, service, alpha, "a@alpha.example.com", "owner")
    beta_token = _token(client, service, beta, "b@beta.example.com", "owner")

    foreign = _create(client, beta_token, "Beta Secret Warehouse").json()["branch_id"]

    refused = client.patch(
        f"/api/admin/access/branches/{foreign}",
        headers=_headers(alpha_token),
        json={"name": "Taken"},
    )

    assert refused.status_code == 404


def test_a_company_cannot_delete_another_companys_branch(
    client, service, alpha, beta
):
    alpha_token = _token(client, service, alpha, "a@alpha.example.com", "owner")
    beta_token = _token(client, service, beta, "b@beta.example.com", "owner")

    foreign = _create(client, beta_token, "Beta Secret Warehouse").json()["branch_id"]

    assert client.delete(
        f"/api/admin/access/branches/{foreign}", headers=_headers(alpha_token)
    ).status_code == 404

    still_there = client.get(
        "/api/admin/access/branches", headers=_headers(beta_token)
    ).json()["branches"]

    assert len(still_there) == 1


# ----------------------------------------------------------------- permission


def test_an_ordinary_employee_cannot_manage_branches(client, service, alpha):
    """Same guard as roles and team members. Who works where is an
    administrator's decision."""
    agent_token = _token(client, service, alpha, "agent@alpha.example.com", "agent")

    assert _create(client, agent_token, "Downtown").status_code == 403


# --------------------------------------------------------------------- record


def test_creating_a_branch_is_recorded(client, owner, alpha):
    from backend.services.activity_service import Action, activity_service

    _create(client, owner, "Downtown")

    entries = activity_service.list_entries(company_id=alpha["id"], limit=50)
    items = entries["items"] if isinstance(entries, dict) else entries

    assert any(item["action"] == Action.BRANCH_CREATED for item in items)
