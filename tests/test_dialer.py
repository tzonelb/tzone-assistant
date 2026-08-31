"""The Dialer: the live phone line, and what it does when there isn't one.

Most installations of this platform have no telephony account, and that is the
state most of this file is about. A module that needs credentials the company
has not bought must be *off*, not broken: the screen has to say what is missing,
every write has to refuse with a reason rather than a stack trace, and no half
of a call may be recorded for a call that was never placed. Those are the first
tests below, because that is the state the code is in on the day it ships.

The rest are the two things that are dangerous when the line *is* connected.

Making a phone ring spends the company's money and speaks in its name, so it
takes `dialer.use` — while reading the dialer's own state does not, because a
screen that cannot tell you whether calling is set up is a screen nobody can be
sent to. And the provider's callbacks arrive with no session at all, so the
signature is the only thing standing between a stranger and the ability to mark
any call completed. It is checked before a single field of the body is read, and
with no token configured nothing verifies, so everything is refused.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import sys

import pytest


PASSWORD = "AgentPass123456"

TOKEN = "test-auth-token"
BASE_URL = "https://calls.example.com"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.dialer  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.call_log_service  # noqa: F401
    import backend.services.telephony_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.telephony_service" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture(autouse=True)
def unconfigured(monkeypatch):
    """The state a fresh install is in: no provider, no public URL.

    Set explicitly rather than assumed, so a developer with TWILIO_* in their
    own environment runs the same tests as CI.
    """
    from backend.services.telephony_service import NullProvider, telephony_service
    from config.settings import config

    for name in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "PUBLIC_BASE_URL",
    ):
        monkeypatch.setattr(config, name, "")

    monkeypatch.setattr(telephony_service, "provider", NullProvider())


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, dialer

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(dialer.router)
    app.include_router(dialer.webhooks_router)

    return TestClient(app)


def _token(client, service, company, email, role):
    user_id = service.create_user(email, PASSWORD, "Person")
    service.assign_user_to_company(user_id, company["id"], role)

    response = client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text

    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def owner(client, service, alpha):
    return _token(client, service, alpha, "owner@alpha.example.com", "owner")


@pytest.fixture()
def agent(client, service, alpha):
    return _token(client, service, alpha, "agent@alpha.example.com", "agent")


@pytest.fixture()
def beta_owner(client, service, beta):
    return _token(client, service, beta, "owner@beta.example.com", "owner")


class _StubProvider:
    """A provider that answers instead of dialling."""

    name = "twilio"

    def __init__(self):
        self.placed = []
        self.transferred = []
        self.hungup = []

    def is_configured(self):
        return True

    def place_call(self, *, to_number, webhook_base):
        self.placed.append((to_number, webhook_base))
        return {"provider_call_id": f"CA{len(self.placed):032d}"}

    def transfer_call(self, *, provider_call_id, to_number):
        self.transferred.append((provider_call_id, to_number))

    def hangup_call(self, *, provider_call_id):
        self.hungup.append(provider_call_id)


@pytest.fixture()
def connected(monkeypatch):
    """A configured line, with a stub where the provider's REST API would be."""
    from backend.services.telephony_service import telephony_service
    from config.settings import config

    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TWILIO_PHONE_NUMBER", "+96170000000")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", BASE_URL)

    provider = _StubProvider()
    monkeypatch.setattr(telephony_service, "provider", provider)

    return provider


def _sign(path: str, params: dict[str, str], token: str = TOKEN) -> str:
    payload = BASE_URL + path + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(
        token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()

    return base64.b64encode(digest).decode("utf-8")


# ------------------------------------------------------- with no phone line


def test_the_screen_is_told_what_is_missing(client, owner):
    """"Not configured" sends an administrator through documentation. The list
    of names sends them to four lines of one file."""
    status = client.get("/api/dialer/status", headers=_headers(owner))

    assert status.status_code == 200, status.text
    assert status.json()["configured"] is False
    assert status.json()["from_number"] is None
    assert set(status.json()["missing"]) == {
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "PUBLIC_BASE_URL",
    }


def test_placing_a_call_with_no_line_is_refused_not_crashed(client, owner):
    refused = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    )

    assert refused.status_code == 503, refused.text
    assert "TWILIO_ACCOUNT_SID" in refused.json()["detail"]


def test_a_refused_call_leaves_no_row_claiming_it_is_ringing(client, owner):
    """The provider is asked before the row is written. The reverse order
    leaves a call in the history that nobody is on and no callback will ever
    finish."""
    client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    )

    listed = client.get("/api/dialer/calls", headers=_headers(owner))

    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": [], "total": 0}


# --------------------------------------------------------------- permission


def test_an_agent_may_look_at_the_dialer_and_not_use_it(client, agent):
    """Reading the state is how the screen knows what to draw. Ringing a
    customer's phone in the company's name is the part that is granted."""
    assert client.get("/api/dialer/status", headers=_headers(agent)).status_code == 200
    assert client.get("/api/dialer/calls", headers=_headers(agent)).status_code == 200

    refused = client.post(
        "/api/dialer/calls",
        headers=_headers(agent),
        json={"to_number": "+96170111222"},
    )
    assert refused.status_code == 403, refused.text
    assert "dialer.use" in refused.json()["detail"]


def test_dialer_use_is_a_permission_a_company_can_actually_grant():
    """A permission no company's database holds is an endpoint closed to
    everyone, owner included — a silent outage rather than a restriction."""
    from database.schema_control import DEFAULT_PERMISSIONS

    assert "dialer.use" in {code for code, _name, _description in DEFAULT_PERMISSIONS}


# ----------------------------------------------------------- with a line up


def test_a_placed_call_is_recorded_and_listed(client, owner, connected):
    placed = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    )

    assert placed.status_code == 201, placed.text
    assert placed.json()["status"] == "queued"
    assert placed.json()["direction"] == "outbound"
    assert connected.placed == [("+96170111222", BASE_URL)]

    listed = client.get("/api/dialer/calls", headers=_headers(owner))
    assert listed.json()["total"] == 1


def test_a_call_can_only_be_transferred_to_a_colleague_with_a_number(
    client, owner, connected, service, alpha
):
    call_id = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    ).json()["id"]

    colleague = service.create_user("colleague@alpha.example.com", PASSWORD, "Colleague")
    service.assign_user_to_company(colleague, alpha["id"], "agent")

    refused = client.post(
        f"/api/dialer/calls/{call_id}/transfer",
        headers=_headers(owner),
        json={"employee_user_id": colleague},
    )

    assert refused.status_code == 422, refused.text
    assert "phone number" in refused.json()["detail"]
    assert connected.transferred == []


def test_a_call_cannot_be_transferred_to_somebody_else_s_employee(
    client, owner, connected, service, beta
):
    call_id = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    ).json()["id"]

    outsider = service.create_user("outsider@beta.example.com", PASSWORD, "Outsider")
    service.assign_user_to_company(outsider, beta["id"], "agent")

    refused = client.post(
        f"/api/dialer/calls/{call_id}/transfer",
        headers=_headers(owner),
        json={"employee_user_id": outsider},
    )

    assert refused.status_code == 422, refused.text
    assert connected.transferred == []


def test_ending_a_call_tells_the_provider_and_marks_it_over(
    client, owner, connected
):
    call = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    ).json()

    ended = client.post(
        f"/api/dialer/calls/{call['id']}/hangup", headers=_headers(owner)
    )

    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "completed"
    assert connected.hungup == [call["provider_call_id"]]

    # Pressing it twice is not an error: the call is already over.
    again = client.post(
        f"/api/dialer/calls/{call['id']}/hangup", headers=_headers(owner)
    )
    assert again.status_code == 200, again.text


def test_a_call_from_another_company_cannot_be_ended(
    client, owner, beta_owner, connected
):
    call_id = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    ).json()["id"]

    refused = client.post(
        f"/api/dialer/calls/{call_id}/hangup", headers=_headers(beta_owner)
    )

    assert refused.status_code == 404, refused.text
    assert connected.hungup == []


def test_one_companys_calls_never_reach_another(client, owner, beta_owner, connected):
    client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    )

    other = client.get("/api/dialer/calls", headers=_headers(beta_owner))

    assert other.json() == {"items": [], "total": 0}


# ------------------------------------------------------------------ webhooks


def test_an_unsigned_callback_is_refused(client, connected):
    refused = client.post(
        "/api/dialer/webhooks/status", data={"CallSid": "CA1", "CallStatus": "completed"}
    )

    assert refused.status_code == 403, refused.text


def test_a_callback_signed_with_the_wrong_token_is_refused(client, connected):
    params = {"CallSid": "CA1", "CallStatus": "completed"}

    refused = client.post(
        "/api/dialer/webhooks/status",
        data=params,
        headers={"X-Twilio-Signature": _sign("/api/dialer/webhooks/status", params, "not-the-token")},
    )

    assert refused.status_code == 403, refused.text


def test_with_no_token_configured_every_callback_is_refused(client):
    """Nothing to verify against means nothing is verified. Accepting the
    request instead would make an unconfigured deployment the open one."""
    params = {"CallSid": "CA1", "CallStatus": "completed"}

    refused = client.post(
        "/api/dialer/webhooks/status",
        data=params,
        headers={"X-Twilio-Signature": _sign("/api/dialer/webhooks/status", params, "")},
    )

    assert refused.status_code == 403, refused.text


def test_a_finished_call_reaches_the_company_s_history(
    client, owner, connected, platform, alpha
):
    """The Dialer keeps the provider's bookkeeping; the Calls log keeps the
    company's history. A finished call has to cross from one to the other, or
    the history quietly excludes every call the platform itself placed."""
    call = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    ).json()

    params = {
        "CallSid": call["provider_call_id"],
        "CallStatus": "completed",
        "CallDuration": "42",
    }

    accepted = client.post(
        "/api/dialer/webhooks/status",
        data=params,
        headers={"X-Twilio-Signature": _sign("/api/dialer/webhooks/status", params)},
    )
    assert accepted.status_code == 200, accepted.text

    with platform["manager"].tenant(alpha["id"]) as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM call_logs").fetchall()]

    assert len(rows) == 1
    assert rows[0]["duration_seconds"] == 42
    assert rows[0]["status"] == "completed"
    assert rows[0]["phone_number"] == "+96170111222"


def test_a_recording_is_attached_to_the_call_it_belongs_to(
    client, owner, connected
):
    call = client.post(
        "/api/dialer/calls",
        headers=_headers(owner),
        json={"to_number": "+96170111222"},
    ).json()

    params = {
        "CallSid": call["provider_call_id"],
        "RecordingUrl": "https://api.twilio.com/recordings/RE1",
    }

    accepted = client.post(
        "/api/dialer/webhooks/recording",
        data=params,
        headers={"X-Twilio-Signature": _sign("/api/dialer/webhooks/recording", params)},
    )
    assert accepted.status_code == 200, accepted.text

    listed = client.get("/api/dialer/calls", headers=_headers(owner)).json()

    assert listed["items"][0]["recording_url"] == "https://api.twilio.com/recordings/RE1"


def test_an_answered_call_greets_the_caller_and_records_a_message(client, connected):
    params = {"CallSid": "CA-inbound", "From": "+96171999888"}

    answered = client.post(
        "/api/dialer/webhooks/inbound",
        data=params,
        headers={"X-Twilio-Signature": _sign("/api/dialer/webhooks/inbound", params)},
    )

    assert answered.status_code == 200, answered.text
    assert answered.headers["content-type"].startswith("application/xml")
    assert "<Record" in answered.text
    assert "<Say>" in answered.text


def test_an_inbound_call_is_not_filed_against_a_guessed_company(
    client, connected, platform, alpha, beta
):
    """One deployment-wide number and several companies is a call that belongs
    to none of them. Filing it against the lowest company id would put a
    stranger's phone number into another company's records, so it is answered
    and left unfiled instead."""
    params = {"CallSid": "CA-inbound", "From": "+96171999888"}

    client.post(
        "/api/dialer/webhooks/inbound",
        data=params,
        headers={"X-Twilio-Signature": _sign("/api/dialer/webhooks/inbound", params)},
    )

    for company in (alpha, beta):
        with platform["manager"].tenant(company["id"]) as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS total FROM telephony_calls"
            ).fetchone()["total"]

        assert total == 0


# ----------------------------------------------------- the signature itself


def test_the_signature_scheme_matches_the_one_twilio_documents():
    """Positive and negative vectors for the algorithm, computed here rather
    than taken from the implementation, so a change to either side has to be
    deliberate."""
    from backend.services.telephony_service import verify_twilio_signature

    url = "https://calls.example.com/api/dialer/webhooks/status"
    params = {"CallSid": "CA9", "CallStatus": "ringing", "Blank": ""}
    # Sorted by key, key and value run together, blanks included:
    # Blank, CallSid, CallStatus.
    payload = url + "Blank" + "" + "CallSid" + "CA9" + "CallStatus" + "ringing"
    expected = base64.b64encode(
        hmac.new(TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()

    assert verify_twilio_signature(
        url=url, params=params, signature=expected, auth_token=TOKEN
    )
    assert not verify_twilio_signature(
        url=url, params=params, signature=expected, auth_token="other-token"
    )
    assert not verify_twilio_signature(
        url=url, params=params, signature=None, auth_token=TOKEN
    )
    assert not verify_twilio_signature(
        url=url, params=params, signature=expected, auth_token=""
    )
    # A field the caller added after signing must not verify.
    assert not verify_twilio_signature(
        url=url,
        params={**params, "Extra": "1"},
        signature=expected,
        auth_token=TOKEN,
    )
