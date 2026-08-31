"""Tests for keeping the session token out of JavaScript's reach.

The token lived in `localStorage`, which any script on the page can read: one
XSS hole anywhere — in a dependency, in a rendered customer name, in an error
message — handed an attacker a working session that outlived the tab they stole
it from.

An `httpOnly` cookie cannot be read by script at all. That is the change, and
the tests below are mostly about what it costs: a cookie is attached by the
browser automatically, so a form on another site can make the browser send it.
The double-submit token is what closes that, and the interesting cases are the
ones where it must *not* apply — a bearer token cannot be forged cross-site, and
demanding a CSRF header from an API client would break every integration for no
gain.
"""

from __future__ import annotations

import pytest


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.dashboard  # noqa: F401
    import backend.services.auth_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.auth_service" in rebound

    return test_manager


@pytest.fixture()
def client(wired):
    """The routers behind the same middleware the real app uses.

    Mounting the routers without it would test a different application: the
    cookie bridge and the CSRF check are middleware, so a test client that
    skipped them would prove nothing about either.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.middleware import SessionCookieMiddleware
    from backend.api.routes import auth, dashboard, knowledge

    app = FastAPI()
    app.add_middleware(SessionCookieMiddleware)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(knowledge.router)

    return TestClient(app)


@pytest.fixture()
def employee(wired, platform, alpha, client):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="agent@alpha.example.com", password=PASSWORD, full_name="An Agent"
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

    return user_id


def _sign_in(client, alpha):
    return client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "agent@alpha.example.com",
            "password": PASSWORD,
        },
    )


# ------------------------------------------------------------------ the cookie


def test_signing_in_sets_an_http_only_session_cookie(client, employee, alpha):
    response = _sign_in(client, alpha)

    assert response.status_code == 200, response.text

    header = "; ".join(response.headers.get_list("set-cookie"))

    assert "tzone_session=" in header
    assert "HttpOnly" in header
    assert "SameSite=strict" in header.replace("Strict", "strict")


def test_the_csrf_cookie_is_readable_by_script(client, employee, alpha):
    """It is not a credential. It proves the request came from a page that
    could read this origin's cookies, which is what a cross-origin attacker
    cannot do."""
    response = _sign_in(client, alpha)

    csrf_cookie = next(
        item
        for item in response.headers.get_list("set-cookie")
        if item.startswith("tzone_csrf=")
    )

    assert "HttpOnly" not in csrf_cookie


def test_the_token_is_still_in_the_body(client, employee, alpha):
    """Removing it would break the CLI, the tests, and any integration a
    customer has built. The cookie is an additional path, not a replacement."""
    body = _sign_in(client, alpha).json()

    assert body["access_token"]
    assert body["csrf_token"]


def test_the_cookie_alone_authenticates_a_read(client, employee, alpha):
    _sign_in(client, alpha)

    # No Authorization header. The test client keeps the cookie jar.
    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200, response.text


def test_signing_out_removes_the_cookies(client, employee, alpha):
    """Either half alone leaves a half-signed-out state: a live cookie for a
    dead session 401s every request with no explanation, and a revoked token
    still in the jar is a credential nobody meant to keep."""
    csrf = _sign_in(client, alpha).json()["csrf_token"]

    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text

    assert client.get("/api/dashboard/summary").status_code == 401


# -------------------------------------------------------------------- the CSRF


def test_a_cookie_write_without_the_token_is_refused(client, employee, alpha):
    """The whole reason the double-submit token exists: the browser attaches
    the session cookie by itself, so a form on another site could otherwise
    make it write."""
    _sign_in(client, alpha)

    response = client.post(
        "/api/knowledge",
        json={"title": "Forged", "content_en": "text"},
    )

    assert response.status_code == 403
    assert "csrf_token_invalid" in response.text


def test_a_cookie_write_with_the_token_is_allowed(client, employee, alpha):
    csrf = _sign_in(client, alpha).json()["csrf_token"]

    response = client.post(
        "/api/knowledge",
        headers={"X-CSRF-Token": csrf},
        json={"title": "Opening hours", "content_en": "Nine to six"},
    )

    assert response.status_code == 201, response.text


def test_a_wrong_token_is_refused(client, employee, alpha):
    _sign_in(client, alpha)

    response = client.post(
        "/api/knowledge",
        headers={"X-CSRF-Token": "not-the-token"},
        json={"title": "Forged", "content_en": "text"},
    )

    assert response.status_code == 403


def test_a_cookie_read_needs_no_token(client, employee, alpha):
    """A forged GET achieves nothing, and requiring a header on reads would
    make every page load fail before the token could be fetched."""
    _sign_in(client, alpha)

    assert client.get("/api/knowledge").status_code == 200


def test_a_bearer_write_needs_no_csrf_token(client, employee, alpha):
    """Nothing attaches an `Authorization` header automatically, so such a
    request cannot be forged from another origin. Demanding a CSRF header here
    would break every CLI and integration for no gain."""
    token = _sign_in(client, alpha).json()["access_token"]

    # A fresh jar, so only the header authenticates this.
    client.cookies.clear()

    response = client.post(
        "/api/knowledge",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "From an integration", "content_en": "text"},
    )

    assert response.status_code == 201, response.text


def test_the_header_wins_over_a_stale_cookie(client, employee, alpha, platform):
    """A client that sends a token is naming the session it means. A leftover
    cookie in the same browser must not override it."""
    token = _sign_in(client, alpha).json()["access_token"]

    # Replace the cookie with a dead token; the header should still work.
    client.cookies.set("tzone_session", "a-revoked-token")

    response = client.get(
        "/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200, response.text


def test_an_unauthenticated_write_is_not_turned_into_a_csrf_failure(client):
    """A caller with no session at all should be told to sign in, not told
    their CSRF token is wrong — the second sends them to reload a page that
    was never going to work."""
    response = client.post(
        "/api/knowledge", json={"title": "x", "content_en": "y"}
    )

    assert response.status_code in (401, 403)
    assert "csrf_token_invalid" not in response.text


# ----------------------------------------------------------------- the details


def test_the_cookie_is_marked_secure_behind_a_proxy(client, employee, alpha):
    """In production the application speaks plain http to nginx, so reading the
    scheme off the request would never mark a cookie `Secure`."""
    response = client.post(
        "/api/auth/login",
        headers={"X-Forwarded-Proto": "https"},
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "agent@alpha.example.com",
            "password": PASSWORD,
        },
    )

    header = "; ".join(response.headers.get_list("set-cookie"))

    assert "Secure" in header


def test_the_cookie_is_not_marked_secure_over_plain_http(client, employee, alpha):
    """A `Secure` cookie is dropped by the browser on http, so marking one in
    development would sign the developer out on every request."""
    header = "; ".join(_sign_in(client, alpha).headers.get_list("set-cookie"))

    assert "Secure" not in header


def test_each_sign_in_issues_a_fresh_csrf_token(client, employee, alpha):
    first = _sign_in(client, alpha).json()["csrf_token"]
    second = _sign_in(client, alpha).json()["csrf_token"]

    assert first != second


def test_the_webhook_paths_are_exempt(client):
    """A provider's callback carries no cookie and is authenticated by an HMAC
    over the raw body, which is a stronger proof than this one."""
    from backend.api.middleware import SessionCookieMiddleware

    assert "/webhook" in SessionCookieMiddleware.EXEMPT_PREFIXES


def test_signing_in_again_with_a_stale_cookie_is_not_a_csrf_failure(
    client, employee, alpha
):
    """The defect this file caught while being written.

    With a session cookie still in the jar, the sign-in endpoint was a
    cookie-authenticated write and the CSRF check refused it. That is not an
    edge case: it is a user whose session expired, or who wants to sign in as
    somebody else, being told to reload a page that will fail the same way.

    Requiring the *old* session's token in order to replace it is circular. It
    is safe to exempt because nothing on this path reads the existing session —
    whatever cookie arrives is overwritten by the response — and `SameSite=Strict`
    is what answers login CSRF.
    """
    _sign_in(client, alpha)

    second = _sign_in(client, alpha)

    assert second.status_code == 200, second.text
    assert second.json()["csrf_token"]


def test_a_forced_password_reset_is_not_a_csrf_failure(client):
    """Same reasoning: the token in the path is the credential, and somebody
    with a stale cookie must still be able to use the link they were sent."""
    from backend.api.middleware import SessionCookieMiddleware

    assert "/api/auth/password/reset" in (
        SessionCookieMiddleware.SESSION_ESTABLISHING_PREFIXES
    )
