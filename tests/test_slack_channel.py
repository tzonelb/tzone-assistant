"""Tests for Slack as a channel a company connects.

Built the same shape `tests/test_telegram_channel.py` was, because
`channels/slack/webhook.py` is deliberately the same shape as
`channels/telegram/webhook.py`: identity in the URL, a signed delivery, an
account resolved before anything is trusted, and the shared
`process_inbound_event` pipeline downstream. What differs is only how Slack
proves a delivery is really its own -- a request signature rather than a bare
secret header -- and how the routing id is obtained -- asked of Slack's own
`auth.test`, since a Slack bot token carries no id to parse locally the way a
Telegram token does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest


# Shapes that look real; nothing here reaches Slack.
# Deliberately not shaped like a real Slack token (GitHub's secret scanner
# pattern-matches xoxb-<digits>-<digits>-<alnum>) -- nothing here reaches
# Slack, auth.test is mocked below, so the value only needs to be non-empty.
BOT_TOKEN = "test-fixture-not-a-real-slack-bot-token"
SIGNING_SECRET = "s3cret-signing-secret"
TEAM_ID = "T12345678"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.slack.webhook  # noqa: F401

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
        "channels.slack.webhook",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


class _AuthTestResponse:
    def __init__(self, *, ok: bool = True, team_id: str = TEAM_ID, error: str | None = None):
        self._ok = ok
        self._team_id = team_id
        self._error = error

    def json(self):
        body = {"ok": self._ok}
        if self._ok:
            body["team_id"] = self._team_id
        elif self._error:
            body["error"] = self._error
        return body


def _connect(
    company,
    monkeypatch,
    *,
    token: str = BOT_TOKEN,
    secret: str | None = SIGNING_SECRET,
    auth_response: _AuthTestResponse | None = None,
):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx,
        "post",
        lambda *args, **kwargs: auth_response or _AuthTestResponse(),
    )

    values = {"access_token": token}

    if secret:
        values["verify_token"] = secret

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="slack",
        name="Support bot",
        values=values,
    )


# ------------------------------------------------------------------ the token


def test_the_team_id_is_derived_from_the_token(wired, alpha, monkeypatch):
    """The operator pastes a bot token. Asking them to also find and type the
    workspace id would add a transcription error that matters -- a wrong id
    either receives nothing, or claims a workspace another company routes on."""
    account = _connect(alpha, monkeypatch)

    assert account["external_account_id"] == TEAM_ID


def test_a_token_slack_rejects_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="Slack"):
        _connect(
            alpha,
            monkeypatch,
            auth_response=_AuthTestResponse(ok=False, error="invalid_auth"),
        )


def test_connecting_without_a_token_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="slack",
            name="Support bot",
            values={},
        )


def test_two_companies_cannot_claim_the_same_workspace(wired, alpha, beta, monkeypatch):
    """The unique index is per channel and per routing id. Without it, the
    second company would silently receive the first company's customers."""
    from backend.services.channel_account_service import ChannelAccountError

    _connect(alpha, monkeypatch)

    with pytest.raises(ChannelAccountError):
        _connect(beta, monkeypatch)


def test_the_token_is_not_stored_in_the_clear(wired, platform, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE channel = 'slack'"
        ).fetchone()

    assert row["access_token_sealed"]
    assert BOT_TOKEN not in str(row["access_token_sealed"])


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    match = wired.resolve_account_for_channel(channel="slack", page_id=TEAM_ID)

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_a_workspace_nobody_connected_resolves_to_nothing(wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(channel="slack", page_id="T99999999")
        is None
    )


def test_a_slack_id_does_not_match_another_channel(wired, alpha, monkeypatch):
    """Routing is filtered by channel. A Telegram bot id and a Slack team id
    are different namespaces and could legitimately collide."""
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(channel="telegram", page_id=TEAM_ID)
        is None
    )


# ------------------------------------------------------------ the webhook auth


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from channels.slack import webhook as slack_webhook

    app = FastAPI()
    app.include_router(slack_webhook.router)

    return TestClient(app)


def _event_payload(text: str = "hello", *, user: str = "U0123", channel: str = "D0555") -> dict:
    return {
        "type": "event_callback",
        "team_id": TEAM_ID,
        "event": {
            "type": "message",
            "channel": channel,
            "user": user,
            "text": text,
            "ts": "1700000000.000100",
        },
    }


def _signed(secret: str, payload: dict, *, timestamp: str | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode("utf-8")
    ts = timestamp if timestamp is not None else str(int(time.time()))
    base = f"v0:{ts}:{body.decode('utf-8')}"
    signature = "v0=" + hmac.new(
        secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return body, {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": signature,
        "content-type": "application/json",
    }


def test_a_delivery_with_the_right_signature_is_accepted(
    client, wired, alpha, monkeypatch
):
    import channels.slack.webhook as module

    _connect(alpha, monkeypatch)
    monkeypatch.setattr(module, "dispatch", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module, "resolve_slack_display_name", lambda **kwargs: None
    )

    body, headers = _signed(SIGNING_SECRET, _event_payload())

    response = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"


def test_a_delivery_with_no_signature_is_refused(client, wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    body = json.dumps(_event_payload()).encode("utf-8")

    response = client.post(
        f"/webhook/slack/{TEAM_ID}",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "content-type": "application/json",
        },
    )

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_signature_is_refused(client, wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    body, headers = _signed("a-completely-different-secret", _event_payload())

    response = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert response.status_code == 403


def test_a_stale_timestamp_is_refused_even_with_a_correct_signature(
    client, wired, alpha, monkeypatch
):
    """Slack's own recommendation: a captured, correctly-signed request must
    not be replayable indefinitely."""
    _connect(alpha, monkeypatch)

    stale = str(int(time.time()) - 60 * 30)
    body, headers = _signed(SIGNING_SECRET, _event_payload(), timestamp=stale)

    response = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert response.status_code == 403


def test_an_account_with_no_signing_secret_registered_is_refused(
    client, wired, alpha, monkeypatch
):
    """Not waved through. The workspace id in the URL is not a secret -- it is
    visible on Slack's own app configuration page -- so an unauthenticated
    endpoint would let anybody post into this company's inbox as any Slack
    user they chose."""
    _connect(alpha, monkeypatch, secret=None)

    body, headers = _signed("anything", _event_payload())

    response = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert response.status_code == 403


def test_a_delivery_for_an_unknown_workspace_is_refused(client, wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    other_team = "T99999999"
    body, headers = _signed(SIGNING_SECRET, _event_payload())

    response = client.post(f"/webhook/slack/{other_team}", content=body, headers=headers)

    assert response.status_code == 403


def test_one_companys_secret_does_not_open_anothers_workspace(
    client, wired, alpha, beta, monkeypatch
):
    import channels.slack.webhook as module

    monkeypatch.setattr(module, "dispatch", lambda *args, **kwargs: None)

    _connect(alpha, monkeypatch)
    _connect(
        beta,
        monkeypatch,
        token="test-fixture-token-beta",
        secret="other-secret",
        auth_response=_AuthTestResponse(team_id="T87654321"),
    )

    # Beta's own valid signature, aimed at Alpha's workspace id in the path.
    body, headers = _signed("other-secret", _event_payload())

    refused = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert refused.status_code == 403


def test_a_body_team_id_that_disagrees_with_the_path_is_ignored(
    client, wired, alpha, monkeypatch
):
    """Defence in depth: the URL already names the workspace. A signature
    check alone would not catch a payload whose own team_id was tampered
    with after signing, if the signing secret were ever shared or leaked."""
    import channels.slack.webhook as module

    monkeypatch.setattr(module, "dispatch", lambda *args, **kwargs: None)

    _connect(alpha, monkeypatch)

    payload = _event_payload()
    payload["team_id"] = "T00000000"
    body, headers = _signed(SIGNING_SECRET, payload)

    response = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["reason"] == "team_mismatch"


def test_the_url_verification_handshake_is_answered_after_the_signature_checks(
    client, wired, alpha, monkeypatch
):
    _connect(alpha, monkeypatch)

    payload = {
        "type": "url_verification",
        "token": "ignored",
        "challenge": "a-challenge-value",
    }
    body, headers = _signed(SIGNING_SECRET, payload)

    response = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"challenge": "a-challenge-value"}


def test_the_handshake_still_requires_a_valid_signature(client, wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    payload = {"type": "url_verification", "challenge": "a-challenge-value"}
    body, headers = _signed("wrong-secret", payload)

    response = client.post(f"/webhook/slack/{TEAM_ID}", content=body, headers=headers)

    assert response.status_code == 403


# ----------------------------------------------------------------- the parsing


def test_the_senders_message_is_read_out_of_the_delivery(wired):
    from channels.slack.webhook import parse_slack_events

    events = parse_slack_events(_event_payload("Hi there", user="U9", channel="D9"))

    assert len(events) == 1
    assert events[0]["text"] == "Hi there"
    assert events[0]["user_id"] == "D9"
    assert events[0]["recipient_id"] == "D9"
    assert events[0]["_slack_user_id"] == "U9"


def test_the_bots_own_message_is_not_answered_again(wired):
    """Without this, every reply this platform sends would loop back in as a
    new customer message."""
    from channels.slack.webhook import parse_slack_events

    payload = _event_payload()
    payload["event"]["bot_id"] = "B0123"

    assert parse_slack_events(payload) == []


def test_an_edit_is_not_answered_as_a_new_message(wired):
    from channels.slack.webhook import parse_slack_events

    payload = _event_payload()
    payload["event"]["subtype"] = "message_changed"

    assert parse_slack_events(payload) == []


def test_a_message_with_no_text_is_ignored(wired):
    from channels.slack.webhook import parse_slack_events

    payload = {
        "type": "event_callback",
        "team_id": TEAM_ID,
        "event": {"type": "message", "channel": "D1", "user": "U1"},
    }

    assert parse_slack_events(payload) == []


def test_a_non_message_event_is_ignored(wired):
    from channels.slack.webhook import parse_slack_events

    payload = {
        "type": "event_callback",
        "team_id": TEAM_ID,
        "event": {"type": "reaction_added"},
    }

    assert parse_slack_events(payload) == []


# ------------------------------------------------------------------ the sender


def test_slack_is_reachable_through_the_shared_dispatcher():
    """A channel with no dispatcher entry leaves an employee's manual reply, a
    scheduled message and the takeover handback nowhere to go."""
    from channels.sender import SUPPORTED_CHANNELS

    assert "slack" in SUPPORTED_CHANNELS


def test_sending_without_a_connected_account_fails_rather_than_raising(wired, alpha):
    """The dispatcher's contract is a result dict. A sender that threw would
    take down the batch a customer is waiting in."""
    from channels.slack.sender import send_slack_text

    result = send_slack_text(recipient_id="D555", text="hello", company_id=alpha["id"])

    assert result["ok"] is False
    assert result["error"]


def test_the_sender_uses_the_companys_own_token(wired, alpha, monkeypatch):
    import channels.slack.sender as sender_module

    _connect(alpha, monkeypatch)
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "ts": "1700000000.000200"}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["payload"] = kwargs.get("json")

        return Response()

    monkeypatch.setattr(sender_module.httpx, "post", fake_post)

    result = sender_module.send_slack_text(
        recipient_id="D555", text="hi", company_id=alpha["id"]
    )

    assert result["ok"] is True
    assert captured["headers"]["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert captured["payload"]["channel"] == "D555"
