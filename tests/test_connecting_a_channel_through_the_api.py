"""Connecting a channel through the endpoint the screen actually calls.

Every existing Telegram test drives `channel_account_service` directly. That is
why nobody noticed that `POST /api/channels` refused **every** Telegram account
ever submitted: the route's own validator asked for `external_account_id`, a
field `ChannelAccountCreate` does not declare, so `getattr` returned `None` and
the request never reached the service that would have derived the id from the
bot token.

A service-level test cannot catch that, because the defect lives entirely in the
layer above it. So this file connects each channel the way the Channels screen
does — over HTTP, with a session — and asserts the account comes back.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"

# A syntactically real BotFather token: the digits before the colon are the bot
# id the service derives its routing identifier from.
BOT_TOKEN = "7654321098:AAHfakeTokenForTestingPurposesOnly123456789"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import auth, channels

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, channels):
        app.include_router(module.router)

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


def test_a_telegram_account_can_be_connected_over_http(app_client, owner):
    """The regression: this returned 422 for every Telegram account.

    The operator pastes the bot token and nothing else, because the routing id
    is derived from it. The route has to accept exactly that.
    """
    response = app_client.post(
        "/api/channels",
        json={"channel": "telegram", "name": "T-ZONE Bot", "access_token": BOT_TOKEN},
        headers=owner,
    )

    assert response.status_code in (200, 201), response.text


def test_a_telegram_account_without_a_token_is_still_refused(app_client, owner):
    """The check is not simply removed: with no token there is nothing to derive."""
    response = app_client.post(
        "/api/channels",
        json={"channel": "telegram", "name": "T-ZONE Bot"},
        headers=owner,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("channel", "field"),
    [
        ("messenger", "page_id"),
        ("instagram", "instagram_business_id"),
        ("whatsapp", "phone_number_id"),
    ],
)
def test_the_other_channels_still_require_their_routing_id(
    app_client, owner, channel, field
):
    """The Telegram exception must not loosen the three typed-in identifiers."""
    refused = app_client.post(
        "/api/channels",
        json={"channel": channel, "name": f"T-ZONE {channel}"},
        headers=owner,
    )
    assert refused.status_code == 422, refused.text

    accepted = app_client.post(
        "/api/channels",
        json={"channel": channel, "name": f"T-ZONE {channel}", field: "1234567890"},
        headers=owner,
    )
    assert accepted.status_code in (200, 201), accepted.text
