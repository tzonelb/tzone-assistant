"""Tests for what the API actually puts on the wire.

Every field in a response is readable by whoever is signed in, whatever the
screen chooses to render. Minifying the bundle does not change that, and neither
does the screen ignoring a field — the browser's network tab shows the JSON as
it arrived.

So the rule these tests hold is: an endpoint returns what its caller is entitled
to see, and nothing more. Two real leaks are covered, both of which shipped and
neither of which was visible from the interface:

* the inbox returned every colleague's email, phone, role and branch to anyone
  holding `conversations.view`, the lowest permission on the platform;
* the dashboard returned the company's monthly price and the Meta/WhatsApp
  provider identifiers to anyone holding `dashboard.view`.
"""

from __future__ import annotations

import pytest


EMPLOYEE_PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the services and routers at the test databases."""
    import sys

    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.conversations  # noqa: F401
    import backend.api.routes.dashboard  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.message_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.auth_service" in rebound
    assert "backend.api.routes.conversations" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, conversations, dashboard

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(conversations.router)
    app.include_router(dashboard.router)

    return TestClient(app)


def _employee(service, platform, company, email: str, role_code: str) -> int:
    """A real employee with a real role — not a super admin.

    A super admin holds every permission implicitly, so signing in as one would
    make the permission half of these tests pass without proving anything.
    """
    user_id = service.create_user(email, EMPLOYEE_PASSWORD, "Test Person")
    service.assign_user_to_company(user_id, company["id"], role_code)
    return user_id


def _token(client, company, email: str) -> str:
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


# ----------------------------------------------------------------------
# The employee directory
# ----------------------------------------------------------------------

CONTACT_FIELDS = ("email", "phone", "role_name", "role_code", "branch_id")


def test_the_inbox_does_not_hand_out_colleague_contact_details(
    client, service, platform, alpha
):
    """`agent` holds `conversations.view` and not `users.view`, which is the
    exact shape of the account that could read the whole staff directory."""
    _employee(service, platform, alpha, "agent@alpha.example.com", "agent")
    _employee(service, platform, alpha, "colleague@alpha.example.com", "manager")

    token = _token(client, alpha, "agent@alpha.example.com")
    body = client.get("/conversations/options", headers=_bearer(token)).json()

    assert body["employees"], "the assignment list must still be usable"

    for employee in body["employees"]:
        assert employee["display_name"]
        for field in CONTACT_FIELDS:
            assert field not in employee, f"{field} reached an agent"


def test_the_display_name_never_falls_back_to_an_email_address(
    client, service, platform, alpha
):
    """The old fallback was `full_name or email`, which would have put an
    address back into the response through the one field that is always sent."""
    user_id = service.create_user("nameless@alpha.example.com", EMPLOYEE_PASSWORD, "")
    service.assign_user_to_company(user_id, alpha["id"], "agent")

    _employee(service, platform, alpha, "agent2@alpha.example.com", "agent")
    token = _token(client, alpha, "agent2@alpha.example.com")

    body = client.get("/conversations/options", headers=_bearer(token)).json()
    names = [employee["display_name"] for employee in body["employees"]]

    assert not any("@" in name for name in names), names


def test_users_view_is_what_grants_the_contact_details(
    client, service, platform, alpha
):
    """Access is not forbidden — it is the company owner's decision, made by
    granting `users.view` on the roles screen. `manager` holds it."""
    _employee(service, platform, alpha, "manager@alpha.example.com", "manager")

    token = _token(client, alpha, "manager@alpha.example.com")
    body = client.get("/conversations/options", headers=_bearer(token)).json()

    assert body["employees"]
    for employee in body["employees"]:
        for field in CONTACT_FIELDS:
            assert field in employee, f"{field} withheld from users.view"


def test_the_conversation_list_uses_the_same_gate(client, service, platform, alpha):
    """Three endpoints return this list. A fix applied to one of them is not a
    fix."""
    _employee(service, platform, alpha, "agent3@alpha.example.com", "agent")
    token = _token(client, alpha, "agent3@alpha.example.com")

    body = client.get("/conversations/", headers=_bearer(token)).json()

    for employee in body["employees"]:
        assert "email" not in employee


# ----------------------------------------------------------------------
# The dashboard
# ----------------------------------------------------------------------


def test_the_dashboard_does_not_publish_the_price(client, service, platform, alpha):
    """What the company pays is commercial information about the business, and
    `dashboard.view` is the permission nearly every employee holds."""
    _employee(service, platform, alpha, "manager2@alpha.example.com", "manager")
    token = _token(client, alpha, "manager2@alpha.example.com")

    response = client.get("/api/dashboard/summary", headers=_bearer(token))
    assert response.status_code == 200, response.text

    assert "price_monthly" not in response.text


def test_the_dashboard_does_not_publish_provider_identifiers(
    client, service, platform, alpha
):
    """`page_id` and `phone_number_id` are the keys the webhook layer routes
    on. They belong behind `channels.view`, not on the landing screen."""
    _employee(service, platform, alpha, "manager3@alpha.example.com", "manager")
    token = _token(client, alpha, "manager3@alpha.example.com")

    body = client.get("/api/dashboard/summary", headers=_bearer(token)).json()

    for channel in body.get("channels") or []:
        for field in ("page_id", "phone_number_id", "external_account_id"):
            assert field not in channel, f"{field} reached the dashboard"


# ----------------------------------------------------------------------
# The signed-in user
# ----------------------------------------------------------------------


def test_a_new_user_column_is_not_published_by_accident(service):
    """`sanitize_user` is an allow-list precisely so that adding a column to
    `users` — the next ones hold a TOTP secret and recovery codes — does not
    publish it to every browser until somebody remembers to exclude it."""
    sanitized = service.sanitize_user(
        {
            "id": 1,
            "email": "someone@example.com",
            "full_name": "Someone",
            "is_super_admin": 0,
            "password_hash": "pbkdf2_sha256$...",
            "totp_secret": "JBSWY3DPEHPK3PXP",
            "recovery_codes_json": "[...]",
        }
    )

    assert sanitized["email"] == "someone@example.com"
    assert sanitized["is_super_admin"] is False
    assert "password_hash" not in sanitized
    assert "totp_secret" not in sanitized
    assert "recovery_codes_json" not in sanitized


def test_the_session_fields_survive_sanitising(service):
    """`active_company_id` is not a `users` column — it comes from the session
    row — and every request that resolves a company reads it back off this
    dict. An allow-list that dropped it would refuse every request."""
    sanitized = service.sanitize_user(
        {"id": 1, "active_company_id": 7, "session_scope": "company"}
    )

    assert sanitized["active_company_id"] == 7
    assert sanitized["session_scope"] == "company"
