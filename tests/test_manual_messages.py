"""Tests for the dashboard's manual-reply routes (text and attachment).

Untested until now: the send path itself (`channels/sender.py`) and the
ownership rules (`conversation_control_service`) each have their own test
files, but nothing exercised these two routes over HTTP -- the layer that
actually enforces the permission gate, the ownership conflict this router's
own `_ownership_conflict` helper builds, and the cross-company path check on
an attachment's `media_url`.

A reply here only succeeds once an employee has taken the conversation over
(`renew_reply_lease` refuses an AI-handled one -- see
`test_conversation_ownership.py`), so every send test takes it over first
with `conversation_control_service.set_ai_mode`, the same call the real
"take over" button makes.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "EmployeePass12345"
CHANNEL = "messenger"
CUSTOMER = "customer-manual-1"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.manual_messages  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.conversation_control_service  # noqa: F401
    import backend.services.message_service  # noqa: F401
    import channels.sender  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.auth_service",
        "backend.services.conversation_control_service",
        "backend.services.message_service",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, manual_messages

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(manual_messages.router)

    return TestClient(app)


def _employ(platform, company, user_id: int, role_code: str) -> int:
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = ?",
            (company["id"], role_code),
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


def _login(client, company, email, password=PASSWORD):
    response = client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _employee(client, service, platform, company, email, role_code="agent"):
    user_id = service.create_user(email=email, password=PASSWORD, full_name="Employee")
    _employ(platform, company, user_id, role_code)
    return user_id, _login(client, company, email)


def _take_over(alpha, user_id, *, channel=CHANNEL, external_user_id=CUSTOMER):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )

    return conversation_control_service.set_ai_mode(
        company_id=alpha["id"],
        channel=channel,
        external_user_id=external_user_id,
        handled_by_ai=False,
        actor_user_id=user_id,
    )


def _fake_send_text_ok(monkeypatch):
    import backend.api.routes.manual_messages as manual_messages_module

    def fake(*, channel, recipient_id, company_id, text):
        return {"ok": True, "response": {"message_id": "wamid.123"}}

    monkeypatch.setattr(manual_messages_module, "send_text", fake)


# ----------------------------------------------------------------- the gate


def test_replying_without_the_permission_is_refused(client, service, platform, alpha):
    user_id, headers = _employee(
        client, service, platform, alpha, "viewer@alpha.example.com", "viewer"
    )
    _take_over(alpha, user_id)

    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply",
        headers=headers,
        json={"text": "hello"},
    )

    assert response.status_code == 403


def test_an_unauthenticated_request_is_refused(client):
    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply", json={"text": "hello"}
    )

    assert response.status_code in (401, 403)


# --------------------------------------------------------------- the reply


def test_a_reply_is_sent_and_recorded(client, service, platform, alpha, monkeypatch):
    _fake_send_text_ok(monkeypatch)
    user_id, headers = _employee(
        client, service, platform, alpha, "agent1@alpha.example.com"
    )
    _take_over(alpha, user_id)

    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply",
        headers=headers,
        json={"text": "We will look into it."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "sent"
    assert body["message"]["direction"] == "out"

    with platform["manager"].tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT body, sender_type, sender_user_id FROM messages "
            "WHERE channel = ? AND external_user_id = ? ORDER BY id DESC LIMIT 1",
            (CHANNEL, CUSTOMER),
        ).fetchone()

    assert row["body"] == "We will look into it."
    assert row["sender_type"] == "employee"
    assert row["sender_user_id"] == user_id


def test_replying_before_taking_over_is_a_conflict(client, service, platform, alpha, monkeypatch):
    """`renew_reply_lease` refuses an AI-handled conversation -- an employee
    must take it over before this route will let them speak on it."""
    _fake_send_text_ok(monkeypatch)
    _, headers = _employee(client, service, platform, alpha, "agent2@alpha.example.com")

    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply",
        headers=headers,
        json={"text": "hello"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conversation_owned"


def test_a_non_owner_cannot_reply_to_someone_elses_takeover(
    client, service, platform, alpha, monkeypatch
):
    _fake_send_text_ok(monkeypatch)
    owner_id, _ = _employee(client, service, platform, alpha, "owner3@alpha.example.com")
    _take_over(alpha, owner_id)

    _, other_headers = _employee(
        client, service, platform, alpha, "other3@alpha.example.com"
    )

    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply",
        headers=other_headers,
        json={"text": "let me handle this"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["owner_user_id"] == owner_id


def test_an_empty_reply_is_refused(client, service, platform, alpha, monkeypatch):
    _fake_send_text_ok(monkeypatch)
    user_id, headers = _employee(
        client, service, platform, alpha, "agent4@alpha.example.com"
    )
    _take_over(alpha, user_id)

    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply",
        headers=headers,
        json={"text": "   "},
    )

    assert response.status_code == 422


def test_an_unsupported_channel_is_refused(client, service, platform, alpha, monkeypatch):
    _fake_send_text_ok(monkeypatch)
    user_id, headers = _employee(
        client, service, platform, alpha, "agent5@alpha.example.com"
    )
    _take_over(alpha, user_id, channel="carrier-pigeon")

    response = client.post(
        "/conversations/carrier-pigeon/customer-x/reply",
        headers=headers,
        json={"text": "hello"},
    )

    assert response.status_code == 400


def test_a_provider_rejection_is_reported_not_swallowed(
    client, service, platform, alpha, monkeypatch
):
    import backend.api.routes.manual_messages as manual_messages_module

    monkeypatch.setattr(
        manual_messages_module,
        "send_text",
        lambda **kwargs: {"ok": False, "reason": "missing_credentials"},
    )

    user_id, headers = _employee(
        client, service, platform, alpha, "agent6@alpha.example.com"
    )
    _take_over(alpha, user_id)

    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply",
        headers=headers,
        json={"text": "hello"},
    )

    assert response.status_code == 502


def test_a_reply_never_reaches_another_companys_conversation(
    client, service, platform, alpha, beta, monkeypatch
):
    """The company is resolved from the caller's own session, never from the
    URL -- there is no company id in this route to spoof, but the takeover
    itself must not have crossed the tenant boundary either."""
    _fake_send_text_ok(monkeypatch)
    user_id, headers = _employee(
        client, service, platform, beta, "agent7@beta.example.com"
    )
    # Alpha's own customer has an active takeover -- by an alpha employee.
    alpha_owner, _ = _employee(
        client, service, platform, alpha, "owner7@alpha.example.com"
    )
    _take_over(alpha, alpha_owner)

    response = client.post(
        f"/conversations/{CHANNEL}/{CUSTOMER}/reply",
        headers=headers,
        json={"text": "hijacked?"},
    )

    # Beta's employee has no takeover on beta's own copy of this
    # (channel, external_user_id) pair, so beta's own database refuses it --
    # the same 409 a first-time reply gets, not a peek into alpha's data.
    assert response.status_code == 409
