"""Tests for managing roles and users.

This screen had no tests at all, and all three of its writes were broken in
production:

* creating a role raised `IntegrityError` on every attempt, because the INSERT
  omitted `created_at` — and an `except Exception` around it reported "A role
  with this code already exists", so a completely broken button looked like a
  validation message;
* creating a user failed the same way, with no handler at all to disguise it;
* assigning permissions to a role silently discarded every one of them, because
  `INSERT OR IGNORE` suppresses a NOT NULL violation exactly as it suppresses a
  duplicate. Roles were created, reported success, and came back empty.

The third is the one worth remembering: it is the failure mode where the
mechanism *looks* like it worked. A test that only asserted a 200 would have
passed against the broken code, so these assert the row is really there.
"""

from __future__ import annotations

import pytest


ADMIN_PASSWORD = "AdminPass123456"
NEW_USER_PASSWORD = "NewUserPass1234"


@pytest.fixture()
def service(platform, monkeypatch):
    import sys

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


@pytest.fixture()
def admin_token(client, service, alpha):
    user_id = service.create_user("admin@alpha.example.com", ADMIN_PASSWORD, "Admin")
    service.assign_user_to_company(user_id, alpha["id"], "owner")

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "admin@alpha.example.com",
            "password": ADMIN_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------------------
# Roles
# ----------------------------------------------------------------------


def test_a_role_can_be_created(client, admin_token):
    response = client.post(
        "/api/admin/access/roles",
        headers=_bearer(admin_token),
        json={
            "name": "Support Lead",
            "code": "support_lead",
            "description": "Runs the inbox",
            "permission_codes": ["conversations.view", "conversations.reply"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["role_id"]


def test_a_created_role_actually_holds_its_permissions(client, admin_token):
    """The silent one. `INSERT OR IGNORE` swallowed the NOT NULL violation, so
    the role was created, success was reported, and it held nothing."""
    client.post(
        "/api/admin/access/roles",
        headers=_bearer(admin_token),
        json={
            "name": "Catalogue Editor",
            "code": "catalogue_editor",
            "description": None,
            "permission_codes": ["catalogue.view", "catalogue.manage"],
        },
    )

    overview = client.get(
        "/api/admin/access/overview", headers=_bearer(admin_token)
    ).json()

    created = next(
        role for role in overview["roles"] if role["code"] == "catalogue_editor"
    )

    assert sorted(created["permission_codes"]) == [
        "catalogue.manage",
        "catalogue.view",
    ]


def test_editing_a_role_replaces_its_permissions(client, admin_token):
    created = client.post(
        "/api/admin/access/roles",
        headers=_bearer(admin_token),
        json={
            "name": "Temp",
            "code": "temp_role",
            "description": None,
            "permission_codes": ["tasks.view"],
        },
    ).json()

    client.patch(
        f"/api/admin/access/roles/{created['role_id']}",
        headers=_bearer(admin_token),
        json={"permission_codes": ["appointments.view", "appointments.manage"]},
    )

    overview = client.get(
        "/api/admin/access/overview", headers=_bearer(admin_token)
    ).json()
    role = next(item for item in overview["roles"] if item["code"] == "temp_role")

    assert sorted(role["permission_codes"]) == [
        "appointments.manage",
        "appointments.view",
    ]


def test_a_duplicate_role_code_is_still_reported_as_a_duplicate(client, admin_token):
    """The 409 that the broad `except Exception` was pretending to produce has
    to keep working now that the handler only catches what it means."""
    payload = {
        "name": "Twice",
        "code": "twice",
        "description": None,
        "permission_codes": [],
    }

    assert client.post(
        "/api/admin/access/roles", headers=_bearer(admin_token), json=payload
    ).status_code == 200

    duplicate = client.post(
        "/api/admin/access/roles", headers=_bearer(admin_token), json=payload
    )

    assert duplicate.status_code == 409, duplicate.text
    assert "already exists" in duplicate.json()["detail"]


# ----------------------------------------------------------------------
# Users
# ----------------------------------------------------------------------


def test_a_user_can_be_created_and_appears_on_the_team(
    client, admin_token, service, alpha
):
    overview = client.get(
        "/api/admin/access/overview", headers=_bearer(admin_token)
    ).json()
    agent_role = next(role for role in overview["roles"] if role["code"] == "agent")

    created = client.post(
        "/api/admin/access/users",
        headers=_bearer(admin_token),
        json={
            "full_name": "New Person",
            "email": "new@alpha.example.com",
            "password": NEW_USER_PASSWORD,
            "phone": None,
            "role_id": agent_role["id"],
            "branch_id": None,
        },
    )

    assert created.status_code == 200, created.text

    team = client.get(
        "/api/admin/access/overview", headers=_bearer(admin_token)
    ).json()["users"]

    assert any(person["email"] == "new@alpha.example.com" for person in team)


def test_a_created_user_can_actually_sign_in(client, admin_token, alpha):
    """The membership row is what login checks, so a create that reported
    success while writing nothing would only surface here."""
    overview = client.get(
        "/api/admin/access/overview", headers=_bearer(admin_token)
    ).json()
    agent_role = next(role for role in overview["roles"] if role["code"] == "agent")

    client.post(
        "/api/admin/access/users",
        headers=_bearer(admin_token),
        json={
            "full_name": "Can Sign In",
            "email": "signin@alpha.example.com",
            "password": NEW_USER_PASSWORD,
            "phone": None,
            "role_id": agent_role["id"],
            "branch_id": None,
        },
    )

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "signin@alpha.example.com",
            "password": NEW_USER_PASSWORD,
        },
    )

    assert response.status_code == 200, response.text


def test_an_ordinary_employee_cannot_manage_roles(client, service, alpha):
    service.create_user("plain@alpha.example.com", ADMIN_PASSWORD, "Plain")
    service.assign_user_to_company(
        service.create_user("plain2@alpha.example.com", ADMIN_PASSWORD, "Plain"),
        alpha["id"],
        "agent",
    )

    token = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "plain2@alpha.example.com",
            "password": ADMIN_PASSWORD,
        },
    ).json()["access_token"]

    refused = client.post(
        "/api/admin/access/roles",
        headers=_bearer(token),
        json={
            "name": "Sneaky",
            "code": "sneaky",
            "description": None,
            "permission_codes": ["users.manage"],
        },
    )

    assert refused.status_code == 403, refused.text


# ----------------------------------------------------------------------
# Branch ownership
#
# `role_id` has been checked against the company since this endpoint shipped.
# `branch_id` sat in the same payload, went into the same INSERT, and was not
# checked at all — and the team list joins the branch name straight back out,
# so an id belonging to another company put that company's branch name on this
# company's Roles & Permissions screen.
#
# `tests/test_branch_ownership.py` covers the same defect on channel accounts
# and explains it in full.
# ----------------------------------------------------------------------


def _agent_role_id(client, token):
    overview = client.get(
        "/api/admin/access/overview", headers=_bearer(token)
    ).json()

    return next(role for role in overview["roles"] if role["code"] == "agent")["id"]


def _make_branch(platform, company_id, name):
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        cursor = conn.execute(
            """
            INSERT INTO branches (
                company_id, name, code, status, created_at, updated_at
            )
            VALUES (?, ?, NULL, 'active', ?, ?)
            """,
            (company_id, name, now, now),
        )
        conn.commit()

    return int(cursor.lastrowid)


def test_adding_a_member_to_another_companys_branch_is_refused(
    client, admin_token, platform, beta
):
    foreign = _make_branch(platform, beta["id"], "Beta Secret Warehouse")

    refused = client.post(
        "/api/admin/access/users",
        headers=_bearer(admin_token),
        json={
            "full_name": "New Person",
            "email": "new@alpha.example.com",
            "password": NEW_USER_PASSWORD,
            "phone": None,
            "role_id": _agent_role_id(client, admin_token),
            "branch_id": foreign,
        },
    )

    assert refused.status_code == 400, refused.text
    assert "branch" in refused.json()["detail"].lower()


def test_adding_a_member_to_the_companys_own_branch_still_works(
    client, admin_token, platform, alpha
):
    """A check that refused every branch would satisfy the test above while
    breaking the feature."""
    own = _make_branch(platform, alpha["id"], "Alpha Downtown")

    created = client.post(
        "/api/admin/access/users",
        headers=_bearer(admin_token),
        json={
            "full_name": "New Person",
            "email": "new@alpha.example.com",
            "password": NEW_USER_PASSWORD,
            "phone": None,
            "role_id": _agent_role_id(client, admin_token),
            "branch_id": own,
        },
    )

    assert created.status_code == 200, created.text

    team = client.get(
        "/api/admin/access/overview", headers=_bearer(admin_token)
    ).json()["users"]
    person = next(p for p in team if p["email"] == "new@alpha.example.com")

    assert person["branch_id"] == own
    assert person["branch_name"] == "Alpha Downtown"


def test_moving_a_member_to_another_companys_branch_is_refused(
    client, admin_token, platform, alpha, beta
):
    """The update endpoint is a second door into the same column."""
    own = _make_branch(platform, alpha["id"], "Alpha Downtown")
    foreign = _make_branch(platform, beta["id"], "Beta Secret Warehouse")
    role_id = _agent_role_id(client, admin_token)

    created = client.post(
        "/api/admin/access/users",
        headers=_bearer(admin_token),
        json={
            "full_name": "New Person",
            "email": "new@alpha.example.com",
            "password": NEW_USER_PASSWORD,
            "phone": None,
            "role_id": role_id,
            "branch_id": own,
        },
    )
    user_id = created.json()["user_id"]

    refused = client.patch(
        f"/api/admin/access/users/{user_id}",
        headers=_bearer(admin_token),
        json={"role_id": role_id, "branch_id": foreign, "status": "active"},
    )

    assert refused.status_code == 400, refused.text

    team = client.get(
        "/api/admin/access/overview", headers=_bearer(admin_token)
    ).json()["users"]
    person = next(p for p in team if p["email"] == "new@alpha.example.com")

    assert person["branch_id"] == own


def test_a_member_can_still_have_no_branch(client, admin_token):
    """Optional means optional. Most companies have one location and the
    dropdown's first entry is "All branches"."""
    created = client.post(
        "/api/admin/access/users",
        headers=_bearer(admin_token),
        json={
            "full_name": "New Person",
            "email": "new@alpha.example.com",
            "password": NEW_USER_PASSWORD,
            "phone": None,
            "role_id": _agent_role_id(client, admin_token),
            "branch_id": None,
        },
    )

    assert created.status_code == 200, created.text
