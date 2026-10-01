"""Tests for website live chat as a channel a company connects.

Website chat is the odd one out among the channels this platform now
supports: there is no provider on the other end to authenticate against, no
token to paste, nothing to derive. Connecting one needs nothing but a display
name, and `channel_account_service.generate_webchat_widget_key` mints the one
thing that makes it routable -- a public key, not a secret, meant to sit in a
company's own page source.

What this file tests, in order: the account (a widget key is generated, two
never collide, nothing is sealed because nothing is secret), the public
endpoints a visitor's browser actually calls (`backend/api/routes/
webchat_widget.py`, no session, no signature -- scope by `visitor_id` is the
only thing standing in for auth), and that the shared dispatcher reaches the
no-op sender the same way every other channel's send path does.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import backend.api.routes.webchat_widget  # noqa: F401
    import channels.inbound  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.channel_account_service",
        "backend.api.routes.webchat_widget",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


def _connect(company, *, name: str = "Website chat"):
    from backend.services.channel_account_service import channel_account_service

    return channel_account_service.create_account(
        company_id=company["id"], channel="webchat", name=name, values={},
    )


# ------------------------------------------------------------------ the key


def test_a_widget_key_is_minted_with_nothing_supplied(wired, alpha):
    """The one channel needing no input at all -- there is no bot, app or
    external account to connect."""
    account = _connect(alpha)

    assert account["external_account_id"]
    assert account["external_account_id"].startswith("wc_")


def test_two_accounts_never_share_a_widget_key(wired, alpha, beta):
    a = _connect(alpha, name="Alpha site")
    b = _connect(beta, name="Beta site")

    assert a["external_account_id"] != b["external_account_id"]


def test_nothing_is_sealed_because_nothing_is_secret(wired, platform, alpha):
    """Unlike every other channel, there is no access token to protect -- the
    widget key is meant to sit in a company's own page source."""
    _connect(alpha)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed, verify_token_sealed "
            "FROM channel_accounts WHERE channel = 'webchat'"
        ).fetchone()

    assert row["access_token_sealed"] is None
    assert row["verify_token_sealed"] is None


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha):
    account = _connect(alpha)

    match = wired.resolve_account_for_channel(
        channel="webchat", page_id=account["external_account_id"]
    )

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_an_unknown_widget_key_resolves_to_nothing(wired, alpha):
    _connect(alpha)

    assert (
        wired.resolve_account_for_channel(channel="webchat", page_id="wc_unknown")
        is None
    )


# ------------------------------------------------------------------ the API


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import webchat_widget

    app = FastAPI()
    app.include_router(webchat_widget.router)

    return TestClient(app)


def test_an_unknown_widget_key_is_refused(client, wired):
    response = client.post(
        "/api/webchat/wc_unknown/messages",
        json={"visitor_id": "v-1111111111111111", "text": "hello"},
    )

    assert response.status_code == 404


def test_a_message_reaches_storage_and_can_be_polled_back(client, wired, alpha):
    account = _connect(alpha)
    key = account["external_account_id"]

    sent = client.post(
        f"/api/webchat/{key}/messages",
        json={
            "visitor_id": "v-2222222222222222",
            "text": "Do you have this in blue?",
        },
    )
    assert sent.status_code == 201, sent.text

    polled = client.get(
        f"/api/webchat/{key}/messages",
        headers={"X-Visitor-Id": "v-2222222222222222"},
    )
    assert polled.status_code == 200, polled.text

    messages = polled.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["text"] == "Do you have this in blue?"
    assert messages[0]["direction"] == "in"


def test_a_visitor_never_sees_another_visitors_messages(client, wired, alpha):
    account = _connect(alpha)
    key = account["external_account_id"]

    client.post(
        f"/api/webchat/{key}/messages",
        json={"visitor_id": "v-visitor-one-aaaaa", "text": "My order is late"},
    )

    polled = client.get(
        f"/api/webchat/{key}/messages",
        headers={"X-Visitor-Id": "v-visitor-two-bbbbb"},
    )

    assert polled.json()["messages"] == []


def test_an_empty_message_is_refused_before_it_is_stored(client, wired, alpha):
    account = _connect(alpha)
    key = account["external_account_id"]

    response = client.post(
        f"/api/webchat/{key}/messages",
        json={"visitor_id": "v-3333333333333333", "text": ""},
    )

    assert response.status_code == 422


def test_a_visitor_id_shorter_than_the_floor_is_refused(client, wired, alpha):
    """The floor is defense-in-depth, not cosmetic: the server must not trust
    an implausibly short id just because the client sent one."""
    account = _connect(alpha)
    key = account["external_account_id"]

    response = client.post(
        f"/api/webchat/{key}/messages",
        json={"visitor_id": "v-short", "text": "hi"},
    )

    assert response.status_code == 422


def test_polling_without_the_visitor_header_is_refused(client, wired, alpha):
    account = _connect(alpha)
    key = account["external_account_id"]

    response = client.get(f"/api/webchat/{key}/messages")

    assert response.status_code == 422


def test_disconnecting_the_widget_stops_new_messages(client, wired, alpha):
    from backend.services.channel_account_service import channel_account_service

    account = _connect(alpha)
    key = account["external_account_id"]

    channel_account_service.update_account(
        company_id=alpha["id"], account_id=account["id"], values={"status": "disabled"}
    )

    response = client.post(
        f"/api/webchat/{key}/messages",
        json={"visitor_id": "v-4444444444444444", "text": "hello?"},
    )

    assert response.status_code == 404


# --------------------------------------------------------------------- CORS


def test_the_widget_routes_answer_any_origin(client, wired, alpha):
    """The widget is meant to be embedded on any company's own website, an
    origin this platform cannot know in advance -- unlike every cookie-
    authenticated route, which is intentionally locked to a fixed allowlist
    (see `backend/api/middleware.py:PublicWidgetCorsMiddleware`).

    Exercised directly against the middleware here, since the `client`
    fixture's bare `FastAPI()` app does not mount it -- that only happens on
    the real `main.app`.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.middleware import PublicWidgetCorsMiddleware
    from backend.api.routes import webchat_widget

    app = FastAPI()
    app.include_router(webchat_widget.router)
    app.add_middleware(PublicWidgetCorsMiddleware)

    cors_client = TestClient(app)

    account = _connect(alpha)
    key = account["external_account_id"]

    preflight = cors_client.options(
        f"/api/webchat/{key}/messages",
        headers={
            "Origin": "https://a-companys-own-website.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "x-visitor-id" in preflight.headers["access-control-allow-headers"].lower(), (
        "the poll route reads its bearer key from X-Visitor-Id, but a "
        "cross-origin browser strips a header the preflight did not allow"
    )

    real = cors_client.get(
        f"/api/webchat/{key}/messages",
        headers={
            "Origin": "https://a-companys-own-website.example",
            "X-Visitor-Id": "v-cors-check-aaaaaaaa",
        },
    )

    assert real.headers["access-control-allow-origin"] == "*"


# ------------------------------------------------------------------ the sender


def test_webchat_is_reachable_through_the_shared_dispatcher():
    from channels.sender import SUPPORTED_CHANNELS

    assert "webchat" in SUPPORTED_CHANNELS


def test_send_text_actually_calls_the_webchat_sender(monkeypatch):
    """The same gap pinned for Slack and Discord: being listed in
    `SUPPORTED_CHANNELS` does not by itself prove `send_text` calls
    anything."""
    import channels.sender as sender_module

    captured = {}

    def fake_send_webchat_text(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr(sender_module, "send_webchat_text", fake_send_webchat_text)

    result = sender_module.send_text(
        channel="webchat", recipient_id="v-999", company_id=1, text="hi"
    )

    assert result["ok"] is True
    assert captured["recipient_id"] == "v-999"


def test_the_sender_is_a_genuine_no_op():
    """Nothing to send: the reply this platform writes to storage is what the
    widget's own poll picks up. Documented, not accidental."""
    from channels.webchat.sender import send_webchat_text

    result = send_webchat_text(recipient_id="v-1", text="hi", company_id=1)

    assert result == {"ok": True, "skipped": False}
