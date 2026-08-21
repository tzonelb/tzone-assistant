"""A delegated administrator cannot grant themselves more than they hold.

`users.manage` lets an owner delegate day-to-day team administration -- adding
members, assigning roles -- without handing over the company. But the role and
assignment writes checked only that the caller *was* an administrator, never
that what they were conferring stayed within their own permissions. A user in a
custom "HR admin" role holding only `users.manage` + `settings.manage` could
therefore mint a role carrying every permission, or assign themselves the Owner
role, and walk up to full access.

The fix holds every grant to the caller's own ceiling. An owner (and a super
admin) has no ceiling and can still do anything, including assign Owner; a
delegated admin may only confer permissions they themselves hold.
"""

from __future__ import annotations

import sys

import pytest

import backend.api.routes.roles  # noqa: F401  (loaded before any monkeypatch)

ADMIN_PASSWORD = "AdminPass123456"
HR_PASSWORD = "HrAdminPass1234"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.services.auth_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    assert (
        getattr(sys.modules["backend.api.routes.roles"], "database_manager")
        is test_manager
    )

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


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, alpha, email, password):
    r = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": email,
            "password": password,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture()
def owner_token(client, service, alpha):
    uid = service.create_user("owner@alpha.example.com", ADMIN_PASSWORD, "Owner")
    service.assign_user_to_company(uid, alpha["id"], "owner")
    return _login(client, alpha, "owner@alpha.example.com", ADMIN_PASSWORD)


@pytest.fixture()
def owner_id(platform, alpha):
    with platform["manager"].control() as conn:
        return int(
            conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
                (alpha["id"],),
            ).fetchone()["id"]
        )


@pytest.fixture()
def hr_admin(client, service, alpha, owner_token):
    """A delegated admin: a custom role holding only user/settings management."""
    created = client.post(
        "/api/admin/access/roles",
        headers=_bearer(owner_token),
        json={
            "name": "HR Admin",
            "code": "hradmin",
            "permission_codes": ["users.manage", "settings.manage"],
        },
    )
    assert created.status_code == 200, created.text
    hr_role_id = created.json()["role_id"]

    uid = service.create_user("hr@alpha.example.com", HR_PASSWORD, "HR")
    service.assign_user_to_company(uid, alpha["id"], "hradmin")
    token = _login(client, alpha, "hr@alpha.example.com", HR_PASSWORD)
    return {"token": token, "user_id": uid, "role_id": hr_role_id}


def _all_codes(platform):
    with platform["manager"].control() as conn:
        return [str(r["code"]) for r in conn.execute("SELECT code FROM permissions")]


def test_a_delegated_admin_cannot_mint_an_all_powerful_role(
    client, platform, hr_admin
):
    r = client.post(
        "/api/admin/access/roles",
        headers=_bearer(hr_admin["token"]),
        json={"name": "God", "code": "god", "permission_codes": _all_codes(platform)},
    )
    assert r.status_code == 403, r.text


def test_a_delegated_admin_cannot_assign_themselves_the_owner_role(
    client, hr_admin, owner_id
):
    r = client.patch(
        f"/api/admin/access/users/{hr_admin['user_id']}",
        headers=_bearer(hr_admin["token"]),
        json={"role_id": owner_id},
    )
    assert r.status_code == 403, r.text


def test_a_delegated_admin_can_delegate_within_their_own_ceiling(client, hr_admin):
    """Not a lockout: they may still confer what they themselves hold."""
    r = client.post(
        "/api/admin/access/roles",
        headers=_bearer(hr_admin["token"]),
        json={
            "name": "Junior HR",
            "code": "junior_hr",
            "permission_codes": ["users.manage"],
        },
    )
    assert r.status_code == 200, r.text


def test_an_owner_has_no_ceiling(client, platform, owner_token, owner_id, service, alpha):
    """The guard must not touch the owner: they can still grant anything and
    assign the Owner role itself."""
    god = client.post(
        "/api/admin/access/roles",
        headers=_bearer(owner_token),
        json={"name": "God", "code": "god", "permission_codes": _all_codes(platform)},
    )
    assert god.status_code == 200, god.text

    # An owner assigning the Owner role to another member is allowed.
    other = service.create_user("other@alpha.example.com", HR_PASSWORD, "Other")
    service.assign_user_to_company(other, alpha["id"], "agent")
    promoted = client.patch(
        f"/api/admin/access/users/{other}",
        headers=_bearer(owner_token),
        json={"role_id": owner_id},
    )
    assert promoted.status_code == 200, promoted.text
