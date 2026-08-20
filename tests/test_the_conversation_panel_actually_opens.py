"""Three features that had never once worked, and nothing said so.

`conversation_control_service` resolved employee names with `LEFT JOIN users`
from inside a **tenant** connection. `users` lives in the control database, in
a different encrypted file, and SQLite does not treat an unknown table as an
empty one — it raises `no such table: users`.

So these three raised, every time, for every company, since they were written:

* `GET /conversations/{channel}/{user}/control` — the panel an employee opens
  to see who touched a conversation
* `GET /conversations/{channel}/{user}/export` — the same data as a file
* `POST /conversations/{channel}/{user}/notes` — leaving an internal note

Not an edge case and not an attack: the plain request, with no parameters,
answered 500. Nothing noticed because nothing called them — every test for this
service went through methods that stayed inside one database.

Found by sweeping every route for 500s rather than by reading, and worth
recording as a class: a cross-database join cannot be caught by review of
either half. The query is valid SQL and the table exists — in the other file.
The only thing that finds it is running it.

The fix is the pattern already used by the inbox: resolve the page's names in
one control-plane query afterwards. These tests assert the names arrive, not
merely that the call returns, because a version that answers 200 with "System"
against every row would pass a status check and tell an owner nothing about who
did what.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


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

    assert (
        getattr(sys.modules["backend.services.auth_service"], "database_manager", None)
        is test_manager
    )

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
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "rana@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


@pytest.fixture()
def conversation(platform, alpha, app_client, owner):
    """A real conversation with a real customer message on it."""
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.message_service import message_service

    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    )
    message_service.save_message(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        direction="in",
        text="Do you deliver on Sundays?",
        sender_type="customer",
    )

    return "messenger", "cust-1"


# ------------------------------------------------------------------- the panel


def test_the_control_panel_opens(app_client, owner, conversation):
    channel, user_id = conversation

    response = app_client.get(
        f"/conversations/{channel}/{user_id}/control", headers=owner["headers"]
    )

    assert response.status_code == 200, (
        f"the conversation panel answered {response.status_code}:\n"
        f"{response.text}"
    )

    body = response.json()

    assert "events" in body and "notes" in body, body


def test_the_panel_names_the_employee_who_acted(
    app_client, owner, conversation, platform, alpha
):
    """The half a status check would miss.

    A timeline that loads and calls everybody "System" is a timeline that
    answers 200 and tells an owner nothing about who did what — which is the
    only reason to open it.
    """
    channel, user_id = conversation

    taken = app_client.post(
        f"/conversations/{channel}/{user_id}/take-over",
        headers=owner["headers"],
        json={},
    )
    assert taken.status_code in (200, 201), taken.text

    body = app_client.get(
        f"/conversations/{channel}/{user_id}/control", headers=owner["headers"]
    ).json()

    actors = {event.get("actor_name") for event in body["events"]}

    assert "Rana Haddad" in actors, (
        f"the timeline does not name the employee who took the conversation "
        f"over; it says {actors}"
    )


def test_a_note_can_be_left_and_names_its_author(app_client, owner, conversation):
    channel, user_id = conversation

    written = app_client.post(
        f"/conversations/{channel}/{user_id}/notes",
        headers=owner["headers"],
        json={"note": "Customer asked about Sunday delivery."},
    )

    assert written.status_code in (200, 201), (
        f"leaving an internal note answered {written.status_code}:\n"
        f"{written.text}"
    )
    assert "Rana Haddad" in written.text, (
        f"the note does not name its author:\n{written.text}"
    )

    body = app_client.get(
        f"/conversations/{channel}/{user_id}/control", headers=owner["headers"]
    ).json()

    assert body["notes"], "the note is not on the timeline"
    assert body["notes"][0]["author_name"] == "Rana Haddad", body["notes"][0]


def test_the_conversation_exports(app_client, owner, conversation):
    channel, user_id = conversation

    response = app_client.get(
        f"/conversations/{channel}/{user_id}/export", headers=owner["headers"]
    )

    assert response.status_code == 200, (
        f"exporting a conversation answered {response.status_code}:\n"
        f"{response.text}"
    )
    assert "Sunday" in response.text, "the export does not contain the conversation"


def test_a_conversation_that_does_not_exist_is_a_404_not_a_crash(app_client, owner):
    """A stale tab, a deleted conversation, a bookmarked link."""
    for suffix in ("/control", "/export"):
        response = app_client.get(
            f"/conversations/messenger/nobody-at-all{suffix}",
            headers=owner["headers"],
        )

        assert response.status_code == 404, (
            f"{suffix} answered {response.status_code} for a conversation that "
            f"does not exist:\n{response.text}"
        )


# --------------------------------------------------------------------- tags


def test_conversation_tags_can_be_listed_and_created(app_client, owner):
    """`list_tags` selected and filtered on a `status` column the table did not
    have, so the inbox's tag list answered 500 from the day it shipped."""
    listed = app_client.get("/api/conversation-tags", headers=owner["headers"])

    assert listed.status_code == 200, (
        f"the tag list answered {listed.status_code}:\n{listed.text}"
    )
    assert listed.json()["items"] == []

    created = app_client.post(
        "/api/conversation-tags",
        headers=owner["headers"],
        json={"name": "Refund", "color": "#ff0000"},
    )

    assert created.status_code in (200, 201), created.text

    again = app_client.get("/api/conversation-tags", headers=owner["headers"])

    assert [item["name"] for item in again.json()["items"]] == ["Refund"], (
        f"the tag was created and is not listed: {again.text}"
    )


def test_a_tag_survives_the_upgrade_path_too(platform, alpha):
    """New companies get `status` from CREATE TABLE. Companies provisioned
    before this release get it from `TENANT_COLUMNS`, and that is the path that
    actually matters — every existing company is on it."""
    from database.schema_tenant import TENANT_COLUMNS

    assert "status" in TENANT_COLUMNS.get("conversation_tags", {}), (
        "existing companies will never get the column, so their tag list stays "
        "broken after this fix ships"
    )

    with platform["manager"].tenant(alpha["id"]) as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(conversation_tags)").fetchall()
        }

    assert "status" in columns, f"the column is missing: {sorted(columns)}"
