"""The HTTP layer in front of `conversation_share_service`.

`list_links`/`revoke` have been in the service -- and tested there, directly,
in `test_conversation_share.py` -- since the feature was built. Nobody had
ever put a route in front of them: an employee could create a share link but
had no way to see it again or kill it before its TTL expired. These tests
cover the routes added to close that gap, the same way `test_appointments_route.py`
covers the router in front of its own service.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


EMPLOYEE_PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.conversations  # noqa: F401
    import backend.services.conversation_share_service  # noqa: F401
    import channels.inbound  # noqa: F401

    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound: set[str] = set()
    for name, module in list(sys.modules.items()):
        if module is None or module is manager_module:
            continue
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.add(name)

    assert "backend.services.conversation_share_service" in rebound

    import channels.inbound as inbound

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, conversations

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(conversations.router)

    return TestClient(app)


def _employee(service, platform, company, email: str, role_code: str = "agent") -> int:
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


def _seed_conversation(company, channel="messenger", user_id="share-route-cust"):
    from channels.inbound import process_inbound_event

    process_inbound_event(
        company_id=company["id"],
        event={
            "channel": channel,
            "user_id": user_id,
            "text": "hello there",
            "message_id": f"mid-{user_id}",
        },
    )


def test_a_created_link_is_listed_and_can_be_revoked(client, service, platform, alpha):
    _employee(service, platform, alpha, "shareagent@alpha.example.com")
    token = _token(client, alpha, "shareagent@alpha.example.com")
    headers = _bearer(token)
    _seed_conversation(alpha)

    created = client.post(
        "/conversations/messenger/share-route-cust/share-link",
        headers=headers,
        json={"scope": "chat"},
    )
    assert created.status_code == 201, created.text

    listed = client.get(
        "/conversations/messenger/share-route-cust/share-links", headers=headers
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["scope"] == "chat"
    assert "token" not in items[0]
    link_id = items[0]["id"]

    revoked = client.post(
        f"/conversations/messenger/share-route-cust/share-links/{link_id}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["success"] is True

    after = client.get(
        "/conversations/messenger/share-route-cust/share-links", headers=headers
    )
    assert after.json()["items"] == []


def test_revoking_an_unknown_link_is_a_clean_404(client, service, platform, alpha):
    _employee(service, platform, alpha, "shareagent2@alpha.example.com")
    token = _token(client, alpha, "shareagent2@alpha.example.com")
    headers = _bearer(token)

    response = client.post(
        "/conversations/messenger/nobody/share-links/999999/revoke",
        headers=headers,
    )
    assert response.status_code == 404, response.text


def test_a_link_cannot_be_revoked_from_another_company(
    client, service, platform, alpha, beta
):
    _employee(service, platform, alpha, "shareagent3@alpha.example.com")
    alpha_token = _token(client, alpha, "shareagent3@alpha.example.com")
    _seed_conversation(alpha, user_id="share-route-cust-2")

    created = client.post(
        "/conversations/messenger/share-route-cust-2/share-link",
        headers=_bearer(alpha_token),
        json={"scope": "chat"},
    )
    assert created.status_code == 201, created.text
    listed = client.get(
        "/conversations/messenger/share-route-cust-2/share-links",
        headers=_bearer(alpha_token),
    )
    link_id = listed.json()["items"][0]["id"]

    _employee(service, platform, beta, "intruder@beta.example.com")
    beta_token = _token(client, beta, "intruder@beta.example.com")

    response = client.post(
        f"/conversations/messenger/share-route-cust-2/share-links/{link_id}/revoke",
        headers=_bearer(beta_token),
    )
    assert response.status_code == 404, response.text

    # Still live for the owning company.
    still_listed = client.get(
        "/conversations/messenger/share-route-cust-2/share-links",
        headers=_bearer(alpha_token),
    )
    assert len(still_listed.json()["items"]) == 1
