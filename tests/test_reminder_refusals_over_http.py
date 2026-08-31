"""The reminder endpoint's documented refusals, exercised over HTTP.

`tests/test_conversation_reminders.py` drives `conversation_reminder_service`
directly. That is why nobody noticed the route could not report a refusal at
all: it answered `ReminderError` with

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, ...)

in a module that never imported `status`. Every refusal the endpoint documents
-- a time in the past, `auto_send` with no message, an unparseable time --
raised `NameError` inside the handler and came back as a 500. The service still
refused the write, so no bad reminder was ever stored; what was broken was the
answer, and an employee saw "server error" where the product had a sentence
ready for them.

The class of bug matters more than the line: a handler's error path is only
executed when something goes wrong, so a service-level test suite can be
complete and green while every refusal on the route is a crash. These tests go
through the endpoint for that reason.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"
CHANNEL = "messenger"
CUSTOMER = "cust-1"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import auth, conversations

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, conversations):
        app.include_router(module.router)

    # raise_server_exceptions=False so an unhandled error arrives as the 500 a
    # browser would receive, instead of being re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def owner(platform, alpha, app_client):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="owner@alpha.example.com", password=PASSWORD, full_name="Rana Haddad"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
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
            "company": alpha["name"],
            "email": "owner@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def conversation(platform, alpha, owner):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.message_service import message_service

    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel=CHANNEL, external_user_id=CUSTOMER
    )
    message_service.save_message(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        direction="in",
        text="Can you follow up with me tomorrow?",
        sender_type="customer",
    )

    return f"/conversations/{CHANNEL}/{CUSTOMER}/reminder"


def test_a_time_in_the_past_is_refused_with_a_message(app_client, owner, conversation):
    response = app_client.post(
        conversation,
        json={"reminder_at": "2020-01-01T00:00:00Z"},
        headers=owner,
    )

    assert response.status_code == 400, response.text
    assert "passed" in response.json()["detail"].lower()


def test_auto_send_with_no_message_is_refused_with_a_message(
    app_client, owner, conversation
):
    response = app_client.post(
        conversation,
        json={"reminder_at": "2099-01-01T00:00:00Z", "auto_send": True},
        headers=owner,
    )

    assert response.status_code == 400, response.text
    assert "message" in response.json()["detail"].lower()


def test_an_unparseable_time_is_refused_with_a_message(app_client, owner, conversation):
    response = app_client.post(
        conversation,
        json={"reminder_at": "next tuesday-ish"},
        headers=owner,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]


def test_a_good_reminder_is_still_accepted(app_client, owner, conversation):
    """The refusals are reported, not manufactured."""
    response = app_client.post(
        conversation,
        json={"reminder_at": "2099-01-01T09:00:00Z", "note": "Ask about delivery"},
        headers=owner,
    )

    assert response.status_code == 200, response.text
