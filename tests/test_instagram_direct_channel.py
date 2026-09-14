"""Tests for Instagram (direct login) -- the unofficial channel.

Unlike every other channel built so far, connecting this one is not a single
call the service can validate synchronously: `backend/api/routes/
instagram_direct.py` runs the actual Instagram login itself, across two HTTP
requests when Instagram demands a 2FA code, with the live, partially-
authenticated `instagrapi.Client` held in an in-memory `_pending_logins`
cache between them. So this file tests the router directly, over HTTP with a
session, the same way `test_connecting_a_channel_through_the_api.py` tests
every other channel's connect endpoint -- plus the poller and sender, which
this channel also has and no other unofficial channel here does.

`instagrapi.Client` itself is never used: it would mean a real login attempt
against Instagram's servers. Every test that needs one substitutes a small
fake with the same surface the router, poller and sender actually call,
scripted per test. The one real dependency kept throughout is `instagrapi`'s
own exception classes and, for the poller, its own `DirectThread`/
`DirectMessage`/`UserShort` Pydantic types -- constructing the genuine types
catches a field this channel's code reads that they do not actually have,
which a hand-rolled fake object could not.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import pytest
from instagrapi import exceptions as ig_exceptions
from instagrapi.types import DirectMessage, DirectThread, UserShort


PASSWORD = "OwnerPass123!"


# ------------------------------------------------------------------ wiring


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.credentials  # noqa: F401
    import channels.instagram_direct.poller  # noqa: F401

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
        "channels.credentials",
        "channels.instagram_direct.poller",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager
    import database.manager as manager_module

    from backend.api.routes import auth, channels, instagram_direct

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, channels, instagram_direct):
        app.include_router(module.router)

    return TestClient(app, raise_server_exceptions=False)


def _make_owner(app_client, manager, company, *, email, code):
    from backend.services.auth_service import auth_service
    from backend.services import channel_verification_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name="Test Owner"
    )

    with manager.control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (company["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    response = app_client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text

    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    from unittest.mock import patch

    with patch.object(channel_verification_service, "_generate_code", lambda: code):
        request_sent = app_client.post(
            "/api/channels/verification/request", headers=headers
        )
        assert request_sent.status_code == 200, request_sent.text

        confirmed = app_client.post(
            "/api/channels/verification/confirm",
            json={"code": code},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text

    headers["X-Elevated-Token"] = confirmed.json()["elevated_token"]
    return headers


@pytest.fixture()
def owner(platform, alpha, app_client):
    return _make_owner(
        app_client, platform["manager"], alpha, email="owner@alpha.example.com", code="482913"
    )


@pytest.fixture()
def beta_owner(platform, beta, app_client):
    return _make_owner(
        app_client, platform["manager"], beta, email="owner@beta.example.com", code="739201"
    )


@pytest.fixture(autouse=True)
def _clean_pending_logins():
    """`_pending_logins` is a module-level dict, so one test's leftover entry
    could otherwise leak into the next."""
    from backend.api.routes import instagram_direct as router_module

    router_module._pending_logins.clear()
    yield
    router_module._pending_logins.clear()


# ------------------------------------------------------------------ fakes


class _FakeClient:
    """Stands in for `instagrapi.Client` in router tests.

    `login_sequence` is consumed in order, across both the initial
    username/password call and any later `verification_code` call: `None`
    means that call succeeds, an exception instance means it raises. A
    username passed to the first call is remembered so a later
    `verification_code`-only call (exactly like the real `login()`, which
    keeps the username/password it was first given) reports the same one.
    """

    _counter = 1000

    def __init__(self, *, login_sequence=None, fixed_user_id=None):
        self.login_sequence = list(login_sequence if login_sequence is not None else [None])
        self.proxy = None
        self.user_id = None
        self.username = None
        self._attempted_username = None
        self._fixed_user_id = fixed_user_id

    def set_proxy(self, url):
        if url == "https://bad-proxy.invalid":
            raise ValueError("that proxy could not be used")
        self.proxy = url

    def login(self, username=None, password=None, verification_code=""):
        if username is not None:
            self._attempted_username = username

        outcome = self.login_sequence.pop(0)

        if outcome is not None:
            raise outcome

        if self._fixed_user_id is not None:
            self.user_id = self._fixed_user_id
        else:
            _FakeClient._counter += 1
            self.user_id = _FakeClient._counter

        self.username = self._attempted_username or "resumed_user"
        return True

    def get_settings(self):
        return {"user_id": self.user_id, "cookies": {"sessionid": "fake-session"}}


def _set_fake_client(monkeypatch, *, login_sequence=None, fixed_user_id=None):
    from backend.api.routes import instagram_direct as router_module

    def factory(*args, **kwargs):
        return _FakeClient(login_sequence=login_sequence, fixed_user_id=fixed_user_id)

    monkeypatch.setattr(router_module, "Client", factory)


def _start_payload(**overrides):
    payload = {
        "name": "Support IG",
        "username": "shop_alpha",
        "password": "hunter2-not-real",
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ connect/start, no 2FA


def test_connecting_without_2fa_creates_the_account(app_client, owner, monkeypatch):
    _set_fake_client(monkeypatch, login_sequence=[None])

    response = app_client.post(
        "/api/instagram-direct/connect/start",
        json=_start_payload(),
        headers=owner,
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "connected"
    assert body["account"]["channel"] == "instagram_direct"
    assert body["account"]["has_access_token"] is True
    assert body["account"]["external_account_id"]


def test_connecting_never_puts_the_session_or_password_in_the_response(
    app_client, owner, monkeypatch
):
    _set_fake_client(monkeypatch, login_sequence=[None])

    response = app_client.post(
        "/api/instagram-direct/connect/start",
        json=_start_payload(proxy_url="socks5://residential.example:1080"),
        headers=owner,
    )

    account = response.json()["account"]

    assert "access_token" not in account
    assert "verify_token" not in account
    assert "hunter2-not-real" not in json.dumps(account)


def test_connect_stores_the_username_and_proxy_flag_in_config(
    app_client, owner, monkeypatch
):
    _set_fake_client(monkeypatch, login_sequence=[None])

    response = app_client.post(
        "/api/instagram-direct/connect/start",
        json=_start_payload(proxy_url="socks5://residential.example:1080"),
        headers=owner,
    )

    account = response.json()["account"]

    assert account["config"]["username"] == "shop_alpha"
    assert account["config"]["has_proxy"] is True
    assert account["has_verify_token"] is True


def test_connect_without_a_proxy_records_no_proxy(app_client, owner, monkeypatch):
    _set_fake_client(monkeypatch, login_sequence=[None])

    response = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )

    account = response.json()["account"]

    assert account["config"]["has_proxy"] is False
    assert account["has_verify_token"] is False


def test_an_unusable_proxy_is_refused_before_any_login_attempt(
    app_client, owner, monkeypatch
):
    _set_fake_client(monkeypatch, login_sequence=[None])

    response = app_client.post(
        "/api/instagram-direct/connect/start",
        json=_start_payload(proxy_url="https://bad-proxy.invalid"),
        headers=owner,
    )

    assert response.status_code == 400


def test_connecting_without_the_elevated_grant_is_refused(
    app_client, platform, alpha, monkeypatch
):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="plain@alpha.example.com", password=PASSWORD, full_name="No Grant"
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

    login = app_client.post(
        "/api/auth/login",
        json={
            "company": alpha["name"],
            "email": "plain@alpha.example.com",
            "password": PASSWORD,
        },
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    _set_fake_client(monkeypatch, login_sequence=[None])

    response = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=headers
    )

    assert response.status_code == 403


# ------------------------------------------------------------------ connect/start, refusals


def test_a_wrong_password_is_refused(app_client, owner, monkeypatch):
    _set_fake_client(
        monkeypatch, login_sequence=[ig_exceptions.BadPassword("bad password")]
    )

    response = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )

    assert response.status_code == 400
    assert response.json()["detail"]


def test_a_hard_checkpoint_is_refused_immediately_not_looped(app_client, owner, monkeypatch):
    """Retrying into a checkpoint is a documented path to a permanent ban --
    this channel treats it as a stop, never a retry."""
    _set_fake_client(
        monkeypatch, login_sequence=[ig_exceptions.ChallengeRequired("checkpoint")]
    )

    response = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )

    assert response.status_code == 409
    assert "Instagram app" in response.json()["detail"]


def test_an_unreachable_instagram_is_a_gateway_failure_not_a_client_error(
    app_client, owner, monkeypatch
):
    _set_fake_client(
        monkeypatch, login_sequence=[ig_exceptions.ClientError("connection reset")]
    )

    response = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )

    assert response.status_code == 502


def test_connecting_the_same_instagram_account_twice_is_refused(
    app_client, owner, monkeypatch
):
    _set_fake_client(monkeypatch, login_sequence=[None], fixed_user_id=555444333)

    first = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )
    assert first.status_code == 200, first.text

    _set_fake_client(monkeypatch, login_sequence=[None], fixed_user_id=555444333)

    second = app_client.post(
        "/api/instagram-direct/connect/start",
        json=_start_payload(name="Support IG (again)"),
        headers=owner,
    )

    assert second.status_code == 409


# ------------------------------------------------------------------ connect/verify (2FA)


def test_two_factor_then_the_right_code_connects(app_client, owner, monkeypatch):
    _set_fake_client(
        monkeypatch, login_sequence=[ig_exceptions.TwoFactorRequired("need code"), None]
    )

    started = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] == "needs_code"
    pending_id = body["pending_id"]

    verified = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": pending_id, "code": "123456"},
        headers=owner,
    )

    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "connected"
    assert verified.json()["account"]["config"]["username"] == "shop_alpha"


def test_a_wrong_code_can_be_retried_on_the_same_pending_login(
    app_client, owner, monkeypatch
):
    _set_fake_client(
        monkeypatch,
        login_sequence=[
            ig_exceptions.TwoFactorRequired("need code"),
            ig_exceptions.TwoFactorRequired("still wrong"),
            None,
        ],
    )

    started = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )
    pending_id = started.json()["pending_id"]

    wrong = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": pending_id, "code": "000000"},
        headers=owner,
    )
    assert wrong.status_code == 400

    retried = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": pending_id, "code": "123456"},
        headers=owner,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "connected"


def test_a_checkpoint_during_verify_ends_the_attempt(app_client, owner, monkeypatch):
    _set_fake_client(
        monkeypatch,
        login_sequence=[
            ig_exceptions.TwoFactorRequired("need code"),
            ig_exceptions.ChallengeRequired("checkpoint"),
        ],
    )

    started = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )
    pending_id = started.json()["pending_id"]

    challenged = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": pending_id, "code": "123456"},
        headers=owner,
    )
    assert challenged.status_code == 409

    gone = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": pending_id, "code": "123456"},
        headers=owner,
    )
    assert gone.status_code == 410


def test_verifying_an_unknown_pending_id_is_refused(app_client, owner):
    response = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": "does-not-exist", "code": "123456"},
        headers=owner,
    )

    assert response.status_code == 410


def test_a_pending_login_cannot_be_verified_by_another_company(
    app_client, owner, beta_owner, monkeypatch
):
    _set_fake_client(
        monkeypatch, login_sequence=[ig_exceptions.TwoFactorRequired("need code")]
    )

    started = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )
    pending_id = started.json()["pending_id"]

    stolen = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": pending_id, "code": "123456"},
        headers=beta_owner,
    )

    assert stolen.status_code == 410


def test_an_expired_pending_login_is_refused(app_client, owner, monkeypatch):
    from backend.api.routes import instagram_direct as router_module

    _set_fake_client(
        monkeypatch, login_sequence=[ig_exceptions.TwoFactorRequired("need code")]
    )

    started = app_client.post(
        "/api/instagram-direct/connect/start", json=_start_payload(), headers=owner
    )
    pending_id = started.json()["pending_id"]

    router_module._pending_logins[pending_id]["expires_at"] = time.monotonic() - 1

    expired = app_client.post(
        "/api/instagram-direct/connect/verify",
        json={"pending_id": pending_id, "code": "123456"},
        headers=owner,
    )

    assert expired.status_code == 410


# ------------------------------------------------------------------ poller


def _connect_via_service(
    company,
    *,
    external_account_id="7001",
    username="shop_alpha",
    settings=None,
    proxy=None,
):
    import backend.services.channel_account_service as service_module

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="instagram_direct",
        name="Support IG",
        values={
            "external_account_id": external_account_id,
            "access_token": json.dumps(settings or {"cookies": {}}),
            "_instagram_username": username,
            "verify_token": proxy,
        },
    )


def _message(*, message_id, text, is_sent_by_viewer=False, timestamp=None):
    return DirectMessage(
        id=message_id,
        user_id="9999",
        thread_id=123,
        timestamp=timestamp or datetime.now(timezone.utc),
        text=text,
        is_sent_by_viewer=is_sent_by_viewer,
    )


def _thread(*, thread_id="123", messages, users, pending=False):
    return DirectThread(
        pk=thread_id,
        id=thread_id,
        messages=messages,
        users=users,
        admin_user_ids=[],
        last_activity_at=datetime.now(timezone.utc),
        muted=False,
        named=False,
        canonical=True,
        pending=pending,
        archived=False,
        thread_type="private",
        thread_title="",
        folder=0,
        vc_muted=False,
        is_group=False,
        mentions_muted=False,
        approval_required_for_new_members=False,
        input_mode=0,
    )


class _FakePollerClient:
    def __init__(self, *, threads=None, pending=None, raise_on_poll=None):
        self.settings = None
        self.proxy = None
        self.threads = threads if threads is not None else []
        self.pending = pending if pending is not None else []
        self.raise_on_poll = raise_on_poll
        self.approved = []

    def set_settings(self, settings):
        self.settings = settings

    def set_proxy(self, proxy):
        self.proxy = proxy

    def direct_threads(self, amount=20, thread_message_limit=20):
        if self.raise_on_poll:
            raise self.raise_on_poll
        return self.threads

    def direct_pending_inbox(self, amount=20):
        if self.raise_on_poll:
            raise self.raise_on_poll
        return self.pending

    def direct_pending_approve(self, thread_id):
        self.approved.append(thread_id)


def _wire_poller_client(monkeypatch, fake):
    import channels.instagram_direct.poller as poller_module

    monkeypatch.setattr(poller_module, "Client", lambda *a, **k: fake)


def test_poll_account_delivers_a_new_customer_message(wired, alpha, monkeypatch):
    import channels.instagram_direct.poller as poller_module

    account = _connect_via_service(alpha)

    thread = _thread(
        messages=[
            _message(message_id="m1", text="hello there"),
            _message(message_id="m2", text="hi, how can we help?", is_sent_by_viewer=True),
        ],
        users=[UserShort(pk="9999", username="a_customer")],
    )
    _wire_poller_client(monkeypatch, _FakePollerClient(threads=[thread]))

    delivered = []
    monkeypatch.setattr(
        poller_module,
        "process_inbound_event",
        lambda **kwargs: delivered.append(kwargs) or {},
    )

    poller_module.poll_account(account["id"])

    assert len(delivered) == 1
    event = delivered[0]["event"]
    assert event["text"] == "hello there"
    assert event["channel"] == "instagram_direct"
    assert event["customer_name"] == "a_customer"
    assert delivered[0]["company_id"] == alpha["id"]
    assert delivered[0]["channel_account_id"] == account["id"]


def test_poll_account_does_not_redeliver_messages_already_past_the_cursor(
    wired, alpha, monkeypatch
):
    import channels.instagram_direct.poller as poller_module

    account = _connect_via_service(alpha)
    seen_at = datetime.now(timezone.utc)

    with wired.control() as conn:
        conn.execute(
            "UPDATE channel_accounts SET config_json = ? WHERE id = ?",
            (json.dumps({"last_message_ts": seen_at.timestamp() + 10}), account["id"]),
        )
        conn.commit()

    thread = _thread(
        messages=[_message(message_id="m1", text="already seen", timestamp=seen_at)],
        users=[UserShort(pk="9999", username="a_customer")],
    )
    _wire_poller_client(monkeypatch, _FakePollerClient(threads=[thread]))

    delivered = []
    monkeypatch.setattr(
        poller_module,
        "process_inbound_event",
        lambda **kwargs: delivered.append(kwargs) or {},
    )

    poller_module.poll_account(account["id"])

    assert delivered == []


def test_poll_account_approves_a_pending_request_with_a_real_message(
    wired, alpha, monkeypatch
):
    import channels.instagram_direct.poller as poller_module

    account = _connect_via_service(alpha)

    thread = _thread(
        thread_id="456",
        messages=[_message(message_id="m1", text="are you open?")],
        users=[UserShort(pk="9999", username="a_customer")],
        pending=True,
    )
    fake = _FakePollerClient(pending=[thread])
    _wire_poller_client(monkeypatch, fake)
    monkeypatch.setattr(poller_module, "process_inbound_event", lambda **kwargs: {})

    poller_module.poll_account(account["id"])

    assert fake.approved == ["456"]


def test_poll_account_does_not_approve_an_empty_pending_request(
    wired, alpha, monkeypatch
):
    import channels.instagram_direct.poller as poller_module

    account = _connect_via_service(alpha)

    thread = _thread(
        thread_id="456",
        messages=[_message(message_id="m1", text="", is_sent_by_viewer=False)],
        users=[UserShort(pk="9999", username="a_customer")],
        pending=True,
    )
    fake = _FakePollerClient(pending=[thread])
    _wire_poller_client(monkeypatch, fake)

    poller_module.poll_account(account["id"])

    assert fake.approved == []


def test_poll_account_advances_the_cursor_to_the_newest_message(wired, alpha, monkeypatch):
    import channels.instagram_direct.poller as poller_module

    account = _connect_via_service(alpha)
    newest = datetime.now(timezone.utc)

    thread = _thread(
        messages=[_message(message_id="m1", text="hi", timestamp=newest)],
        users=[UserShort(pk="9999", username="a_customer")],
    )
    _wire_poller_client(monkeypatch, _FakePollerClient(threads=[thread]))
    monkeypatch.setattr(poller_module, "process_inbound_event", lambda **kwargs: {})

    poller_module.poll_account(account["id"])

    with wired.control() as conn:
        row = conn.execute(
            "SELECT config_json FROM channel_accounts WHERE id = ?", (account["id"],)
        ).fetchone()

    stored = json.loads(row["config_json"])
    assert stored["last_message_ts"] == pytest.approx(newest.timestamp())
    # The username set at connect time must survive the merge-update.
    assert stored["username"] == "shop_alpha"


def test_poll_account_skips_an_account_whose_session_has_expired(
    wired, alpha, monkeypatch
):
    import channels.instagram_direct.poller as poller_module
    from instagrapi.exceptions import LoginRequired

    account = _connect_via_service(alpha)
    _wire_poller_client(
        monkeypatch, _FakePollerClient(raise_on_poll=LoginRequired("session expired"))
    )

    delivered = []
    monkeypatch.setattr(
        poller_module,
        "process_inbound_event",
        lambda **kwargs: delivered.append(kwargs) or {},
    )

    poller_module.poll_account(account["id"])  # must not raise

    assert delivered == []


def test_poll_account_survives_a_generic_instagram_error(wired, alpha, monkeypatch):
    import channels.instagram_direct.poller as poller_module
    from instagrapi.exceptions import ClientError

    account = _connect_via_service(alpha)
    _wire_poller_client(monkeypatch, _FakePollerClient(raise_on_poll=ClientError("boom")))

    poller_module.poll_account(account["id"])  # must not raise


def test_poll_all_accounts_isolates_one_companys_failure_from_another(
    wired, alpha, beta, monkeypatch
):
    import channels.instagram_direct.poller as poller_module

    account_a = _connect_via_service(alpha, external_account_id="7001")
    account_b = _connect_via_service(beta, external_account_id="7002")

    polled = []

    def _fake_poll_account(account_id):
        polled.append(account_id)
        if account_id == account_a["id"]:
            raise RuntimeError("account a exploded")

    monkeypatch.setattr(poller_module, "poll_account", _fake_poll_account)

    poller_module.poll_all_accounts()  # must not raise

    assert set(polled) == {account_a["id"], account_b["id"]}


def test_load_client_returns_none_without_a_session(wired, alpha):
    import channels.instagram_direct.poller as poller_module

    with wired.control() as conn:
        row = conn.execute(
            "SELECT id, company_id, access_token_sealed, verify_token_sealed, config_json "
            "FROM channel_accounts WHERE id = ?",
            (_connect_via_service(alpha, external_account_id="7003")["id"],),
        ).fetchone()

    # No session was ever sealed for this handcrafted row.
    fake_row = dict(row)
    fake_row["access_token_sealed"] = None

    client = poller_module._load_client(
        fake_row, company_id=alpha["id"], account_id=int(fake_row["id"])
    )

    assert client is None


# ------------------------------------------------------------------ sender


class _FakeSenderClient:
    def __init__(self, *, sent_id="msg-1", raise_error=None):
        self.settings = None
        self.proxy = None
        self.sent_id = sent_id
        self.raise_error = raise_error
        self.last_call = None

    def set_settings(self, settings):
        self.settings = settings

    def set_proxy(self, proxy):
        self.proxy = proxy

    def direct_send(self, text, thread_ids):
        self.last_call = {"text": text, "thread_ids": thread_ids}

        if self.raise_error:
            raise self.raise_error

        class _Message:
            id = self.sent_id

        return _Message()


def _wire_sender_client(monkeypatch, fake):
    import channels.instagram_direct.sender as sender_module

    monkeypatch.setattr(sender_module, "Client", lambda *a, **k: fake)


def test_send_delivers_through_the_connected_accounts_session(wired, alpha, monkeypatch):
    from channels.instagram_direct.sender import send_instagram_direct_text

    _connect_via_service(alpha, settings={"cookies": {"sessionid": "abc"}})
    fake = _FakeSenderClient(sent_id="ig-msg-42")
    _wire_sender_client(monkeypatch, fake)

    result = send_instagram_direct_text(
        recipient_id="123", text="Thanks for reaching out!", company_id=alpha["id"]
    )

    assert result["ok"] is True
    assert result["response"]["message_id"] == "ig-msg-42"
    assert fake.last_call == {"text": "Thanks for reaching out!", "thread_ids": [123]}


def test_send_uses_the_sealed_proxy_the_session_was_established_over(
    wired, alpha, monkeypatch
):
    from channels.instagram_direct.sender import send_instagram_direct_text

    _connect_via_service(alpha, proxy="socks5://residential.example:1080")
    fake = _FakeSenderClient()
    _wire_sender_client(monkeypatch, fake)

    send_instagram_direct_text(recipient_id="123", text="hi", company_id=alpha["id"])

    assert fake.proxy == "socks5://residential.example:1080"


def test_send_appends_buttons_as_plain_text(wired, alpha, monkeypatch):
    from channels.instagram_direct.sender import send_instagram_direct_text

    _connect_via_service(alpha)
    fake = _FakeSenderClient()
    _wire_sender_client(monkeypatch, fake)

    send_instagram_direct_text(
        recipient_id="123",
        text="Pick one:",
        company_id=alpha["id"],
        buttons=["Sales", "Support"],
    )

    assert "- Sales" in fake.last_call["text"]
    assert "- Support" in fake.last_call["text"]


def test_send_without_a_connected_account_fails_without_sending(wired, alpha, monkeypatch):
    from channels.instagram_direct.sender import send_instagram_direct_text

    fake = _FakeSenderClient()
    _wire_sender_client(monkeypatch, fake)

    result = send_instagram_direct_text(recipient_id="123", text="hi", company_id=alpha["id"])

    assert result["ok"] is False
    assert fake.last_call is None


def test_send_with_a_corrupted_session_fails_without_a_stale_retry(
    wired, alpha, monkeypatch
):
    from channels.instagram_direct.sender import send_instagram_direct_text

    _connect_via_service(alpha)

    # Overwrite the sealed session with something that unseals fine but is
    # not valid JSON -- the shape a hand-edited or truncated value would take.
    import backend.services.channel_account_service as service_module

    with wired.control() as conn:
        row = conn.execute(
            "SELECT id FROM channel_accounts WHERE company_id = ?", (alpha["id"],)
        ).fetchone()

    from backend.security import keyring

    with wired.control() as conn:
        conn.execute(
            "UPDATE channel_accounts SET access_token_sealed = ? WHERE id = ?",
            (
                keyring.seal_secret(
                    "not valid json", wired.company_key(alpha["id"]), alpha["id"], "access_token"
                ),
                row["id"],
            ),
        )
        conn.commit()

    fake = _FakeSenderClient()
    _wire_sender_client(monkeypatch, fake)

    result = send_instagram_direct_text(recipient_id="123", text="hi", company_id=alpha["id"])

    assert result["ok"] is False
    assert "session" in result["error"]
    assert fake.last_call is None


def test_send_failure_is_reported_not_raised(wired, alpha, monkeypatch):
    from channels.instagram_direct.sender import send_instagram_direct_text
    from instagrapi.exceptions import ClientError

    _connect_via_service(alpha)
    _wire_sender_client(monkeypatch, _FakeSenderClient(raise_error=ClientError("rate limited")))

    result = send_instagram_direct_text(recipient_id="123", text="hi", company_id=alpha["id"])

    assert result["ok"] is False
    assert result["error"]
