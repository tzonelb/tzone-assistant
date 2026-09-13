"""Tests for LINE as a channel a company connects.

Built the same shape `tests/test_viber_channel.py` and `tests/test_slack_
channel.py` were, because LINE is a genuine hybrid of the two:

* Like Viber, the routing id (`ROUTING_FIELD["line"] = "external_account_id"`)
  is asked of the provider itself (`bot/info`'s `userId`) rather than typed
  in, and the webhook URL is keyed by this platform's own row id
  (`/webhook/line/{account_id}`) because `register_line_webhook` has to
  build that URL before LINE's own id is otherwise relevant.
* Like Slack, a LINE channel needs two separate credentials -- a Channel
  Access Token to send with (`access_token`) and a Channel Secret to verify
  webhook signatures with (`verify_token`) -- and one delivery's `events`
  array can carry more than one message, the same batching Slack does not
  do but Telegram can.

What this file does not test: the reply-token path, because
`channels/line/sender.py` never uses one -- see that module's own docstring
for why a delayed AI reply could never use a token that expired a `push`
message ago.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest


ACCESS_TOKEN = "test-fixture-not-a-real-line-access-token"
CHANNEL_SECRET = "test-fixture-not-a-real-line-channel-secret"
BOT_USER_ID = "Ub9952f8698708a08e30f8fedca1ce4c0"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.line.webhook  # noqa: F401

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
        "channels.line.webhook",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


class _BotInfoResponse:
    def __init__(self, *, status_code: int = 200, user_id: str = BOT_USER_ID):
        self.status_code = status_code
        self._user_id = user_id

    def json(self):
        return {"userId": self._user_id, "basicId": "@example", "displayName": "Bot"}


def _connect(
    company,
    monkeypatch,
    *,
    token: str = ACCESS_TOKEN,
    secret: str = CHANNEL_SECRET,
    info_response: _BotInfoResponse | None = None,
):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx,
        "get",
        lambda *args, **kwargs: info_response or _BotInfoResponse(),
    )

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="line",
        name="Support bot",
        values={"access_token": token, "verify_token": secret},
    )


# ------------------------------------------------------------------ the token


def test_the_bot_user_id_is_derived_from_the_token(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    assert account["external_account_id"] == BOT_USER_ID


def test_a_token_line_rejects_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="LINE"):
        _connect(alpha, monkeypatch, info_response=_BotInfoResponse(status_code=401))


def test_connecting_without_a_token_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError, match="Access Token"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="line",
            name="Support bot",
            values={"verify_token": CHANNEL_SECRET},
        )


def test_connecting_without_a_channel_secret_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="Channel Secret"):
        _connect(alpha, monkeypatch, secret="")


def test_two_companies_cannot_claim_the_same_bot(wired, alpha, beta, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    _connect(alpha, monkeypatch)

    with pytest.raises(ChannelAccountError):
        _connect(beta, monkeypatch)


def test_neither_credential_is_stored_in_the_clear(wired, platform, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed, verify_token_sealed FROM channel_accounts "
            "WHERE channel = 'line'"
        ).fetchone()

    assert row["access_token_sealed"] and row["verify_token_sealed"]
    assert ACCESS_TOKEN not in str(row["access_token_sealed"])
    assert CHANNEL_SECRET not in str(row["verify_token_sealed"])


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    match = wired.resolve_account_for_channel(channel="line", page_id=BOT_USER_ID)

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_a_bot_nobody_connected_resolves_to_nothing(wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(channel="line", page_id="Uffffffffffffffffffffffffffffffff")
        is None
    )


# ---------------------------------------------------------- webhook registration


def test_register_line_webhook_calls_the_right_endpoint(monkeypatch):
    import backend.services.channel_account_service as service_module
    from config.settings import config

    captured = {}

    def fake_put(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json

        class Response:
            status_code = 200

        return Response()

    monkeypatch.setattr(service_module.httpx, "put", fake_put)
    monkeypatch.setattr(config, "APP_PUBLIC_URL", "https://example.tzone.app")

    service_module.register_line_webhook(access_token=ACCESS_TOKEN, account_id=42)

    assert captured["url"] == f"{service_module.LINE_API_BASE}/channel/webhook/endpoint"
    assert captured["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert captured["body"]["endpoint"] == "https://example.tzone.app/webhook/line/42"


def test_register_line_webhook_raises_when_line_refuses(monkeypatch):
    import backend.services.channel_account_service as service_module

    class Response:
        status_code = 400

    monkeypatch.setattr(service_module.httpx, "put", lambda *a, **k: Response())

    with pytest.raises(service_module.ChannelAccountError, match="LINE"):
        service_module.register_line_webhook(access_token=ACCESS_TOKEN, account_id=1)


# ------------------------------------------------------------ the webhook auth


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from channels.line import webhook as line_webhook

    app = FastAPI()
    app.include_router(line_webhook.router)

    return TestClient(app)


def _events_payload(*messages: tuple[str, str]) -> dict:
    return {
        "destination": BOT_USER_ID,
        "events": [
            {
                "type": "message",
                "message": {"type": "text", "id": f"msg-{i}", "text": text},
                "source": {"type": "user", "userId": user_id},
                "timestamp": 1700000000000 + i,
                "replyToken": f"reply-{i}",
            }
            for i, (text, user_id) in enumerate(messages)
        ],
    }


def _signed_body(secret: str, payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")

    return body, {"x-line-signature": signature, "content-type": "application/json"}


def test_a_delivery_with_the_right_signature_is_accepted(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    body, headers = _signed_body(
        CHANNEL_SECRET, _events_payload(("hello", "U0123456789abcdef0123456789abcdef"))
    )

    response = client.post(f"/webhook/line/{account['id']}", content=body, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "accepted", "accepted": 1}


def test_a_delivery_with_several_messages_is_accepted_as_one_batch(
    client, wired, alpha, monkeypatch
):
    account = _connect(alpha, monkeypatch)

    body, headers = _signed_body(
        CHANNEL_SECRET,
        _events_payload(
            ("first", "U0123456789abcdef0123456789abcdef"),
            ("second", "U0123456789abcdef0123456789abcdef"),
        ),
    )

    response = client.post(f"/webhook/line/{account['id']}", content=body, headers=headers)

    assert response.json() == {"status": "accepted", "accepted": 2}


def test_a_delivery_with_no_signature_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    body = json.dumps(
        _events_payload(("hello", "U0123456789abcdef0123456789abcdef"))
    ).encode("utf-8")

    response = client.post(
        f"/webhook/line/{account['id']}", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_signature_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    body, headers = _signed_body(
        "a-completely-different-secret",
        _events_payload(("hello", "U0123456789abcdef0123456789abcdef")),
    )

    response = client.post(f"/webhook/line/{account['id']}", content=body, headers=headers)

    assert response.status_code == 403


def test_an_unknown_account_id_is_refused(client, wired):
    body, headers = _signed_body(
        CHANNEL_SECRET, _events_payload(("hello", "U0123456789abcdef0123456789abcdef"))
    )

    response = client.post("/webhook/line/999999", content=body, headers=headers)

    assert response.status_code == 403


def test_a_disabled_accounts_delivery_is_refused(client, wired, alpha, monkeypatch):
    from backend.services.channel_account_service import channel_account_service

    account = _connect(alpha, monkeypatch)
    channel_account_service.update_account(
        company_id=alpha["id"], account_id=account["id"], values={"status": "disabled"},
    )

    body, headers = _signed_body(
        CHANNEL_SECRET, _events_payload(("hello", "U0123456789abcdef0123456789abcdef"))
    )

    response = client.post(f"/webhook/line/{account['id']}", content=body, headers=headers)

    assert response.status_code == 403


# ------------------------------------------------------------------ the parser


def test_a_text_message_is_parsed_into_a_normalised_event():
    from channels.line.webhook import parse_line_events

    events = parse_line_events(
        _events_payload(("Do you have this in blue?", "U0123456789abcdef0123456789abcdef"))
    )

    assert events == [
        {
            "channel": "line",
            "user_id": "U0123456789abcdef0123456789abcdef",
            "text": "Do you have this in blue?",
            "message_id": "msg-0",
        }
    ]


def test_a_group_message_is_ignored():
    from channels.line.webhook import parse_line_events

    payload = _events_payload(("hello", "some-user"))
    payload["events"][0]["source"] = {"type": "group", "groupId": "Cabc"}

    assert parse_line_events(payload) == []


def test_a_non_text_message_is_ignored():
    from channels.line.webhook import parse_line_events

    payload = _events_payload(("hello", "U0123456789abcdef0123456789abcdef"))
    payload["events"][0]["message"] = {"type": "sticker", "id": "1", "packageId": "1"}

    assert parse_line_events(payload) == []


def test_a_non_message_event_is_ignored():
    from channels.line.webhook import parse_line_events

    payload = {
        "destination": BOT_USER_ID,
        "events": [{"type": "follow", "source": {"type": "user", "userId": "U1"}}],
    }

    assert parse_line_events(payload) == []


# ------------------------------------------------------------------ the sender


def test_line_is_reachable_through_the_shared_dispatcher():
    from channels.sender import SUPPORTED_CHANNELS

    assert "line" in SUPPORTED_CHANNELS


def test_send_text_actually_calls_the_line_sender(monkeypatch):
    """The same gap pinned for every earlier channel: being listed in
    `SUPPORTED_CHANNELS` does not by itself prove `send_text` calls
    anything."""
    import channels.sender as sender_module

    captured = {}

    def fake_send_line_text(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr(sender_module, "send_line_text", fake_send_line_text)

    result = sender_module.send_text(
        channel="line", recipient_id="U0123456789abcdef0123456789abcdef", company_id=1, text="hi"
    )

    assert result["ok"] is True
    assert captured["recipient_id"] == "U0123456789abcdef0123456789abcdef"


def test_the_sender_pushes_with_the_companys_own_token(wired, alpha, monkeypatch):
    from channels.line.sender import send_line_text

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
                return {}

        return Response()

    import channels.line.sender as sender_module

    monkeypatch.setattr(sender_module.httpx, "post", fake_post)

    result = send_line_text(
        recipient_id="U0123456789abcdef0123456789abcdef",
        text="It shipped yesterday.",
        company_id=alpha["id"],
    )

    assert result["ok"] is True
    assert captured["url"].endswith("/message/push")
    assert captured["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert captured["body"]["to"] == "U0123456789abcdef0123456789abcdef"
    assert captured["body"]["messages"] == [{"type": "text", "text": "It shipped yesterday."}]


def test_sending_without_a_connected_account_fails_rather_than_raising(wired, alpha):
    from channels.line.sender import send_line_text

    result = send_line_text(
        recipient_id="U0123456789abcdef0123456789abcdef", text="hi", company_id=alpha["id"],
    )

    assert result["ok"] is False
    assert result["skipped"] is False
