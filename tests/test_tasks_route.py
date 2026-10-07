"""The HTTP layer in front of the tasks router -- specifically the one piece
of it with no prior coverage at all: resolving a task's `conversation_id`
back to the channel and external id its "open source conversation" link
needs. `ticket_service`'s own tests (`test_tasks.py`) prove `conversation_id`
itself round-trips correctly; none of them go through this router, so none
of them would have caught the frontend reading fields (`conversation_channel`,
`conversation_external_user_id`) the response never carried.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.tickets  # noqa: F401
    import backend.services.ticket_service  # noqa: F401
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, tickets

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(tickets.tasks_router)

    return TestClient(app)


def _employee(service, platform, company, email, role_code="agent"):
    user_id = service.create_user(email, PASSWORD, "Test Person")
    service.assign_user_to_company(user_id, company["id"], role_code)
    return user_id


def _login(client, company, email):
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"], "company": company["name"],
            "email": email, "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _insert_conversation(platform, company, *, external_user_id="cust-task-route"):
    from database.manager import utc_now_iso

    now = utc_now_iso()
    with platform["manager"].tenant(company["id"]) as conn:
        cursor = conn.execute(
            """
            INSERT INTO conversations (
                company_id, channel, external_user_id, created_at, updated_at
            )
            VALUES (?, 'messenger', ?, ?, ?)
            """,
            (company["id"], external_user_id, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_a_task_linked_to_a_conversation_carries_its_channel_and_user(
    client, service, platform, alpha
):
    _employee(service, platform, alpha, "taskroute1@alpha.example.com")
    headers = _login(client, alpha, "taskroute1@alpha.example.com")
    conversation_id = _insert_conversation(platform, alpha)

    created = client.post(
        "/api/tasks", headers=headers,
        json={"title": "Follow up", "conversation_id": conversation_id},
    )
    assert created.status_code == 201, created.text
    assert created.json()["conversation_channel"] == "messenger"
    assert created.json()["conversation_external_user_id"] == "cust-task-route"

    listed = client.get("/api/tasks", headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["items"] if item["id"] == created.json()["id"])
    assert row["conversation_channel"] == "messenger"
    assert row["conversation_external_user_id"] == "cust-task-route"


def test_a_standalone_task_carries_no_conversation_link(client, service, platform, alpha):
    _employee(service, platform, alpha, "taskroute2@alpha.example.com")
    headers = _login(client, alpha, "taskroute2@alpha.example.com")

    created = client.post(
        "/api/tasks", headers=headers, json={"title": "Standalone task"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["conversation_channel"] is None
    assert created.json()["conversation_external_user_id"] is None
