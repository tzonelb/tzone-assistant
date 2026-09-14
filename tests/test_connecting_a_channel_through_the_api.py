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

import json
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


PASSWORD = "OwnerPass123!"

# A syntactically real BotFather token: the digits before the colon are the bot
# id the service derives its routing identifier from.
BOT_TOKEN = "7654321098:AAHfakeTokenForTestingPurposesOnly123456789"

# One throwaway RSA keypair, for the Google Chat credential-replacement test
# below -- see `tests/test_google_chat_channel.py` for why it is generated
# once rather than per test.
_GOOGLE_CHAT_PRIVATE_KEY_PEM = rsa.generate_private_key(
    public_exponent=65537, key_size=2048
).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def _google_chat_service_account_json(client_email: str) -> str:
    return json.dumps(
        {
            "project_id": "test-project",
            "private_key": _GOOGLE_CHAT_PRIVATE_KEY_PEM,
            "client_email": client_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


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
def owner(platform, alpha, app_client, monkeypatch):
    from backend.services.auth_service import auth_service
    from backend.services import channel_verification_service
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

    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    # Connecting a channel now requires an elevated grant from confirming a
    # 6-digit emailed code (see test_channel_verification.py for that flow in
    # detail). Fixed here to a known value so this file can stay focused on
    # the routing/validation behaviour it actually tests.
    monkeypatch.setattr(
        channel_verification_service, "_generate_code", lambda: "482913"
    )
    request_sent = app_client.post(
        "/api/channels/verification/request", headers=headers
    )
    assert request_sent.status_code == 200, request_sent.text

    confirmed = app_client.post(
        "/api/channels/verification/confirm",
        json={"code": "482913"},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    headers["X-Elevated-Token"] = confirmed.json()["elevated_token"]

    return headers


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


def test_a_google_chat_credential_cannot_be_swapped_through_a_rename(
    app_client, owner, monkeypatch
):
    """`channel_account_service.update_account` never routes an update
    through the JSON-parsing `_validate` step a create goes through, so a
    fresh `access_token` sent here would be sealed exactly as sent -- the
    whole pasted JSON, not the private key `_validate` would have extracted
    from it -- while `external_account_id` kept naming whichever bot the
    *previous* key belonged to. The route has to refuse this outright rather
    than let the two silently drift apart.

    An unrelated edit -- a rename, with no `access_token` in the request at
    all -- must still go through, which is the difference between a real
    guard and a route that has simply stopped accepting edits.
    """
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx,
        "post",
        lambda *a, **k: type(
            "Response", (), {"status_code": 200, "json": lambda self: {"access_token": "tok"}}
        )(),
    )

    created = app_client.post(
        "/api/channels",
        json={
            "channel": "google_chat",
            "name": "Support bot",
            "access_token": _google_chat_service_account_json(
                "bot@test-project.iam.gserviceaccount.com"
            ),
        },
        headers=owner,
    )
    assert created.status_code in (200, 201), created.text
    account_id = created.json()["account"]["id"]

    swap_attempt = app_client.patch(
        f"/api/channels/{account_id}",
        json={
            "access_token": _google_chat_service_account_json(
                "someone-else@another-project.iam.gserviceaccount.com"
            )
        },
        headers=owner,
    )
    assert swap_attempt.status_code == 400, swap_attempt.text

    rename = app_client.patch(
        f"/api/channels/{account_id}",
        json={"name": "Renamed support bot"},
        headers=owner,
    )
    assert rename.status_code == 200, rename.text
    assert rename.json()["account"]["name"] == "Renamed support bot"
