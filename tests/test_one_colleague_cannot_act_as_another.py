"""The attacker who is already inside, and already allowed.

Every other security file here asks whether one *company* can reach another's
data. This one asks a narrower and more awkward question: inside a single
company, where everybody holds a valid token and most people hold the same
permissions, what stops one employee acting as another?

The permission model cannot answer it. Two agents on the same shift hold an
identical permission set by design — that is what a role is. So the separation
between them is not `require_permission`; it is whether each endpoint scopes
its work to the caller's own id, and that is a per-endpoint decision nothing
checks centrally.

The cases below are the ones where the platform stores something *personal*
inside shared company data: a notification addressed to one person, a private
chat channel, a message with an author, a second factor bound to an account.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import auth, notifications, team_chat, tickets

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

    for module in (auth, notifications, team_chat):
        app.include_router(module.router)

    app.include_router(tickets.tasks_router)

    return TestClient(app, raise_server_exceptions=False)


def _colleague(platform, alpha, app_client, email, name):
    """An ordinary employee of Alpha, signed in. Same role as the other one —
    that is the point."""
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(email=email, password=PASSWORD, full_name=name)

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'manager'",
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
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


@pytest.fixture()
def rana(platform, alpha, app_client):
    return _colleague(platform, alpha, app_client, "rana@alpha.example.com", "Rana")


@pytest.fixture()
def sami(platform, alpha, app_client):
    return _colleague(platform, alpha, app_client, "sami@alpha.example.com", "Sami")


def _notify(platform, alpha, user_id, title):
    from backend.services.notification_service import notification_service

    return notification_service.create(
        company_id=alpha["id"],
        notification_type="handover",
        title=title,
        body="A customer is waiting.",
        recipient_user_id=user_id,
    )


# ------------------------------------------------------------- notifications


def test_a_notification_addressed_to_a_colleague_is_not_listed(
    app_client, rana, sami, platform, alpha
):
    """A handover notice names a customer and a conversation. Addressed to one
    person, it must reach one person."""
    _notify(platform, alpha, sami["user_id"], "For Sami only")

    listed = app_client.get("/api/notifications", headers=rana["headers"])

    assert listed.status_code == 200, listed.text
    assert "For Sami only" not in listed.text, (
        f"Rana can read a notification addressed to Sami\\n{listed.text}"
    )


def test_a_colleagues_notification_cannot_be_marked_read(
    app_client, rana, sami, platform, alpha
):
    """Quieter than reading it, and worse in one way: the person it was for
    never sees it as new, so the customer waits."""
    created = _notify(platform, alpha, sami["user_id"], "For Sami only")
    notification_id = int(created["id"]) if created else None

    assert notification_id, "the fixture did not create a notification"

    response = app_client.post(
        f"/api/notifications/{notification_id}/read", headers=rana["headers"], json={}
    )

    assert response.status_code in (403, 404), (
        f"Rana marked Sami's notification as read\\n{response.text}"
    )

    from backend.services.notification_service import notification_service

    still_unread = notification_service.list_for_user(
        company_id=alpha["id"], user_id=sami["user_id"], status="unread"
    )

    assert any(int(item["id"]) == notification_id for item in still_unread), (
        "Sami's notification is no longer unread"
    )


def test_marking_everything_read_stops_at_your_own(
    app_client, rana, sami, platform, alpha
):
    """`read-all` takes no id at all, so nothing about the request says whose
    notifications it means."""
    _notify(platform, alpha, sami["user_id"], "For Sami only")
    _notify(platform, alpha, rana["user_id"], "For Rana")

    response = app_client.post("/api/notifications/read-all", headers=rana["headers"], json={})

    assert response.status_code in (200, 204), response.text

    from backend.services.notification_service import notification_service

    sami_unread = notification_service.list_for_user(
        company_id=alpha["id"], user_id=sami["user_id"], status="unread"
    )

    assert sami_unread, "Rana's 'mark all read' cleared Sami's notifications too"


def test_your_own_notification_can_still_be_marked_read(
    app_client, rana, platform, alpha
):
    """The other half. A scope check that refused everything would satisfy
    every assertion above and break the bell for everyone."""
    created = _notify(platform, alpha, rana["user_id"], "For Rana")

    response = app_client.post(
        f"/api/notifications/{int(created['id'])}/read",
        headers=rana["headers"],
        json={},
    )

    assert response.status_code in (200, 204), response.text


# --------------------------------------------------------------- team chat


def test_a_private_channel_is_invisible_to_a_colleague(app_client, rana, sami):
    """Even the existence of it: a private channel in the sidebar has already
    told somebody that colleagues are discussing something without them."""
    created = app_client.post(
        "/api/team-chat/channels",
        headers=sami["headers"],
        json={"name": "hr-case-2026", "topic": "sensitive", "is_private": True},
    )
    assert created.status_code in (200, 201), created.text

    channel_id = created.json().get("id") or created.json()["channel"]["id"]

    listing = app_client.get("/api/team-chat/channels", headers=rana["headers"])

    assert "hr-case-2026" not in listing.text, (
        f"a private channel is listed to a non-member\\n{listing.text}"
    )

    messages = app_client.get(
        f"/api/team-chat/channels/{channel_id}/messages", headers=rana["headers"]
    )

    assert messages.status_code in (403, 404), (
        f"a non-member read a private channel's messages\\n{messages.text}"
    )


def test_a_colleagues_message_cannot_be_edited(app_client, rana, sami):
    """An edited message keeps its author's name. Editing someone else's is
    putting words in their mouth."""
    created = app_client.post(
        "/api/team-chat/channels",
        headers=sami["headers"],
        json={"name": "shift-notes", "topic": "open"},
    )
    channel_id = created.json().get("id") or created.json()["channel"]["id"]

    posted = app_client.post(
        f"/api/team-chat/channels/{channel_id}/messages",
        headers=sami["headers"],
        json={"body": "I will handle the refund."},
    )
    assert posted.status_code in (200, 201), posted.text

    message_id = posted.json().get("id") or posted.json()["message"]["id"]

    edited = app_client.patch(
        f"/api/team-chat/messages/{message_id}",
        headers=rana["headers"],
        json={"body": "I will not handle the refund."},
    )

    assert edited.status_code in (403, 404), (
        f"Rana edited a message written by Sami\\n{edited.text}"
    )


def test_you_can_still_edit_your_own_message(app_client, sami):
    created = app_client.post(
        "/api/team-chat/channels",
        headers=sami["headers"],
        json={"name": "my-notes", "topic": "open"},
    )
    channel_id = created.json().get("id") or created.json()["channel"]["id"]

    posted = app_client.post(
        f"/api/team-chat/channels/{channel_id}/messages",
        headers=sami["headers"],
        json={"body": "First draft."},
    )
    message_id = posted.json().get("id") or posted.json()["message"]["id"]

    edited = app_client.patch(
        f"/api/team-chat/messages/{message_id}",
        headers=sami["headers"],
        json={"body": "Second draft."},
    )

    assert edited.status_code in (200, 201, 204), edited.text
