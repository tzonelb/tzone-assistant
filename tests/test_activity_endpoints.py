"""Tests that the endpoints actually file what they change.

`tests/test_activity_log.py` proves the log works. This file proves it is
*called* — which is the half that was missing before, since a writer nobody
calls is the same defect in a tidier place.

One test per gap the audit named: knowledge, the catalogue (with the price
change called out on its own), channels, and permissions.
"""

from __future__ import annotations

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    """The customer routers, wired to this test's databases."""
    import sys

    import database.manager as manager_module

    import backend.api.routes.activity  # noqa: F401
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.catalogue  # noqa: F401
    import backend.api.routes.channels  # noqa: F401
    import backend.api.routes.knowledge  # noqa: F401
    import backend.api.routes.roles  # noqa: F401
    import backend.services.activity_service  # noqa: F401
    import backend.services.auth_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.activity_service" in rebound
    assert "backend.services.auth_service" in rebound

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import (
        activity,
        auth,
        catalogue,
        channels,
        knowledge,
        roles,
    )

    app = FastAPI()

    for module in (auth, activity, catalogue, channels, knowledge, roles):
        app.include_router(module.router)

    return TestClient(app)


@pytest.fixture()
def owner(platform, alpha, app_client):
    """An owner of alpha, signed in, with full permissions."""
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="owner@alpha.example.com",
        password=PASSWORD,
        full_name="Rita Haddad",
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (alpha["id"],),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO company_users (
                company_id, user_id, role_id, status, created_at
            )
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


def _log(app_client, owner, **params) -> list[dict]:
    response = app_client.get(
        "/api/activity", headers=owner["headers"], params=params
    )
    assert response.status_code == 200, response.text

    return response.json()["items"]


# --------------------------------------------------------------------- login


def test_a_sign_in_is_recorded_in_the_company_log(app_client, owner):
    """An owner should be able to see who accessed their workspace, and from
    where. Nothing recorded it before."""
    entries = _log(app_client, owner, category="auth")

    assert [entry["action"] for entry in entries] == ["auth.signed_in"]
    assert entries[0]["actor_label"] == "Rita Haddad"


def test_a_refused_sign_in_does_not_land_in_a_company_log(
    app_client, owner, platform, alpha
):
    """The email on a refused attempt may belong to nobody. Attributing it
    would need a lookup whose duration reveals whether the account exists."""
    app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "nobody@alpha.example.com",
            "password": "WrongPassword123!",
        },
    )

    assert not [
        entry
        for entry in _log(app_client, owner, category="auth")
        if entry["action"] == "auth.sign_in_failed"
    ]

    with platform["manager"].control() as conn:
        rows = conn.execute(
            "SELECT company_id FROM audit_log WHERE action = 'auth.sign_in_failed'"
        ).fetchall()

    assert rows, "the operator was not told about a refused sign-in"
    assert all(row["company_id"] is None for row in rows)


# ----------------------------------------------------------------- knowledge


def test_teaching_the_assistant_is_recorded(app_client, owner):
    response = app_client.post(
        "/api/knowledge",
        headers=owner["headers"],
        json={"title": "Opening hours", "content_en": "Nine to six"},
    )
    assert response.status_code == 201, response.text

    entries = _log(app_client, owner, category="knowledge")

    assert entries[0]["action"] == "knowledge.item_created"
    assert "Opening hours" in entries[0]["summary"]


def test_the_log_records_the_title_and_not_the_answer(app_client, owner):
    """The base is already the record of what it says. Copying the answer text
    would duplicate it into a table with different retention."""
    app_client.post(
        "/api/knowledge",
        headers=owner["headers"],
        json={"title": "Refunds", "content_en": "SECRET-POLICY-TEXT"},
    )

    entries = _log(app_client, owner, category="knowledge")

    assert "SECRET-POLICY-TEXT" not in str(entries[0])


def test_editing_and_deleting_knowledge_are_recorded(app_client, owner):
    created = app_client.post(
        "/api/knowledge",
        headers=owner["headers"],
        json={"title": "Opening hours", "content_en": "Nine to six"},
    ).json()

    app_client.put(
        f"/api/knowledge/{created['id']}",
        headers=owner["headers"],
        json={"title": "Opening times"},
    )
    app_client.delete(f"/api/knowledge/{created['id']}", headers=owner["headers"])

    actions = [entry["action"] for entry in _log(app_client, owner, category="knowledge")]

    assert actions == [
        "knowledge.item_deleted",
        "knowledge.item_updated",
        "knowledge.item_created",
    ]


# ----------------------------------------------------------------- catalogue


def test_a_price_change_is_its_own_event_with_both_numbers(app_client, owner):
    """The sharpest gap. The assistant quotes catalogue prices to customers as
    confirmed facts, so a wrong one is a promise the business has to keep — and
    there was nowhere to look for who changed it."""
    created = app_client.post(
        "/api/catalogue/products",
        headers=owner["headers"],
        json={"name": "Blue widget", "price": 25},
    ).json()

    app_client.put(
        f"/api/catalogue/products/{created['id']}",
        headers=owner["headers"],
        json={"price": 30},
    )

    entry = _log(app_client, owner, action="catalogue.price_changed")[0]

    assert entry["before"]["price"] == 25
    assert entry["after"]["price"] == 30
    assert entry["severity"] == "notice"


def test_an_edit_that_is_not_a_price_change_is_filed_separately(app_client, owner):
    """So an owner can filter the log down to exactly the changes that reach a
    customer's screen."""
    created = app_client.post(
        "/api/catalogue/products",
        headers=owner["headers"],
        json={"name": "Blue widget", "price": 25},
    ).json()

    app_client.put(
        f"/api/catalogue/products/{created['id']}",
        headers=owner["headers"],
        json={"name": "Azure widget"},
    )

    assert _log(app_client, owner, action="catalogue.price_changed") == []
    assert _log(app_client, owner, action="catalogue.product_updated")


def test_a_missing_product_does_not_produce_a_log_entry(app_client, owner):
    """An entry for something that does not exist and was never changed is not
    merely useless — it is misleading during an investigation."""
    app_client.put(
        "/api/catalogue/products/424242",
        headers=owner["headers"],
        json={"price": 30},
    )

    assert _log(app_client, owner, category="catalogue") == []


# ------------------------------------------------------------------ channels


def test_connecting_a_channel_is_recorded_and_mirrored(
    app_client, owner, platform, alpha
):
    response = app_client.post(
        "/api/channels",
        headers=owner["headers"],
        json={
            "channel": "messenger",
            "name": "Shop page",
            "page_id": "PAGE_1",
            "access_token": "a-token",
        },
    )
    assert response.status_code in (200, 201), response.text

    entry = _log(app_client, owner, category="channels")[0]
    assert entry["action"] == "channels.account_connected"
    assert entry["kind"] == "security"

    with platform["manager"].control() as conn:
        mirrored = conn.execute(
            "SELECT action FROM audit_log WHERE company_id = ?", (alpha["id"],)
        ).fetchall()

    assert "channels.account_connected" in {row["action"] for row in mirrored}


def test_the_access_token_never_reaches_the_log(app_client, owner):
    app_client.post(
        "/api/channels",
        headers=owner["headers"],
        json={
            "channel": "messenger",
            "name": "Shop page",
            "page_id": "PAGE_1",
            "access_token": "SUPER-SECRET-TOKEN",
        },
    )

    assert "SUPER-SECRET-TOKEN" not in str(_log(app_client, owner))


def test_replacing_a_credential_is_not_filed_as_a_rename(app_client, owner):
    """It is the change that can silently redirect a company's messages, and it
    looks identical to a rename in a log that records only "account updated"."""
    created = app_client.post(
        "/api/channels",
        headers=owner["headers"],
        json={
            "channel": "messenger",
            "name": "Shop page",
            "page_id": "PAGE_1",
            "access_token": "a-token",
        },
    ).json()["account"]

    app_client.patch(
        f"/api/channels/{created['id']}",
        headers=owner["headers"],
        json={"access_token": "a-new-token"},
    )

    assert _log(app_client, owner, action="channels.credentials_replaced")


# ---------------------------------------------------------------- permissions


def test_granting_permissions_is_recorded_as_a_security_event(app_client, owner):
    """Deciding who may read a customer file or replace a channel credential
    is the change most worth reviewing after the fact, and nothing recorded
    it."""
    response = app_client.post(
        "/api/admin/access/roles",
        headers=owner["headers"],
        json={
            "name": "Supervisor",
            "code": "supervisor",
            "permission_codes": ["conversations.view"],
        },
    )
    assert response.status_code == 200, response.text

    entry = _log(app_client, owner, category="roles")[0]

    assert entry["action"] == "roles.role_created"
    assert entry["kind"] == "security"
    assert entry["after"]["permissions"] == ["conversations.view"]


# ------------------------------------------------------------- authorisation


def test_the_log_is_not_readable_without_the_permission(app_client, platform, alpha):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="agent@alpha.example.com", password=PASSWORD, full_name="An Agent"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'agent'",
            (alpha["id"],),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO company_users (
                company_id, user_id, role_id, status, created_at
            )
            VALUES (?, ?, ?, 'active', ?)
            """,
            (alpha["id"], user_id, int(role["id"]) if role else None, utc_now_iso()),
        )
        conn.commit()

    token = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "agent@alpha.example.com",
            "password": PASSWORD,
        },
    ).json()["access_token"]

    response = app_client.get(
        "/api/activity", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403, (
        "the log names who did what; it is not a feed of one's colleagues"
    )
