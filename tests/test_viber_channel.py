"""Tests for Viber as a channel a company connects.

Built the same shape `tests/test_slack_channel.py` and `tests/test_discord_
channel.py` were: identity in the URL, a signed delivery, an account
resolved before anything is trusted, and the shared `process_inbound_event`
pipeline downstream (tested for parsing here, not for full storage -- see
those two files' own docstrings for why that split exists). What differs is
Viber's own shape:

* The routing id (`ROUTING_FIELD["viber"] = "external_account_id"`) is
  Viber's own ``pa:<digits>`` public-account id, derived from the pasted
  token via `get_account_info` -- the same "ask the provider, never type it"
  reasoning `slack_team_id` and `discord_bot_id` document.
* The webhook URL, unlike every other channel's, is **not** keyed by that
  provider id. It is keyed by this platform's own row id
  (`/webhook/viber/{account_id}`), because `register_viber_webhook` has to
  build that URL before the row's provider id is even relevant to it -- the
  provider id still does the actual routing/uniqueness work, just not in
  the URL.
* Viber has no separate signing secret. `X-Viber-Content-Signature` is an
  HMAC-SHA256 of the raw body keyed by the account's own bot token -- the
  same token `send_message` authenticates with.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest


BOT_TOKEN = "test-fixture-not-a-real-viber-bot-token"
ACCOUNT_ID = "pa:75346594275468546724"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.viber.webhook  # noqa: F401

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
        "channels.viber.webhook",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


class _AccountInfoResponse:
    def __init__(self, *, status: int = 0, account_id: str = ACCOUNT_ID, message: str = "ok"):
        self._status = status
        self._account_id = account_id
        self._message = message

    def json(self):
        body = {"status": self._status, "status_message": self._message}
        if self._status == 0:
            body["id"] = self._account_id
        return body


def _connect(
    company,
    monkeypatch,
    *,
    token: str = BOT_TOKEN,
    info_response: _AccountInfoResponse | None = None,
):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx,
        "post",
        lambda *args, **kwargs: info_response or _AccountInfoResponse(),
    )

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="viber",
        name="Support bot",
        values={"access_token": token},
    )


# ------------------------------------------------------------------ the token


def test_the_account_id_is_derived_from_the_token(wired, alpha, monkeypatch):
    """The operator pastes a bot token; the routing id -- Viber's own `pa:`
    account id -- is asked of Viber itself, never typed in."""
    account = _connect(alpha, monkeypatch)

    assert account["external_account_id"] == ACCOUNT_ID


def test_a_token_viber_rejects_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="Viber"):
        _connect(
            alpha,
            monkeypatch,
            info_response=_AccountInfoResponse(status=2, message="invalidToken"),
        )


def test_connecting_without_a_token_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError):
        channel_account_service.create_account(
            company_id=alpha["id"], channel="viber", name="Support bot", values={},
        )


def test_two_companies_cannot_claim_the_same_bot(wired, alpha, beta, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    _connect(alpha, monkeypatch)

    with pytest.raises(ChannelAccountError):
        _connect(beta, monkeypatch)


def test_the_token_is_not_stored_in_the_clear(wired, platform, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE channel = 'viber'"
        ).fetchone()

    assert row["access_token_sealed"]
    assert BOT_TOKEN not in str(row["access_token_sealed"])


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    match = wired.resolve_account_for_channel(channel="viber", page_id=ACCOUNT_ID)

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_a_bot_nobody_connected_resolves_to_nothing(wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(channel="viber", page_id="pa:99999999999999999999")
        is None
    )


def test_a_viber_id_does_not_match_another_channel(wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(channel="slack", page_id=ACCOUNT_ID) is None
    )


# ---------------------------------------------------------- webhook registration


def test_register_viber_webhook_calls_set_webhook_with_the_right_url(monkeypatch):
    import backend.services.channel_account_service as service_module
    from config.settings import config

    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json

        class Response:
            @staticmethod
            def json():
                return {"status": 0, "status_message": "ok"}

        return Response()

    monkeypatch.setattr(service_module.httpx, "post", fake_post)
    monkeypatch.setattr(config, "APP_PUBLIC_URL", "https://example.tzone.app")

    service_module.register_viber_webhook(token=BOT_TOKEN, account_id=42)

    assert captured["url"] == f"{service_module.VIBER_API_BASE}/set_webhook"
    assert captured["headers"]["X-Viber-Auth-Token"] == BOT_TOKEN
    assert captured["body"]["url"] == "https://example.tzone.app/webhook/viber/42"


def test_register_viber_webhook_raises_when_viber_refuses(monkeypatch):
    import backend.services.channel_account_service as service_module

    class Response:
        @staticmethod
        def json():
            return {"status": 1, "status_message": "invalidUrl"}

    monkeypatch.setattr(service_module.httpx, "post", lambda *a, **k: Response())

    with pytest.raises(service_module.ChannelAccountError, match="Viber"):
        service_module.register_viber_webhook(token=BOT_TOKEN, account_id=1)


def test_unregister_viber_webhook_never_raises(monkeypatch):
    import httpx as real_httpx

    import backend.services.channel_account_service as service_module

    def fake_post(*args, **kwargs):
        raise real_httpx.ConnectError("unreachable")

    monkeypatch.setattr(service_module.httpx, "post", fake_post)

    # Would raise if this were not deliberately best-effort.
    service_module.unregister_viber_webhook(BOT_TOKEN)


# ------------------------------------------------------------ the webhook auth


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from channels.viber import webhook as viber_webhook

    app = FastAPI()
    app.include_router(viber_webhook.router)

    return TestClient(app)


def _message_payload(text: str = "hello", *, sender_id: str = "01234567890A=") -> dict:
    return {
        "event": "message",
        "timestamp": 1700000000000,
        "message_token": 4912661846655238145,
        "sender": {"id": sender_id, "name": "Jane Customer"},
        "message": {"type": "text", "text": text},
    }


def _signed_body(token: str, payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()

    return body, {
        "X-Viber-Content-Signature": signature,
        "content-type": "application/json",
    }


def test_a_delivery_with_the_right_signature_is_accepted(
    client, wired, alpha, monkeypatch
):
    account = _connect(alpha, monkeypatch)

    body, headers = _signed_body(BOT_TOKEN, _message_payload())

    response = client.post(f"/webhook/viber/{account['id']}", content=body, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"


def test_a_delivery_with_no_signature_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    body = json.dumps(_message_payload()).encode("utf-8")

    response = client.post(
        f"/webhook/viber/{account['id']}",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_signature_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    body, headers = _signed_body("a-completely-different-token", _message_payload())

    response = client.post(f"/webhook/viber/{account['id']}", content=body, headers=headers)

    assert response.status_code == 403


def test_an_unknown_account_id_is_refused(client, wired):
    body, headers = _signed_body(BOT_TOKEN, _message_payload())

    response = client.post("/webhook/viber/999999", content=body, headers=headers)

    assert response.status_code == 403


def test_a_disabled_accounts_delivery_is_refused(client, wired, alpha, monkeypatch):
    """The same signature the account used while active no longer opens the
    door once it is disabled -- `_authenticate` only matches active rows."""
    from backend.services.channel_account_service import channel_account_service

    account = _connect(alpha, monkeypatch)
    channel_account_service.update_account(
        company_id=alpha["id"], account_id=account["id"], values={"status": "disabled"},
    )

    body, headers = _signed_body(BOT_TOKEN, _message_payload())

    response = client.post(f"/webhook/viber/{account['id']}", content=body, headers=headers)

    assert response.status_code == 403


# ------------------------------------------------------------------ the parser


def test_a_text_message_is_parsed_into_a_normalised_event():
    from channels.viber.webhook import parse_viber_event

    event = parse_viber_event(_message_payload("Do you have this in blue?"))

    assert event == {
        "channel": "viber",
        "user_id": "01234567890A=",
        "text": "Do you have this in blue?",
        "message_id": "4912661846655238145",
        "customer_name": "Jane Customer",
    }


def test_a_non_message_event_is_ignored():
    from channels.viber.webhook import parse_viber_event

    assert parse_viber_event({"event": "conversation_started", "timestamp": 1}) is None
    assert parse_viber_event({"event": "delivered", "timestamp": 1}) is None
    assert parse_viber_event({"event": "webhook", "timestamp": 1}) is None


def test_a_non_text_message_is_ignored():
    """No attachment handling yet for any channel -- see
    `channels/sender.py`'s `MEDIA_SUPPORTED_CHANNELS`."""
    from channels.viber.webhook import parse_viber_event

    payload = _message_payload()
    payload["message"] = {"type": "picture", "text": "", "media": "https://example.com/x.jpg"}

    assert parse_viber_event(payload) is None


def test_a_message_with_no_text_is_ignored():
    from channels.viber.webhook import parse_viber_event

    payload = _message_payload()
    payload["message"]["text"] = ""

    assert parse_viber_event(payload) is None


# ------------------------------------------------------------------ the sender


def test_viber_is_reachable_through_the_shared_dispatcher():
    from channels.sender import SUPPORTED_CHANNELS

    assert "viber" in SUPPORTED_CHANNELS


def test_send_text_actually_calls_the_viber_sender(monkeypatch):
    """The same gap pinned for every earlier channel: being listed in
    `SUPPORTED_CHANNELS` does not by itself prove `send_text` calls
    anything."""
    import channels.sender as sender_module

    captured = {}

    def fake_send_viber_text(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr(sender_module, "send_viber_text", fake_send_viber_text)

    result = sender_module.send_text(
        channel="viber", recipient_id="01234567890A=", company_id=1, text="hi"
    )

    assert result["ok"] is True
    assert captured["recipient_id"] == "01234567890A="


def test_the_sender_uses_the_companys_own_token(wired, alpha, monkeypatch):
    from channels.viber.sender import send_viber_text

    _connect(alpha, monkeypatch)

    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"status": 0, "status_message": "ok", "message_token": 123}

        return Response()

    import channels.viber.sender as sender_module

    monkeypatch.setattr(sender_module.httpx, "post", fake_post)

    result = send_viber_text(
        recipient_id="01234567890A=", text="It shipped yesterday.", company_id=alpha["id"],
    )

    assert result["ok"] is True
    assert captured["headers"]["X-Viber-Auth-Token"] == BOT_TOKEN
    assert captured["body"]["receiver"] == "01234567890A="
    assert captured["body"]["text"] == "It shipped yesterday."


def test_sending_without_a_connected_account_fails_rather_than_raising(wired, alpha):
    from channels.viber.sender import send_viber_text

    result = send_viber_text(recipient_id="01234567890A=", text="hi", company_id=alpha["id"])

    assert result["ok"] is False
    assert result["skipped"] is False
