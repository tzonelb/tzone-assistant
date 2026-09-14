"""Tests for SMS (Twilio) as a channel a company connects.

SMS is the hardest channel built so far, in three ways `tests/test_email_
channel.py` and `tests/test_line_channel.py` only cover separately:

* Like email, the routing id (`ROUTING_FIELD["sms"] = "external_account_id"`)
  is typed in by the operator -- a phone number, not something any API call
  can derive -- and confirmed with a live provider call
  (`twilio_phone_number_sid`) before the account is ever saved.
* Unlike every other channel here, the webhook body is form-encoded
  (`application/x-www-form-urlencoded`), not JSON, so it is parsed with
  `urllib.parse.parse_qsl` rather than `json.loads`.
* Unlike every other channel's signature, Twilio's `X-Twilio-Signature` is
  not a raw-body HMAC: it is HMAC-SHA1 over the exact request URL with every
  POST parameter's name and value appended in sorted order.

What this file does not test: a reply sent inline from the webhook, because
`channels/sms/webhook.py` never composes one -- Twilio's messaging webhook
gets an empty TwiML document every time, the same "no reply token to use"
reasoning `channels/line/sender.py` documents for push-only delivery.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import urlencode

import pytest


ACCOUNT_SID = "ACtestfixturenotarealaccountsid0000"
AUTH_TOKEN = "test-fixture-not-a-real-twilio-auth-token"
PHONE_NUMBER = "+15550001111"
PHONE_NUMBER_SID = "PNtestfixturenotarealphonenumber000"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.sms.webhook  # noqa: F401

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
        "channels.sms.webhook",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


class _PhoneLookupResponse:
    def __init__(self, *, status_code: int = 200, phone_sid: str | None = PHONE_NUMBER_SID):
        self.status_code = status_code
        self._phone_sid = phone_sid

    def json(self):
        if self._phone_sid is None:
            return {"incoming_phone_numbers": []}

        return {"incoming_phone_numbers": [{"sid": self._phone_sid}]}


def _connect(
    company,
    monkeypatch,
    *,
    phone_number: str = PHONE_NUMBER,
    account_sid: str = ACCOUNT_SID,
    auth_token: str = AUTH_TOKEN,
    lookup_response: _PhoneLookupResponse | None = None,
):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx,
        "get",
        lambda *args, **kwargs: lookup_response or _PhoneLookupResponse(),
    )

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="sms",
        name="Support line",
        values={
            "external_account_id": phone_number,
            "account_sid": account_sid,
            "access_token": auth_token,
        },
    )


# ------------------------------------------------------------------ the token


def test_the_phone_number_is_confirmed_against_twilio(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    assert account["external_account_id"] == PHONE_NUMBER


def test_credentials_twilio_rejects_are_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="Twilio"):
        _connect(alpha, monkeypatch, lookup_response=_PhoneLookupResponse(status_code=401))


def test_a_number_not_on_the_account_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="not found"):
        _connect(alpha, monkeypatch, lookup_response=_PhoneLookupResponse(phone_sid=None))


def test_connecting_without_a_phone_number_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx, "get", lambda *args, **kwargs: _PhoneLookupResponse()
    )

    with pytest.raises(ChannelAccountError, match="phone number"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="sms",
            name="Support line",
            values={"account_sid": ACCOUNT_SID, "access_token": AUTH_TOKEN},
        )


def test_connecting_without_an_auth_token_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError, match="Auth Token"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="sms",
            name="Support line",
            values={"external_account_id": PHONE_NUMBER, "account_sid": ACCOUNT_SID},
        )


def test_connecting_without_an_account_sid_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError, match="Account SID"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="sms",
            name="Support line",
            values={"external_account_id": PHONE_NUMBER, "access_token": AUTH_TOKEN},
        )


def test_two_companies_cannot_claim_the_same_number(wired, alpha, beta, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    _connect(alpha, monkeypatch)

    with pytest.raises(ChannelAccountError):
        _connect(beta, monkeypatch, phone_number=PHONE_NUMBER)


def test_the_auth_token_is_not_stored_in_the_clear(wired, platform, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE channel = 'sms'"
        ).fetchone()

    assert row["access_token_sealed"]
    assert AUTH_TOKEN not in str(row["access_token_sealed"])


def test_the_account_sid_is_stored_plainly_in_config(wired, alpha, monkeypatch):
    """Twilio's own security model treats the Account SID as a public
    identifier, not a credential -- it already appears on every inbound
    webhook Twilio itself sends -- so it lives in the generic, non-sealed
    `config_json` column, unlike the Auth Token above."""
    account = _connect(alpha, monkeypatch)

    assert account["config"]["account_sid"] == ACCOUNT_SID
    assert account["config"]["phone_number_sid"] == PHONE_NUMBER_SID


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    match = wired.resolve_account_for_channel(channel="sms", page_id=PHONE_NUMBER)

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_a_number_nobody_connected_resolves_to_nothing(wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(channel="sms", page_id="+19995550000")
        is None
    )


# ---------------------------------------------------------- webhook registration


def test_register_sms_webhook_calls_the_right_endpoint(monkeypatch):
    import backend.services.channel_account_service as service_module
    from config.settings import config

    captured = {}

    def fake_post(url, *, data, auth, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["auth"] = auth

        class Response:
            status_code = 200

        return Response()

    monkeypatch.setattr(service_module.httpx, "post", fake_post)
    monkeypatch.setattr(config, "APP_PUBLIC_URL", "https://example.tzone.app")

    service_module.register_sms_webhook(
        account_sid=ACCOUNT_SID,
        auth_token=AUTH_TOKEN,
        phone_number_sid=PHONE_NUMBER_SID,
        account_id=42,
    )

    assert captured["url"] == (
        f"{service_module.TWILIO_API_BASE}/Accounts/{ACCOUNT_SID}"
        f"/IncomingPhoneNumbers/{PHONE_NUMBER_SID}.json"
    )
    assert captured["data"]["SmsUrl"] == "https://example.tzone.app/webhook/sms/42"
    assert captured["auth"] == (ACCOUNT_SID, AUTH_TOKEN)


def test_register_sms_webhook_raises_when_twilio_refuses(monkeypatch):
    import backend.services.channel_account_service as service_module

    class Response:
        status_code = 400

    monkeypatch.setattr(service_module.httpx, "post", lambda *a, **k: Response())

    with pytest.raises(service_module.ChannelAccountError, match="Twilio"):
        service_module.register_sms_webhook(
            account_sid=ACCOUNT_SID,
            auth_token=AUTH_TOKEN,
            phone_number_sid=PHONE_NUMBER_SID,
            account_id=1,
        )


# ------------------------------------------------------------ the webhook auth


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from channels.sms import webhook as sms_webhook

    app = FastAPI()
    app.include_router(sms_webhook.router)

    return TestClient(app)


_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# `_signing_url` in `channels/sms/webhook.py` is deliberately built from
# `config.APP_PUBLIC_URL`, not from the incoming request -- a proxy in front
# of this platform can present a different scheme than the one Twilio itself
# signed. Every signature test below pins that same value so the signature it
# computes matches what the webhook itself expects.
PUBLIC_URL = "https://example.tzone.app"


@pytest.fixture(autouse=True)
def _public_url(monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "APP_PUBLIC_URL", PUBLIC_URL)


def _twilio_signature(auth_token: str, url: str, fields: dict[str, str]) -> str:
    """Twilio's own algorithm: the full URL with every POST parameter's name
    and value appended, sorted by name, HMAC-SHA1'd with the Auth Token and
    base64-encoded. Reimplemented independently from `channels/sms/webhook.
    py`'s `_authenticate` so this test cannot pass by sharing a bug with it."""
    base = url

    for key, value in sorted(fields.items()):
        base += key + value

    digest = hmac.new(
        auth_token.encode("utf-8"), base.encode("utf-8"), hashlib.sha1
    ).digest()

    return base64.b64encode(digest).decode("ascii")


def _form_body(fields: dict[str, str]) -> bytes:
    return urlencode(fields).encode("utf-8")


def _sms_fields(*, from_number: str = "+15557778888", body: str = "hello") -> dict[str, str]:
    return {"From": from_number, "To": PHONE_NUMBER, "Body": body, "MessageSid": "SM123"}


def test_a_delivery_with_the_right_signature_is_accepted(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    fields = _sms_fields()
    url = f"{PUBLIC_URL}/webhook/sms/{account['id']}"
    signature = _twilio_signature(AUTH_TOKEN, url, fields)

    response = client.post(
        f"/webhook/sms/{account['id']}",
        content=_form_body(fields),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )

    assert response.status_code == 200, response.text
    assert response.text == _EMPTY_TWIML
    assert response.headers["content-type"].startswith("text/xml")


def test_a_delivery_with_no_signature_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    fields = _sms_fields()

    response = client.post(
        f"/webhook/sms/{account['id']}",
        content=_form_body(fields),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_signature_is_refused(client, wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    fields = _sms_fields()
    url = f"{PUBLIC_URL}/webhook/sms/{account['id']}"
    signature = _twilio_signature("a-completely-different-token", url, fields)

    response = client.post(
        f"/webhook/sms/{account['id']}",
        content=_form_body(fields),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )

    assert response.status_code == 403


def test_a_signature_computed_against_the_wrong_url_is_refused(
    client, wired, alpha, monkeypatch
):
    """Pins the reason `_signing_url` is built from `config.APP_PUBLIC_URL`
    rather than trusted from the incoming request: a signature computed
    against any other URL -- what a mismatched scheme behind a proxy would
    produce -- must not verify."""
    account = _connect(alpha, monkeypatch)

    fields = _sms_fields()
    wrong_url = f"http://a-different-host/webhook/sms/{account['id']}"
    signature = _twilio_signature(AUTH_TOKEN, wrong_url, fields)

    response = client.post(
        f"/webhook/sms/{account['id']}",
        content=_form_body(fields),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )

    assert response.status_code == 403


def test_an_unknown_account_id_is_refused(client, wired):
    fields = _sms_fields()
    url = f"{PUBLIC_URL}/webhook/sms/999999"
    signature = _twilio_signature(AUTH_TOKEN, url, fields)

    response = client.post(
        "/webhook/sms/999999",
        content=_form_body(fields),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )

    assert response.status_code == 403


def test_a_disabled_accounts_delivery_is_refused(client, wired, alpha, monkeypatch):
    from backend.services.channel_account_service import channel_account_service

    account = _connect(alpha, monkeypatch)
    channel_account_service.update_account(
        company_id=alpha["id"], account_id=account["id"], values={"status": "disabled"},
    )

    fields = _sms_fields()
    url = f"{PUBLIC_URL}/webhook/sms/{account['id']}"
    signature = _twilio_signature(AUTH_TOKEN, url, fields)

    response = client.post(
        f"/webhook/sms/{account['id']}",
        content=_form_body(fields),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )

    assert response.status_code == 403


def test_a_delivery_with_no_text_still_gets_an_empty_twiml_reply(
    client, wired, alpha, monkeypatch
):
    """An MMS with a picture and no caption, or any other event this parser
    drops, still has to satisfy Twilio's webhook contract -- the response is
    the same empty document either way, not an error."""
    account = _connect(alpha, monkeypatch)

    fields = {"From": "+15557778888", "To": PHONE_NUMBER, "Body": "", "NumMedia": "1"}
    url = f"{PUBLIC_URL}/webhook/sms/{account['id']}"
    signature = _twilio_signature(AUTH_TOKEN, url, fields)

    response = client.post(
        f"/webhook/sms/{account['id']}",
        content=_form_body(fields),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.text == _EMPTY_TWIML


# ------------------------------------------------------------------ the parser


def test_a_text_message_is_parsed_into_a_normalised_event():
    from channels.sms.webhook import parse_sms_event

    event = parse_sms_event(
        {"From": "+15557778888", "To": PHONE_NUMBER, "Body": "Do you have this in blue?", "MessageSid": "SM1"}
    )

    assert event == {
        "channel": "sms",
        "user_id": "+15557778888",
        "text": "Do you have this in blue?",
        "message_id": "SM1",
    }


def test_a_message_with_no_body_is_ignored():
    from channels.sms.webhook import parse_sms_event

    assert parse_sms_event({"From": "+15557778888", "To": PHONE_NUMBER, "Body": ""}) is None


def test_a_message_with_no_sender_is_ignored():
    from channels.sms.webhook import parse_sms_event

    assert parse_sms_event({"From": "", "To": PHONE_NUMBER, "Body": "hi"}) is None


# ------------------------------------------------------------------ the sender


def test_sms_is_reachable_through_the_shared_dispatcher():
    from channels.sender import SUPPORTED_CHANNELS

    assert "sms" in SUPPORTED_CHANNELS


def test_send_text_actually_calls_the_sms_sender(monkeypatch):
    """The same gap pinned for every earlier channel: being listed in
    `SUPPORTED_CHANNELS` does not by itself prove `send_text` calls
    anything."""
    import channels.sender as sender_module

    captured = {}

    def fake_send_sms_text(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr(sender_module, "send_sms_text", fake_send_sms_text)

    result = sender_module.send_text(
        channel="sms", recipient_id="+15557778888", company_id=1, text="hi"
    )

    assert result["ok"] is True
    assert captured["recipient_id"] == "+15557778888"


def test_the_sender_authenticates_with_basic_auth_and_posts_form_data(
    wired, alpha, monkeypatch
):
    from channels.sms.sender import send_sms_text

    _connect(alpha, monkeypatch)

    captured = {}

    def fake_post(url, *, auth, data, timeout):
        captured["url"] = url
        captured["auth"] = auth
        captured["data"] = data

        class Response:
            status_code = 201

            @staticmethod
            def json():
                return {"sid": "SM999"}

        return Response()

    import channels.sms.sender as sender_module

    monkeypatch.setattr(sender_module.httpx, "post", fake_post)

    result = send_sms_text(
        recipient_id="+15557778888",
        text="It shipped yesterday.",
        company_id=alpha["id"],
    )

    assert result["ok"] is True
    assert result["response"]["message_id"] == "SM999"
    assert captured["url"] == f"{sender_module.API_BASE}/Accounts/{ACCOUNT_SID}/Messages.json"
    assert captured["auth"] == (ACCOUNT_SID, AUTH_TOKEN)
    assert captured["data"] == {
        "To": "+15557778888",
        "From": PHONE_NUMBER,
        "Body": "It shipped yesterday.",
    }


def test_sending_without_a_connected_account_fails_rather_than_raising(wired, alpha):
    from channels.sms.sender import send_sms_text

    result = send_sms_text(
        recipient_id="+15557778888", text="hi", company_id=alpha["id"],
    )

    assert result["ok"] is False
    assert result["skipped"] is False
