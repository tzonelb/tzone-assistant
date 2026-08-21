"""A dashboard route checks its permission against the company it will read.

These routes accept `?company_id=` so a member of more than one company can pull
up any of them. The permission dependency, though, runs before the handler and
resolves the *session's active* company — not the one the query names. Checked
only there, a member who holds `subscriptions.view` in company A (their active
one) but only a lesser role in company B could ask for B and read B's plan,
usage and connected channels anyway. The fix re-checks the permission against
the company whose data actually leaves.

`agent` holds `dashboard.view` but neither `subscriptions.view` nor
`channels.view`; `manager` holds all three — the exact asymmetry that makes the
cross-company read observable.
"""

from __future__ import annotations

import pytest

EMPLOYEE_PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.dashboard  # noqa: F401
    import backend.services.auth_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, dashboard

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    return TestClient(app)


def _login(client, company, email):
    r = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": EMPLOYEE_PASSWORD,
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_a_member_cannot_borrow_one_companys_permission_to_read_another(
    client, service, platform, alpha, beta
):
    uid = service.create_user("dual@x.com", EMPLOYEE_PASSWORD, "Dual")
    service.assign_user_to_company(uid, alpha["id"], "manager")  # full rights here
    service.assign_user_to_company(uid, beta["id"], "agent")     # lesser role there

    headers = _login(client, alpha, "dual@x.com")

    # agent in beta has neither of these permissions; asking for beta must 403,
    # not answer with beta's data.
    sub = client.get(f"/api/dashboard/subscription?company_id={beta['id']}", headers=headers)
    chan = client.get(f"/api/dashboard/channels?company_id={beta['id']}", headers=headers)

    assert sub.status_code == 403, sub.text
    assert chan.status_code == 403, chan.text


def test_a_member_still_reads_a_company_where_the_permission_holds(
    client, service, platform, alpha, beta
):
    """The guard must not break the legitimate multi-company switcher."""
    uid = service.create_user("boss@x.com", EMPLOYEE_PASSWORD, "Boss")
    service.assign_user_to_company(uid, alpha["id"], "manager")
    service.assign_user_to_company(uid, beta["id"], "manager")  # full rights in both

    headers = _login(client, alpha, "boss@x.com")

    sub = client.get(f"/api/dashboard/subscription?company_id={beta['id']}", headers=headers)
    default = client.get("/api/dashboard/summary", headers=headers)

    assert sub.status_code == 200, sub.text
    assert default.status_code == 200, default.text
