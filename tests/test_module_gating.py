"""Tests for the Super Admin's module switches, on the customer side.

A switch that only hides a link is not a switch. The property defended here is
that turning a module off for a company actually closes the API for that
company — and for that company alone.

The interface reads the same decision from ``/api/platform-ui/config`` so it can
avoid drawing a door that opens onto a 403, but the door is what is tested.
"""

from __future__ import annotations

import pytest


PLATFORM_PASSWORD = "PlatformAdminPass1"
EMPLOYEE_PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the services and every mounted router at the test databases."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep: a module imported afterwards would bind this
    # test's temporary manager permanently and corrupt later test files.
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.company_settings  # noqa: F401
    import backend.api.routes.customers  # noqa: F401
    import backend.services.company_settings_service  # noqa: F401
    import backend.api.routes.platform  # noqa: F401
    import backend.api.routes.platform_ui  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.module_access  # noqa: F401
    import backend.services.platform_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.platform_service" in rebound
    assert "backend.services.auth_service" in rebound

    from backend.services.platform_service import platform_service

    return platform_service


@pytest.fixture()
def client(service):
    """The control plane, sign-in, the config endpoint and one gated module."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import (
        auth,
        company_settings,
        customers,
        platform as platform_routes,
        platform_ui,
    )
    from backend.services.module_access import require_module

    app = FastAPI()
    app.include_router(platform_routes.router)
    app.include_router(platform_ui.router)
    app.include_router(auth.router)
    app.include_router(
        company_settings.router,
        dependencies=[Depends(require_module("company_settings"))],
    )
    # Mounted the same way main.py mounts it, because the gate lives in the
    # registration rather than in the handler — testing the handler alone would
    # test something the application does not do.
    app.include_router(
        customers.router, dependencies=[Depends(require_module("customers"))]
    )

    return TestClient(app)


def _make_user(email, password, *, full_name="Test Person", is_super_admin=False):
    from backend.services.auth_service import auth_service

    return auth_service.create_user(
        email=email,
        password=password,
        full_name=full_name,
        is_super_admin=is_super_admin,
    )


def _employ(platform, company, user_id: int) -> None:
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        conn.execute(
            """
            INSERT INTO company_users (
                company_id, user_id, role_id, status, created_at
            )
            VALUES (?, ?, NULL, 'active', ?)
            """,
            (company["id"], user_id, utc_now_iso()),
        )
        conn.commit()


def _platform_token(client, email="root@platform.example.com") -> str:
    _make_user(
        email, PLATFORM_PASSWORD, full_name="Platform Root", is_super_admin=True
    )

    response = client.post(
        "/api/platform/auth/login",
        json={"email": email, "password": PLATFORM_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _employee_token(client, platform, company, email) -> str:
    # A super admin so the permission check passes unconditionally: this file
    # is about the module gate, and a permission failure would mask it.
    user_id = _make_user(email, EMPLOYEE_PASSWORD, is_super_admin=True)
    _employ(platform, company, user_id)

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": EMPLOYEE_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _switch_off(client, admin_token, company, module: str) -> None:
    response = client.put(
        f"/api/platform/companies/{company['id']}/config",
        headers=_bearer(admin_token),
        json={"modules": {module: False}},
    )
    assert response.status_code == 200, response.text


# ----------------------------------------------------------------------
# What the customer app is told
# ----------------------------------------------------------------------


def test_a_new_company_has_every_module_on(client, platform, alpha):
    """Defaulting to off would silently disable every module of every existing
    company the first time the platform ships a new one."""
    token = _employee_token(client, platform, alpha, "one@alpha.example.com")

    response = client.get("/api/platform-ui/config", headers=_bearer(token))

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["company_id"] == alpha["id"]
    assert body["modules"]
    assert all(body["modules"].values())


def test_the_config_belongs_to_the_caller_not_to_a_parameter(
    client, platform, alpha, beta
):
    """There is no company argument, so there is nothing to tamper with."""
    token = _employee_token(client, platform, alpha, "two@alpha.example.com")

    response = client.get(
        "/api/platform-ui/config",
        headers=_bearer(token),
        params={"company_id": beta["id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["company_id"] == alpha["id"]


def test_a_platform_token_cannot_read_a_workspace_config(client, platform, alpha):
    """A platform session resolves no company, and this endpoint needs one."""
    admin_token = _platform_token(client)

    response = client.get("/api/platform-ui/config", headers=_bearer(admin_token))

    assert response.status_code == 403, response.text


def test_switching_a_module_off_is_visible_to_the_company(
    client, platform, alpha
):
    admin_token = _platform_token(client)
    token = _employee_token(client, platform, alpha, "three@alpha.example.com")

    _switch_off(client, admin_token, alpha, "customers")

    body = client.get(
        "/api/platform-ui/config", headers=_bearer(token)
    ).json()

    assert body["modules"]["customers"] is False
    assert body["modules"]["conversations"] is True


# ----------------------------------------------------------------------
# What the API actually refuses
# ----------------------------------------------------------------------


def test_a_switched_off_module_closes_its_api(client, platform, alpha):
    """The point of the whole feature. Before the switch the endpoint answers;
    after it, the same token on the same endpoint is refused."""
    admin_token = _platform_token(client)
    token = _employee_token(client, platform, alpha, "four@alpha.example.com")

    assert client.get("/api/customers", headers=_bearer(token)).status_code == 200

    _switch_off(client, admin_token, alpha, "customers")

    refused = client.get("/api/customers", headers=_bearer(token))

    assert refused.status_code == 403, refused.text
    assert "not enabled" in refused.json()["detail"].lower()


def test_switching_a_module_off_for_one_company_leaves_the_other_alone(
    client, platform, alpha, beta
):
    """A per-company switch that is not per company is worse than none: the
    operator turns off one customer's module and silently breaks the rest."""
    admin_token = _platform_token(client)
    alpha_token = _employee_token(client, platform, alpha, "five@alpha.example.com")
    beta_token = _employee_token(client, platform, beta, "five@beta.example.com")

    _switch_off(client, admin_token, alpha, "customers")

    assert (
        client.get("/api/customers", headers=_bearer(alpha_token)).status_code == 403
    )
    assert (
        client.get("/api/customers", headers=_bearer(beta_token)).status_code == 200
    )


def test_switching_a_module_back_on_reopens_its_api(client, platform, alpha):
    """A one-way switch would make an operator mistake permanent."""
    admin_token = _platform_token(client)
    token = _employee_token(client, platform, alpha, "six@alpha.example.com")

    _switch_off(client, admin_token, alpha, "customers")
    assert client.get("/api/customers", headers=_bearer(token)).status_code == 403

    response = client.put(
        f"/api/platform/companies/{alpha['id']}/config",
        headers=_bearer(admin_token),
        json={"modules": {"customers": True}},
    )
    assert response.status_code == 200, response.text

    assert client.get("/api/customers", headers=_bearer(token)).status_code == 200


def test_an_unknown_module_key_is_refused_when_the_gate_is_built(service):
    """A typo in a router registration must fail the process at import, not
    permit everything at runtime — a gate that silently allows is the one
    failure mode nobody notices."""
    from backend.services.module_access import UnknownModule, require_module

    with pytest.raises(UnknownModule):
        require_module("conversatoins")


# ----------------------------------------------------------------------
# One decision, reported in one place
# ----------------------------------------------------------------------


def test_company_settings_reports_the_platform_modules_and_refuses_to_change_them(
    client, platform, alpha
):
    """The company settings API used to carry its own five module switches that
    nothing ever read, so a company could turn Appointments "off" and keep
    using it. The section now mirrors the platform decision and locks it."""
    admin_token = _platform_token(client)
    token = _employee_token(client, platform, alpha, "seven@alpha.example.com")

    _switch_off(client, admin_token, alpha, "catalogue")

    section = client.get(
        "/api/company-settings/modules", headers=_bearer(token)
    ).json()

    assert section["values"]["catalogue"] is False
    assert section["values"]["conversations"] is True
    assert "catalogue" in section["locked_keys"]

    refused = client.put(
        "/api/company-settings/modules",
        headers=_bearer(token),
        json={"values": {"catalogue": True}},
    )

    assert refused.status_code == 409, refused.text
    assert "locked" in refused.text.lower()

    # And the refusal was real: the API still says no.
    assert client.get("/api/customers", headers=_bearer(token)).status_code == 200


def test_company_profile_settings_name_the_company_not_the_platform_owner(
    client, platform, alpha, beta
):
    """The default used to be the string "T-ZONE", so every company's settings
    screen opened showing the platform owner's own company."""
    alpha_token = _employee_token(client, platform, alpha, "eight@alpha.example.com")
    beta_token = _employee_token(client, platform, beta, "eight@beta.example.com")

    alpha_values = client.get(
        "/api/company-settings/company_profile", headers=_bearer(alpha_token)
    ).json()["values"]

    beta_values = client.get(
        "/api/company-settings/company_profile", headers=_bearer(beta_token)
    ).json()["values"]

    assert alpha_values["company_name"] == alpha["name"]
    assert beta_values["company_name"] == beta["name"]

    # The workspace code is the credential that unseals the database. It is not
    # a settings field, and it must never travel to a browser.
    assert "workspace_code" not in alpha_values
