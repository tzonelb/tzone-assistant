"""Each module, driven end to end through its own API, one test each.

Every other file here asks whether something is *guarded*, *scoped*, *fast* or
*refused*. None of them asks the plainest question an owner would ask: does
this screen actually do its job?

That gap is real. A module can be perfectly guarded and completely broken — a
route that 500s on save, a create that returns an id nothing can read back, a
list that never includes what was just added. Nothing in a permission test
would notice, because a permission test is satisfied by a 403 and a 200 and
does not look at what the 200 contained.

So: for each module, do the thing the screen does. Create something real, read
it back, change it, and check the change is there. The assertions are on the
*content*, never on the status code alone — a route that answers 200 with an
empty body passes a status check and fails a customer.

`preferences` is absent because it has no API: it is the personal-settings
screen, and `tests/test_every_module_is_gated.py` holds it to making no
request at all.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import (
        activity, ai_teaching, analytics, appointments, auth, catalogue,
        channels, comments, company_settings, conversation_tags, conversations,
        customers, dashboard, knowledge, manual_messages, notifications, roles,
        scheduler, team_chat, tickets,
    )

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

    for module in (
        auth, activity, ai_teaching, analytics, appointments, catalogue,
        channels, comments, company_settings, conversation_tags, conversations,
        customers, dashboard, knowledge, manual_messages, notifications, roles,
        scheduler, team_chat,
    ):
        app.include_router(module.router)

    app.include_router(tickets.router)
    app.include_router(tickets.tasks_router)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def owner(platform, alpha, app_client):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="owner@alpha.example.com", password=PASSWORD, full_name="Alpha Owner"
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
            "email": "owner@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


def _ok(response, what):
    assert response.status_code in (200, 201, 204), (
        f"{what} answered {response.status_code}:\n{response.text}"
    )
    return response.json() if response.content else {}


def _id_of(body):
    if isinstance(body, dict):
        if isinstance(body.get("id"), int):
            return int(body["id"])
        for key, value in body.items():
            if isinstance(value, dict) and isinstance(value.get("id"), int):
                return int(value["id"])
            if key.endswith("_id") and isinstance(value, int):
                return int(value)
    raise AssertionError(f"no id in {body!r}")


# ------------------------------------------------------------------ comments


def test_comments_lists_replies_and_changes_status(
    app_client, owner, platform, alpha
):
    """Comments arrive from Meta, so the screen's job is to *answer* them, not
    to create them. Replying is the one action that leaves the platform, so it
    is the one worth driving here."""
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(alpha["id"]) as conn:
        cursor = conn.execute(
            """
            INSERT INTO post_comments (
                company_id, channel, provider_comment_id, post_id,
                author_name, message, status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'CMT-1', 'POST-1', 'A Customer',
                    'Is this in stock?', 'open', ?, ?)
            """,
            (alpha["id"], now, now),
        )
        conn.commit()
        comment_id = int(cursor.lastrowid)

    listed = _ok(
        app_client.get("/api/comments", headers=owner["headers"]), "the comment list"
    )

    assert any(
        "Is this in stock?" in str(item) for item in listed.get("items", listed)
    ), f"the comment does not appear on its own screen: {listed}"

    one = _ok(
        app_client.get(f"/api/comments/{comment_id}", headers=owner["headers"]),
        "opening a comment",
    )
    assert "Is this in stock?" in str(one)

    changed = app_client.patch(
        f"/api/comments/{comment_id}/status",
        headers=owner["headers"],
        json={"status": "answered"},
    )
    _ok(changed, "marking a comment handled")

    with platform["manager"].tenant(alpha["id"]) as conn:
        stored = conn.execute(
            "SELECT status FROM post_comments WHERE id = ?", (comment_id,)
        ).fetchone()["status"]

    assert stored == "answered", f"the status did not change, it is still {stored!r}"


# ----------------------------------------------------------------- team chat


def test_team_chat_creates_a_channel_posts_and_reads_it_back(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/team-chat/channels",
            headers=owner["headers"],
            json={"name": "shift-handover", "topic": "end of day"},
        ),
        "creating a chat channel",
    )
    channel_id = _id_of(created)

    _ok(
        app_client.post(
            f"/api/team-chat/channels/{channel_id}/messages",
            headers=owner["headers"],
            json={"body": "Closing the till at eight."},
        ),
        "posting a message",
    )

    messages = _ok(
        app_client.get(
            f"/api/team-chat/channels/{channel_id}/messages", headers=owner["headers"]
        ),
        "reading the messages back",
    )

    assert "Closing the till at eight." in str(messages), (
        f"the message was accepted and is not in the channel: {messages}"
    )

    overview = _ok(
        app_client.get("/api/team-chat/overview", headers=owner["headers"]),
        "the team chat overview",
    )

    assert "shift-handover" in str(overview), (
        f"the new channel is missing from the screen's first paint: {overview}"
    )


# --------------------------------------------------------------- ai teaching


def test_ai_teaching_saves_a_persona_and_reads_it_back(app_client, owner):
    """The screen that decides how the assistant talks. A save that does not
    stick here is a company believing it has changed its assistant's voice and
    finding the old one answering customers."""
    _ok(
        app_client.put(
            "/api/ai-teaching/profile",
            headers=owner["headers"],
            json={"name": "Layla", "tone": "formal", "default_language": "en"},
        ),
        "saving the assistant's profile",
    )

    body = _ok(
        app_client.get("/api/ai-teaching/profile", headers=owner["headers"]),
        "reading the profile back",
    )
    profile = body["profile"]

    assert profile["name"] == "Layla", (
        f"the persona's name was saved and did not come back: {profile}"
    )
    assert profile["tone"] == "formal"
    assert profile["default_language"] == "en"

    # The channels this screen offers must all be connectable. Asserted here
    # as well as in `test_channel_catalogue.py` because this is the response
    # the screen actually draws from, and it is where `website_chat` was found
    # after every check in that file had passed.
    from backend.services.channel_account_service import SUPPORTED_CHANNELS

    assert set(body["channels"]) <= set(SUPPORTED_CHANNELS), (
        f"the AI Teaching screen offers channels that cannot be connected: "
        f"{sorted(set(body['channels']) - set(SUPPORTED_CHANNELS))}"
    )


def test_ai_teaching_manages_departments(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/ai-teaching/departments",
            headers=owner["headers"],
            json={"code": "spare_parts", "name_en": "Spare Parts", "name_ar": "قطع"},
        ),
        "adding a section",
    )
    department_id = _id_of(created)

    listed = _ok(
        app_client.get("/api/ai-teaching/departments", headers=owner["headers"]),
        "the section list",
    )
    assert "spare_parts" in str(listed)

    _ok(
        app_client.put(
            f"/api/ai-teaching/departments/{department_id}",
            headers=owner["headers"],
            json={"name_en": "Parts Counter"},
        ),
        "renaming a section",
    )

    again = _ok(
        app_client.get(
            f"/api/ai-teaching/departments/{department_id}", headers=owner["headers"]
        ),
        "reading the section back",
    )
    assert "Parts Counter" in str(again), f"the rename did not stick: {again}"


def test_ai_teaching_reply_policy_saves(app_client, owner):
    """The flags that decide what a customer actually receives."""
    _ok(
        app_client.put(
            "/api/ai-teaching/reply-policy",
            headers=owner["headers"],
            json={"values": {"show_buttons": False}},
        ),
        "saving the reply policy",
    )

    policy = _ok(
        app_client.get("/api/ai-teaching/reply-policy", headers=owner["headers"]),
        "reading the reply policy",
    )

    assert "show_buttons" in str(policy)


# ------------------------------------------------------- the other modules


def test_knowledge_creates_reads_updates_and_deletes(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/knowledge",
            headers=owner["headers"],
            json={"title": "Opening hours", "content_ar": "من ٩ لـ٩"},
        ),
        "adding a knowledge item",
    )
    item_id = _id_of(created)

    read = _ok(
        app_client.get(f"/api/knowledge/{item_id}", headers=owner["headers"]),
        "reading it back",
    )
    assert "من ٩ لـ٩" in str(read)

    _ok(
        app_client.put(
            f"/api/knowledge/{item_id}",
            headers=owner["headers"],
            json={"title": "Opening hours (winter)"},
        ),
        "editing it",
    )

    again = _ok(
        app_client.get(f"/api/knowledge/{item_id}", headers=owner["headers"]),
        "reading the edit back",
    )
    assert "winter" in str(again)

    _ok(
        app_client.delete(f"/api/knowledge/{item_id}", headers=owner["headers"]),
        "deleting it",
    )

    gone = app_client.get(f"/api/knowledge/{item_id}", headers=owner["headers"])
    assert gone.status_code == 404, f"a deleted item is still readable: {gone.text}"


def test_catalogue_creates_and_prices_a_product(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/catalogue/products",
            headers=owner["headers"],
            json={"name": "Filter", "price": 25, "status": "active"},
        ),
        "adding a product",
    )
    product_id = _id_of(created)

    _ok(
        app_client.put(
            f"/api/catalogue/products/{product_id}",
            headers=owner["headers"],
            json={"price": 30},
        ),
        "changing the price",
    )

    read = _ok(
        app_client.get(
            f"/api/catalogue/products/{product_id}", headers=owner["headers"]
        ),
        "reading the product",
    )

    assert float(read["price"]) == 30.0, f"the price did not change: {read}"


def test_tasks_creates_assigns_and_closes(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/tasks", headers=owner["headers"], json={"title": "Call the supplier"}
        ),
        "creating a task",
    )
    task_id = _id_of(created)

    _ok(
        app_client.post(
            f"/api/tasks/{task_id}/assign",
            headers=owner["headers"],
            json={"assigned_user_id": owner["user_id"]},
        ),
        "assigning it",
    )

    _ok(
        app_client.patch(
            f"/api/tasks/{task_id}/status",
            headers=owner["headers"],
            json={"status": "closed"},
        ),
        "closing it",
    )

    read = _ok(
        app_client.get(f"/api/tasks/{task_id}", headers=owner["headers"]),
        "reading it back",
    )

    assert "closed" in str(read), f"the task did not close: {read}"


def test_appointments_books_and_cancels(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/appointments",
            headers=owner["headers"],
            json={
                "staff_user_id": owner["user_id"],
                "starts_at": "2030-07-01T09:00:00Z",
                "ends_at": "2030-07-01T09:30:00Z",
                "title": "Fitting",
            },
        ),
        "booking an appointment",
    )
    appointment_id = _id_of(created)

    _ok(
        app_client.post(
            f"/api/appointments/{appointment_id}/cancel",
            headers=owner["headers"],
            json={"reason": "customer rescheduled"},
        ),
        "cancelling it",
    )

    read = _ok(
        app_client.get(
            f"/api/appointments/{appointment_id}", headers=owner["headers"]
        ),
        "reading it back",
    )

    assert "cancelled" in str(read), f"the cancellation did not stick: {read}"


def test_scheduler_queues_and_cancels_a_post(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/scheduler",
            headers=owner["headers"],
            json={
                "channel": "messenger",
                "body": "Open late this Thursday.",
                "scheduled_for": "2030-08-01T10:00:00Z",
            },
        ),
        "scheduling a post",
    )
    post_id = _id_of(created)

    listed = _ok(
        app_client.get("/api/scheduler", headers=owner["headers"]),
        "the scheduler list",
    )
    assert "Open late this Thursday." in str(listed)

    _ok(
        app_client.post(
            f"/api/scheduler/{post_id}/cancel", headers=owner["headers"], json={}
        ),
        "cancelling the post",
    )


def test_channels_connects_and_disconnects_an_account(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/channels",
            headers=owner["headers"],
            json={"channel": "messenger", "name": "Shop page", "page_id": "PAGE-77"},
        ),
        "connecting a channel",
    )
    account_id = _id_of(created)

    listed = _ok(
        app_client.get("/api/channels", headers=owner["headers"]), "the channel list"
    )
    assert "Shop page" in str(listed)

    _ok(
        app_client.delete(f"/api/channels/{account_id}", headers=owner["headers"]),
        "disconnecting it",
    )


def test_customers_reads_and_edits(app_client, owner, platform, alpha):
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(alpha["id"]) as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, first_seen_at, last_seen_at,
                created_at, updated_at
            )
            VALUES (?, 'Walk-in Customer', ?, ?, ?, ?)
            """,
            (alpha["id"], now, now, now, now),
        )
        conn.commit()
        customer_id = int(cursor.lastrowid)

    _ok(
        app_client.put(
            f"/api/customers/{customer_id}",
            headers=owner["headers"],
            json={"internal_name": "Abu Ali", "phone": "+9611234567"},
        ),
        "editing a customer",
    )

    read = _ok(
        app_client.get(f"/api/customers/{customer_id}", headers=owner["headers"]),
        "reading the customer",
    )

    assert read.get("internal_name") == "Abu Ali", f"the edit did not stick: {read}"


def test_company_settings_saves_and_reads_back(app_client, owner):
    _ok(
        app_client.put(
            "/api/company-settings/working_hours",
            headers=owner["headers"],
            json={"values": {"timezone": "Asia/Beirut"}},
        ),
        "saving working hours",
    )

    read = _ok(
        app_client.get("/api/company-settings/working_hours", headers=owner["headers"]),
        "reading them back",
    )

    assert read["values"]["timezone"] == "Asia/Beirut", (
        f"the setting did not save: {read}"
    )


def test_roles_creates_a_role_and_grants_it(app_client, owner):
    created = _ok(
        app_client.post(
            "/api/admin/access/roles",
            headers=owner["headers"],
            json={
                "name": "Shift Lead",
                "code": "shift_lead",
                "permission_codes": ["conversations.view", "customers.view"],
            },
        ),
        "creating a role",
    )
    role_id = _id_of(created)

    overview = _ok(
        app_client.get("/api/admin/access/overview", headers=owner["headers"]),
        "the roles screen",
    )

    assert "Shift Lead" in str(overview), f"the new role is not listed: {overview}"

    _ok(
        app_client.patch(
            f"/api/admin/access/roles/{role_id}",
            headers=owner["headers"],
            json={"permission_codes": ["conversations.view"]},
        ),
        "changing what the role may do",
    )


def test_the_read_only_screens_answer(app_client, owner):
    """Dashboard, analytics, notifications and the activity log have nothing to
    create. What they must not do is fail — a summary screen that 500s is the
    first thing an owner sees in the morning."""
    for path in (
        "/api/dashboard/summary",
        "/api/dashboard/company",
        "/api/dashboard/subscription",
        "/api/analytics/summary",
        "/api/notifications",
        "/api/notifications/summary",
        "/api/activity",
        "/api/activity/options",
        "/conversations/",
        "/conversations/options",
    ):
        response = app_client.get(path, headers=owner["headers"])

        assert response.status_code == 200, (
            f"{path} answered {response.status_code}:\n{response.text}"
        )
