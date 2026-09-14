"""Tests for Facebook (cookie download) -- the unofficial, read-only channel.

Three layers, tested separately:

* `channels/facebook_direct/browser.py`'s HTML-parsing functions are pure
  and deterministic, so they are tested directly against static HTML
  fixtures -- no Playwright, no network. These fixtures encode this
  module's own assumptions about Facebook's markup (see that module's
  docstring on why those assumptions are unverified against a live
  session); what these tests actually prove is that the parser behaves
  correctly *given* markup shaped the way it expects, and degrades to
  "nothing found" rather than crashing when it is not.
* `backend/api/routes/facebook_direct.py`'s connect endpoint is tested over
  real HTTP, the same way every other channel's connect route is in
  `test_connecting_a_channel_through_the_api.py` -- with
  `validate_session_and_fetch_page_name` swapped out so no test here ever
  launches a real browser.
* `channels/facebook_direct/poller.py` and the read-only guard in
  `channels/comment_sender.py` are tested by calling them directly, with
  `read_page_comments` swapped out the same way.
"""

from __future__ import annotations

import json
import sys

import pytest


PASSWORD = "OwnerPass123!"

VALID_COOKIES_JSON = json.dumps(
    [
        {"name": "c_user", "value": "100009999999999", "domain": ".facebook.com"},
        {"name": "xs", "value": "some-session-secret", "domain": ".facebook.com"},
    ]
)


# ------------------------------------------------------------------ browser.py parsing


def _comment_html(*, author_href, author_text, message, comment_id):
    return (
        f'<div class="comment">'
        f'<a href="{author_href}">{author_text}</a> '
        f"{message} "
        f'<a href="/comment/replies/?ctoken=abc&amp;comment_id={comment_id}">2h</a> Like'
        f"</div>"
    )


def test_extract_comments_reads_author_and_message_between_the_two_links():
    from channels.facebook_direct import browser

    html = _comment_html(
        author_href="/profile.php?id=100001234567890",
        author_text="Jane Doe",
        message="Hello, do you deliver to Amman?",
        comment_id="5551112223",
    )

    comments = browser._extract_comments(html, post_id="post1")

    assert len(comments) == 1
    comment = comments[0]
    assert comment["provider_comment_id"] == "5551112223"
    assert comment["post_id"] == "post1"
    assert comment["author_name"] == "Jane Doe"
    assert comment["author_external_id"] == "100001234567890"
    assert comment["message"] == "Hello, do you deliver to Amman?"


def test_extract_comments_handles_a_username_style_profile_link():
    from channels.facebook_direct import browser

    html = _comment_html(
        author_href="/john.smith.9",
        author_text="John Smith",
        message="What are your working hours?",
        comment_id="5551112224",
    )

    comments = browser._extract_comments(html, post_id="post1")

    assert comments[0]["author_external_id"] == "/john.smith.9"
    assert comments[0]["message"] == "What are your working hours?"


def test_extract_comments_reads_several_comments_on_one_post():
    from channels.facebook_direct import browser

    html = _comment_html(
        author_href="/profile.php?id=1",
        author_text="Alpha",
        message="First comment",
        comment_id="1001",
    ) + _comment_html(
        author_href="/profile.php?id=2",
        author_text="Beta",
        message="Second comment",
        comment_id="1002",
    )

    comments = browser._extract_comments(html, post_id="post1")

    assert [c["provider_comment_id"] for c in comments] == ["1001", "1002"]
    assert [c["message"] for c in comments] == ["First comment", "Second comment"]


def test_extract_comments_deduplicates_a_repeated_comment_id():
    from channels.facebook_direct import browser

    html = _comment_html(
        author_href="/profile.php?id=1",
        author_text="Alpha",
        message="Hi",
        comment_id="1001",
    )
    html = html + html

    comments = browser._extract_comments(html, post_id="post1")

    assert len(comments) == 1


def test_extract_comments_skips_a_comment_id_with_no_preceding_author_link():
    from channels.facebook_direct import browser

    html = '<div>Some text <a href="/comment/replies/?comment_id=9999">1h</a></div>'

    assert browser._extract_comments(html, post_id="post1") == []


def test_extract_comments_skips_when_the_preceding_link_is_not_a_profile():
    from channels.facebook_direct import browser

    html = (
        '<a href="/some/action">Like</a> a reply '
        '<a href="/comment/replies/?comment_id=9999">1h</a>'
    )

    assert browser._extract_comments(html, post_id="post1") == []


def test_extract_comments_skips_when_there_is_no_text_between_author_and_link():
    from channels.facebook_direct import browser

    html = (
        '<a href="/profile.php?id=1">Alpha</a>'
        '<a href="/comment/replies/?comment_id=9999">1h</a>'
    )

    assert browser._extract_comments(html, post_id="post1") == []


def test_extract_post_links_finds_story_fbid_links():
    from channels.facebook_direct import browser

    html = (
        '<a href="/story.php?story_fbid=111&id=999">Post one</a>'
        '<a href="/story.php?story_fbid=222&id=999">Post two</a>'
        '<a href="/story.php?story_fbid=111&id=999">Duplicate</a>'
    )

    paths = browser._extract_post_links(html, page_id="999")

    assert paths == [
        "/story.php?story_fbid=111&id=999",
        "/story.php?story_fbid=222&id=999",
    ]


def test_extract_post_links_returns_nothing_on_unrelated_markup():
    from channels.facebook_direct import browser

    assert browser._extract_post_links("<div>nothing here</div>", page_id="999") == []


def test_page_title_reads_the_title_tag():
    from channels.facebook_direct import browser

    html = "<html><head><title> My Shop Page </title></head><body></body></html>"

    assert browser._page_title(html) == "My Shop Page"


def test_page_title_is_none_without_a_title_tag():
    from channels.facebook_direct import browser

    assert browser._page_title("<html><body>hi</body></html>") is None


def test_looks_like_login_page_from_the_redirected_url():
    from channels.facebook_direct import browser

    assert browser._looks_like_login_page("<html></html>", "https://mbasic.facebook.com/login/")


def test_looks_like_login_page_from_a_login_form_marker():
    from channels.facebook_direct import browser

    html = '<form id="login_form"><input name="email"></form>'

    assert browser._looks_like_login_page(html, "https://mbasic.facebook.com/999")


def test_does_not_look_like_login_page_for_an_ordinary_page():
    from channels.facebook_direct import browser

    html = "<html><head><title>My Shop Page</title></head></html>"

    assert not browser._looks_like_login_page(html, "https://mbasic.facebook.com/999")


# ------------------------------------------------------------------ wiring


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import backend.services.comment_service  # noqa: F401
    import channels.facebook_direct.poller  # noqa: F401

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
        "backend.services.comment_service",
        "channels.facebook_direct.poller",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager
    import database.manager as manager_module

    from backend.api.routes import auth, channels, facebook_direct

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, channels, facebook_direct):
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


def _connect_payload(**overrides):
    payload = {
        "name": "My Shop Page",
        "page_id": "999888777",
        "cookies_json": VALID_COOKIES_JSON,
    }
    payload.update(overrides)
    return payload


def _mock_validate(monkeypatch, *, page_name=None, error=None):
    from backend.api.routes import facebook_direct as router_module

    def fake(cookies, page_id):
        if error:
            raise error
        return page_name or "My Shop Page"

    monkeypatch.setattr(router_module, "validate_session_and_fetch_page_name", fake)


# ------------------------------------------------------------------ connect route


def test_connect_creates_the_account_with_a_valid_session(app_client, owner, monkeypatch):
    _mock_validate(monkeypatch, page_name="My Shop Page")

    response = app_client.post(
        "/api/facebook-direct/connect", json=_connect_payload(), headers=owner
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "connected"
    account = body["account"]
    assert account["channel"] == "facebook_direct"
    assert account["external_account_id"] == "999888777"
    assert account["has_access_token"] is True
    assert account["config"]["page_name"] == "My Shop Page"


def test_connect_never_leaks_the_cookies_into_the_response(app_client, owner, monkeypatch):
    _mock_validate(monkeypatch)

    response = app_client.post(
        "/api/facebook-direct/connect", json=_connect_payload(), headers=owner
    )

    body_text = json.dumps(response.json())
    assert "some-session-secret" not in body_text
    assert "access_token" not in response.json()["account"]


def test_connect_rejects_cookies_without_c_user(app_client, owner, monkeypatch):
    _mock_validate(monkeypatch)

    bad_cookies = json.dumps([{"name": "xs", "value": "abc"}])

    response = app_client.post(
        "/api/facebook-direct/connect",
        json=_connect_payload(cookies_json=bad_cookies),
        headers=owner,
    )

    assert response.status_code == 400


def test_connect_rejects_malformed_json(app_client, owner, monkeypatch):
    _mock_validate(monkeypatch)

    response = app_client.post(
        "/api/facebook-direct/connect",
        json=_connect_payload(cookies_json="not json at all"),
        headers=owner,
    )

    assert response.status_code == 400


def test_connect_rejects_an_empty_cookie_array(app_client, owner, monkeypatch):
    _mock_validate(monkeypatch)

    response = app_client.post(
        "/api/facebook-direct/connect",
        json=_connect_payload(cookies_json="[]"),
        headers=owner,
    )

    assert response.status_code == 400


def test_connect_surfaces_an_expired_session_as_a_clear_refusal(
    app_client, owner, monkeypatch
):
    from channels.facebook_direct.browser import FacebookSessionError

    _mock_validate(
        monkeypatch, error=FacebookSessionError("Facebook did not accept these cookies.")
    )

    response = app_client.post(
        "/api/facebook-direct/connect", json=_connect_payload(), headers=owner
    )

    assert response.status_code == 400
    assert "cookies" in response.json()["detail"].lower()


def test_connecting_the_same_page_twice_is_refused(app_client, owner, monkeypatch):
    _mock_validate(monkeypatch)

    first = app_client.post(
        "/api/facebook-direct/connect", json=_connect_payload(), headers=owner
    )
    assert first.status_code == 200, first.text

    second = app_client.post(
        "/api/facebook-direct/connect",
        json=_connect_payload(name="Same Page Again"),
        headers=owner,
    )

    assert second.status_code == 409


def test_connect_without_the_elevated_grant_is_refused(
    app_client, platform, alpha, monkeypatch
):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    _mock_validate(monkeypatch)

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

    response = app_client.post(
        "/api/facebook-direct/connect", json=_connect_payload(), headers=headers
    )

    assert response.status_code == 403


# ------------------------------------------------------------------ poller


def _connect_via_service(company, *, page_id="999888777", page_name="My Shop Page"):
    import backend.services.channel_account_service as service_module

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="facebook_direct",
        name="My Shop Page",
        values={
            "external_account_id": page_id,
            "access_token": VALID_COOKIES_JSON,
            "_facebook_page_name": page_name,
        },
    )


def _wire_read_page_comments(monkeypatch, result=None, error=None):
    import channels.facebook_direct.poller as poller_module

    def fake(cookies, page_id, *, max_posts):
        if error:
            raise error
        return result or []

    monkeypatch.setattr(poller_module, "read_page_comments", fake)


def test_poll_account_records_new_comments(wired, alpha, monkeypatch):
    import channels.facebook_direct.poller as poller_module
    from backend.services.comment_service import comment_service

    account = _connect_via_service(alpha)

    _wire_read_page_comments(
        monkeypatch,
        result=[
            {
                "provider_comment_id": "5551112223",
                "post_id": "post1",
                "author_name": "Jane Doe",
                "author_external_id": "100001234567890",
                "message": "Hello, do you deliver to Amman?",
            }
        ],
    )

    poller_module.poll_account(account["id"])

    stored = comment_service.list_comments(company_id=alpha["id"], channel="facebook_direct")
    assert stored["total"] == 1
    row = stored["items"][0]
    assert row["message"] == "Hello, do you deliver to Amman?"
    assert row["author_name"] == "Jane Doe"
    assert row["channel"] == "facebook_direct"
    assert row["permalink"] == "https://www.facebook.com/999888777/posts/post1"


def test_poll_account_does_not_reinsert_a_comment_already_seen(wired, alpha, monkeypatch):
    import channels.facebook_direct.poller as poller_module
    from backend.services.comment_service import comment_service

    account = _connect_via_service(alpha)

    comment = {
        "provider_comment_id": "5551112223",
        "post_id": "post1",
        "author_name": "Jane Doe",
        "author_external_id": "1",
        "message": "Hi",
    }
    _wire_read_page_comments(monkeypatch, result=[comment])

    poller_module.poll_account(account["id"])
    poller_module.poll_account(account["id"])

    stored = comment_service.list_comments(company_id=alpha["id"], channel="facebook_direct")
    assert stored["total"] == 1


def test_poll_account_survives_an_expired_session(wired, alpha, monkeypatch):
    import channels.facebook_direct.poller as poller_module
    from channels.facebook_direct.browser import FacebookSessionError
    from backend.services.comment_service import comment_service

    account = _connect_via_service(alpha)
    _wire_read_page_comments(monkeypatch, error=FacebookSessionError("expired"))

    poller_module.poll_account(account["id"])  # must not raise

    stored = comment_service.list_comments(company_id=alpha["id"], channel="facebook_direct")
    assert stored["total"] == 0


def test_poll_account_does_nothing_without_a_sealed_session(wired, alpha):
    import channels.facebook_direct.poller as poller_module
    import backend.services.channel_account_service as service_module

    account = service_module.channel_account_service.create_account(
        company_id=alpha["id"],
        channel="facebook_direct",
        name="No Session",
        values={"external_account_id": "1", "access_token": VALID_COOKIES_JSON},
    )

    with poller_module.database_manager.control() as conn:
        conn.execute(
            "UPDATE channel_accounts SET access_token_sealed = NULL WHERE id = ?",
            (account["id"],),
        )
        conn.commit()

    poller_module.poll_account(account["id"])  # must not raise


def test_poll_all_accounts_isolates_one_companys_failure_from_another(
    wired, alpha, beta, monkeypatch
):
    import channels.facebook_direct.poller as poller_module

    account_a = _connect_via_service(alpha, page_id="111")
    account_b = _connect_via_service(beta, page_id="222")

    polled = []

    def _fake_poll_account(account_id):
        polled.append(account_id)
        if account_id == account_a["id"]:
            raise RuntimeError("account a exploded")

    monkeypatch.setattr(poller_module, "poll_account", _fake_poll_account)

    poller_module.poll_all_accounts()  # must not raise

    assert set(polled) == {account_a["id"], account_b["id"]}


# ------------------------------------------------------------------ read-only guard


def test_publish_comment_reply_refuses_facebook_direct_without_a_network_call(monkeypatch):
    from channels import comment_sender

    def _forbidden(*args, **kwargs):
        raise AssertionError("must never call the network for a read-only channel")

    monkeypatch.setattr(comment_sender.httpx, "post", _forbidden)

    result = comment_sender.publish_comment_reply(
        company_id=1,
        channel="facebook_direct",
        provider_comment_id="123",
        message="Thanks!",
    )

    assert result["ok"] is False
    assert result["reason"] == "read_only_channel"
