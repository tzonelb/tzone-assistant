"""Tests for Google Chat as a channel a company connects.

Google Chat is the second-hardest channel built here, after SMS, and for a
related reason: like Twilio, it authenticates with a signed JWT rather than a
raw-body HMAC. Unlike every other channel:

* The credential is a whole service account JSON key file, not a single
  token -- `google_chat_parse_service_account` pulls `client_email` (the
  derived routing id, the same "ask the provider" role Viber's and LINE's
  own lookups play) and `private_key` (the one thing actually sealed) out of
  it, and `google_chat_mint_access_token` proves the pasted key is real by
  using it to get an access token from Google.
* The webhook's signature is a JWT Google itself signs (an OIDC id token),
  verified against Google's own public keys -- not a secret this platform
  and Google share, the way every other channel's signature is. There is no
  raw-body concatenation to reimplement independently here; the fixtures
  below build and sign real JWTs with a throwaway RSA key, and the webhook
  under test verifies them exactly the way it would verify Google's own.
* There is no `register_google_chat_webhook` -- Google Chat exposes no REST
  endpoint for it, so the operator pastes the URL into Google Cloud Console
  themselves (see `channels/google_chat/webhook.py`'s own docstring).
"""

from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import jwt as pyjwt


# One throwaway RSA keypair for the whole file -- generating a 2048-bit key
# is slow enough (tens of milliseconds) that doing it per test would add up
# across the number of tests here, and there is nothing test-specific about
# the key itself.
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_KEY_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_PUBLIC_KEY = _PRIVATE_KEY.public_key()

# A second, unrelated keypair -- stands in for an attacker's own key when a
# test needs a JWT that is signed by *something*, just not the key Google
# Chat's own public keys would validate.
_OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

CLIENT_EMAIL = "bot@test-project.iam.gserviceaccount.com"
PROJECT_ID = "test-project"
CHAT_SERVICE_ACCOUNT_EMAIL = "chat@system.gserviceaccount.com"


def _service_account_json(*, client_email: str = CLIENT_EMAIL, private_key: str = _PRIVATE_KEY_PEM) -> str:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": PROJECT_ID,
            "private_key_id": "abc123",
            "private_key": private_key,
            "client_email": client_email,
            "client_id": "12345",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.google_chat.webhook  # noqa: F401

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
        "channels.google_chat.webhook",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


class _TokenMintResponse:
    def __init__(self, *, status_code: int = 200, access_token: str | None = "fake-access-token"):
        self.status_code = status_code
        self._access_token = access_token

    def json(self):
        return {"access_token": self._access_token} if self._access_token else {}


def _connect(
    company,
    monkeypatch,
    *,
    raw_json: str | None = None,
    mint_response: _TokenMintResponse | None = None,
):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx,
        "post",
        lambda *args, **kwargs: mint_response or _TokenMintResponse(),
    )

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="google_chat",
        name="Support bot",
        values={"access_token": raw_json if raw_json is not None else _service_account_json()},
    )


# ------------------------------------------------------------------ the key


def test_the_bot_id_is_derived_from_the_service_account_email(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    assert account["external_account_id"] == CLIENT_EMAIL


def test_credentials_google_rejects_are_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="Google"):
        _connect(alpha, monkeypatch, mint_response=_TokenMintResponse(status_code=401))


def test_connecting_without_a_key_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError, match="service account JSON key"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="google_chat",
            name="Support bot",
            values={},
        )


def test_connecting_with_malformed_json_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="service account key file"):
        _connect(alpha, monkeypatch, raw_json="not json at all")


def test_connecting_with_a_key_missing_required_fields_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="client_email"):
        _connect(alpha, monkeypatch, raw_json=json.dumps({"project_id": PROJECT_ID}))


def test_two_companies_cannot_claim_the_same_bot(wired, alpha, beta, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    _connect(alpha, monkeypatch)

    with pytest.raises(ChannelAccountError):
        _connect(beta, monkeypatch)


def test_the_private_key_is_not_stored_in_the_clear(wired, platform, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed, config_json FROM channel_accounts "
            "WHERE channel = 'google_chat'"
        ).fetchone()

    assert row["access_token_sealed"]
    assert "PRIVATE KEY" not in str(row["access_token_sealed"])

    # And only the private key is sealed -- the rest of the pasted JSON
    # (including the key a second time) never reaches the sealed column or
    # the plain config, which is what proves `_validate` genuinely narrowed
    # the credential down rather than sealing the whole file.
    assert "PRIVATE KEY" not in (row["config_json"] or "")
    assert CLIENT_EMAIL not in (row["config_json"] or "")


def test_the_config_holds_only_the_project_id(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    assert account["config"] == {"project_id": PROJECT_ID}


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    match = wired.resolve_account_for_channel(channel="google_chat", page_id=CLIENT_EMAIL)

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_a_bot_nobody_connected_resolves_to_nothing(wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(
            channel="google_chat", page_id="nobody@nowhere.iam.gserviceaccount.com"
        )
        is None
    )


# -------------------------------------------------------- minting a token


def test_google_chat_mint_access_token_returns_a_token_on_success(monkeypatch):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx, "post", lambda *a, **k: _TokenMintResponse(access_token="tok-123")
    )

    token = service_module.google_chat_mint_access_token(
        client_email=CLIENT_EMAIL, private_key=_PRIVATE_KEY_PEM
    )

    assert token == "tok-123"


def test_google_chat_mint_access_token_raises_when_google_refuses(monkeypatch):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx, "post", lambda *a, **k: _TokenMintResponse(status_code=400)
    )

    with pytest.raises(service_module.ChannelAccountError, match="Google rejected"):
        service_module.google_chat_mint_access_token(
            client_email=CLIENT_EMAIL, private_key=_PRIVATE_KEY_PEM
        )


def test_google_chat_mint_access_token_raises_on_an_unusable_key(monkeypatch):
    import backend.services.channel_account_service as service_module

    with pytest.raises(service_module.ChannelAccountError, match="not a usable RSA key"):
        service_module.google_chat_mint_access_token(
            client_email=CLIENT_EMAIL, private_key="not a real PEM key"
        )


def test_google_chat_parse_service_account_extracts_fields():
    from backend.services.channel_account_service import google_chat_parse_service_account

    parsed = google_chat_parse_service_account(_service_account_json())

    assert parsed["client_email"] == CLIENT_EMAIL
    # `.strip()`ped by `google_chat_parse_service_account`, unlike the module
    # constant it came from, which still carries `cryptography`'s own
    # trailing newline.
    assert parsed["private_key"] == _PRIVATE_KEY_PEM.strip()
    assert parsed["project_id"] == PROJECT_ID


# ------------------------------------------------------------ the webhook auth


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from channels.google_chat import webhook as google_chat_webhook

    app = FastAPI()
    app.include_router(google_chat_webhook.router)

    return TestClient(app)


PUBLIC_URL = "https://example.tzone.app"


@pytest.fixture(autouse=True)
def _public_url(monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "APP_PUBLIC_URL", PUBLIC_URL)


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


def _mock_jwk_client(monkeypatch, *, public_key=_PUBLIC_KEY):
    """Stand in for fetching Google's real public keys over the network:
    the webhook always asks this mock for "the key this token's `kid` names",
    and every test controls what it gets back."""
    from channels.google_chat import webhook as webhook_module

    monkeypatch.setattr(
        webhook_module._jwk_client,
        "get_signing_key_from_jwt",
        lambda token: _FakeSigningKey(public_key),
    )


def _google_id_token(
    *,
    account_id: int,
    signing_key=_PRIVATE_KEY,
    issuer: str = "https://accounts.google.com",
    email: str = CHAT_SERVICE_ACCOUNT_EMAIL,
    audience: str | None = None,
    expired: bool = False,
) -> str:
    now = int(time.time())
    aud = audience if audience is not None else f"{PUBLIC_URL}/webhook/google_chat/{account_id}"

    return pyjwt.encode(
        {
            "iss": issuer,
            "email": email,
            "aud": aud,
            "iat": now - 60,
            "exp": now - 1 if expired else now + 3600,
        },
        signing_key,
        algorithm="RS256",
    )


def _message_event(*, text: str = "hello", space: str = "spaces/AAAAxxxxxx") -> dict:
    return {
        "type": "MESSAGE",
        "message": {"name": "spaces/AAAAxxxxxx/messages/m1", "text": text},
        "space": {"name": space, "spaceType": "DIRECT_MESSAGE"},
        "user": {"name": "users/123", "displayName": "A Customer"},
    }


def test_a_delivery_with_a_valid_jwt_is_accepted(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    token = _google_id_token(account_id=account["id"])

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {}


def test_a_delivery_with_no_bearer_token_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    response = client.post(f"/webhook/google_chat/{account['id']}", json=_message_event())

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_audience_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    token = _google_id_token(account_id=account["id"], audience="https://a-different-app.example")

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_a_delivery_signed_by_the_wrong_key_is_refused(client, wired, alpha, monkeypatch):
    """The mock still hands back the *legitimate* public key -- the same key
    Google's real endpoint would -- so this pins that a token merely signed
    by *some* RSA key is not enough; it must be signed by the key the
    audience's own trusted signer actually holds."""
    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    token = _google_id_token(account_id=account["id"], signing_key=_OTHER_PRIVATE_KEY)

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_issuer_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    token = _google_id_token(account_id=account["id"], issuer="https://not-google.example")

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_email_claim_is_refused(client, wired, alpha, monkeypatch):
    """A token that is genuinely Google-signed but for a *different* Google
    service is not Google Chat -- this is what actually narrows "any
    Google-issued token" down to "Google Chat's own outbound calls"."""
    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    token = _google_id_token(account_id=account["id"], email="someone-else@system.gserviceaccount.com")

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_an_expired_token_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    token = _google_id_token(account_id=account["id"], expired=True)

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_an_unknown_account_id_is_refused(client, wired):
    token = _google_id_token(account_id=999999)

    response = client.post(
        "/webhook/google_chat/999999",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_a_disabled_accounts_delivery_is_refused(client, wired, alpha, monkeypatch):
    from backend.services.channel_account_service import channel_account_service

    account = _connect(alpha, monkeypatch)
    channel_account_service.update_account(
        company_id=alpha["id"], account_id=account["id"], values={"status": "disabled"},
    )
    _mock_jwk_client(monkeypatch)

    token = _google_id_token(account_id=account["id"])

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json=_message_event(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_a_non_message_event_is_accepted_but_not_dispatched(client, wired, alpha, monkeypatch):
    import channels.google_chat.webhook as webhook_module

    account = _connect(alpha, monkeypatch)
    _mock_jwk_client(monkeypatch)

    captured = {}
    monkeypatch.setattr(
        webhook_module, "dispatch", lambda *a, **k: captured.setdefault("called", True)
    )

    token = _google_id_token(account_id=account["id"])

    response = client.post(
        f"/webhook/google_chat/{account['id']}",
        json={"type": "ADDED_TO_SPACE", "space": {"name": "spaces/AAAA"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {}
    assert "called" not in captured


# ------------------------------------------------------------------ the parser


def test_a_message_event_is_parsed_into_a_normalised_event():
    from channels.google_chat.webhook import parse_google_chat_event

    event = parse_google_chat_event(_message_event(text="Do you have this in blue?"))

    assert event == {
        "channel": "google_chat",
        "user_id": "spaces/AAAAxxxxxx",
        "text": "Do you have this in blue?",
        "message_id": "spaces/AAAAxxxxxx/messages/m1",
    }


def test_a_non_message_event_is_ignored():
    from channels.google_chat.webhook import parse_google_chat_event

    assert parse_google_chat_event({"type": "ADDED_TO_SPACE", "space": {"name": "spaces/AAAA"}}) is None


def test_a_message_with_no_text_is_ignored():
    from channels.google_chat.webhook import parse_google_chat_event

    assert parse_google_chat_event(_message_event(text="")) is None


def test_a_message_with_no_space_is_ignored():
    from channels.google_chat.webhook import parse_google_chat_event

    assert parse_google_chat_event(_message_event(space="")) is None


# ------------------------------------------------------------------ the sender


def test_google_chat_is_reachable_through_the_shared_dispatcher():
    from channels.sender import SUPPORTED_CHANNELS

    assert "google_chat" in SUPPORTED_CHANNELS


def test_send_text_actually_calls_the_google_chat_sender(monkeypatch):
    """The same gap pinned for every earlier channel: being listed in
    `SUPPORTED_CHANNELS` does not by itself prove `send_text` calls
    anything."""
    import channels.sender as sender_module

    captured = {}

    def fake_send_google_chat_text(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr(sender_module, "send_google_chat_text", fake_send_google_chat_text)

    result = sender_module.send_text(
        channel="google_chat", recipient_id="spaces/AAAAxxxxxx", company_id=1, text="hi"
    )

    assert result["ok"] is True
    assert captured["recipient_id"] == "spaces/AAAAxxxxxx"


def test_the_sender_mints_a_token_and_posts_to_the_right_space(wired, alpha, monkeypatch):
    from channels.google_chat.sender import send_google_chat_text

    _connect(alpha, monkeypatch)

    captured = {}

    def fake_post(url, **kwargs):
        if url == "https://oauth2.googleapis.com/token":
            return _TokenMintResponse(access_token="minted-token")

        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"name": "spaces/AAAAxxxxxx/messages/m2"}

        return Response()

    import channels.google_chat.sender as sender_module

    monkeypatch.setattr(sender_module.httpx, "post", fake_post)

    result = send_google_chat_text(
        recipient_id="spaces/AAAAxxxxxx",
        text="It shipped yesterday.",
        company_id=alpha["id"],
    )

    assert result["ok"] is True
    assert result["response"]["message_id"] == "spaces/AAAAxxxxxx/messages/m2"
    assert captured["url"] == f"{sender_module.API_BASE}/spaces/AAAAxxxxxx/messages"
    assert captured["headers"]["Authorization"] == "Bearer minted-token"
    assert captured["json"] == {"text": "It shipped yesterday."}


def test_the_sender_returns_a_clear_error_when_google_refuses_the_token_mint(
    wired, alpha, monkeypatch
):
    from channels.google_chat.sender import send_google_chat_text

    _connect(alpha, monkeypatch)

    import channels.google_chat.sender as sender_module

    monkeypatch.setattr(
        sender_module.httpx, "post", lambda *a, **k: _TokenMintResponse(status_code=403)
    )

    result = send_google_chat_text(
        recipient_id="spaces/AAAAxxxxxx", text="hi", company_id=alpha["id"],
    )

    assert result["ok"] is False
    assert result["skipped"] is False
    assert "authenticate" in result["error"].lower()


def test_sending_without_a_connected_account_fails_rather_than_raising(wired, alpha):
    from channels.google_chat.sender import send_google_chat_text

    result = send_google_chat_text(
        recipient_id="spaces/AAAAxxxxxx", text="hi", company_id=alpha["id"],
    )

    assert result["ok"] is False
    assert result["skipped"] is False
