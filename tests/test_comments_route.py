"""Tests for the post-comment queue.

Replaces the deleted page this router's own module docstring describes --
one that rendered two invented comments from a hardcoded array. Nothing over
HTTP had exercised the real replacement: the permission split between
`comments.view` and `comments.reply`, that a failed publish still keeps the
employee's reply and leaves the comment open (see `reply_to_comment`'s own
comment on why the activity entry is recorded only after the publish check),
and that a company only ever sees its own comments.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.comments  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.comment_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.auth_service",
        "backend.services.comment_service",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, comments

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(comments.router)

    return TestClient(app)


def _employ(platform, company, user_id: int, role_code: str) -> int:
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = ?",
            (company["id"], role_code),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    return user_id


def _login(client, company, email, password=PASSWORD):
    response = client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _employee(client, service, platform, company, email, role_code="agent"):
    user_id = service.create_user(email=email, password=PASSWORD, full_name="Employee")
    _employ(platform, company, user_id, role_code)
    return user_id, _login(client, company, email)


def _make_comment(platform, company, **overrides) -> int:
    from backend.services.comment_service import comment_service

    values = {
        "channel": "messenger",
        "provider_comment_id": "c-1",
        "message": "Is this back in stock?",
        "post_id": "p-1",
        "author_name": "A Customer",
    }
    values.update(overrides)

    result = comment_service.record_incoming(company_id=company["id"], **values)
    return int(result["id"])


# ----------------------------------------------------------------- the gate


def test_a_view_only_role_can_read_but_not_reply(client, service, platform, alpha):
    comment_id = _make_comment(platform, alpha)
    _, headers = _employee(
        client, service, platform, alpha, "reader@alpha.example.com", "viewer"
    )

    listed = client.get("/api/comments", headers=headers)
    assert listed.status_code == 200, listed.text

    replied = client.post(
        f"/api/comments/{comment_id}/reply",
        headers=headers,
        json={"message": "Yes, back in stock!"},
    )
    assert replied.status_code == 403, replied.text


def test_an_unauthenticated_request_is_refused(client):
    response = client.get("/api/comments")

    assert response.status_code in (401, 403)


# ---------------------------------------------------------------- listing


def test_a_comment_is_listed_and_can_be_fetched(client, service, platform, alpha):
    comment_id = _make_comment(platform, alpha, message="Do you ship internationally?")
    _, headers = _employee(client, service, platform, alpha, "agent1@alpha.example.com")

    listed = client.get("/api/comments", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == comment_id for item in listed.json()["items"])

    fetched = client.get(f"/api/comments/{comment_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["message"] == "Do you ship internationally?"


def test_an_unknown_comment_is_a_404(client, service, platform, alpha):
    _, headers = _employee(client, service, platform, alpha, "agent2@alpha.example.com")

    response = client.get("/api/comments/999999", headers=headers)

    assert response.status_code == 404


def test_a_comment_never_crosses_the_company_boundary(
    client, service, platform, alpha, beta
):
    comment_id = _make_comment(platform, alpha)
    _, beta_headers = _employee(
        client, service, platform, beta, "peeker@beta.example.com"
    )

    response = client.get(f"/api/comments/{comment_id}", headers=beta_headers)

    assert response.status_code == 404


# ----------------------------------------------------------------- replying


def test_a_successful_reply_is_published_and_recorded(
    client, service, platform, alpha, monkeypatch
):
    import backend.api.routes.comments as comments_module

    monkeypatch.setattr(
        comments_module,
        "publish_comment_reply",
        lambda **kwargs: {"ok": True, "provider_reply_id": "r-1"},
    )

    comment_id = _make_comment(platform, alpha)
    _, headers = _employee(client, service, platform, alpha, "agent3@alpha.example.com")

    response = client.post(
        f"/api/comments/{comment_id}/reply",
        headers=headers,
        json={"message": "Thanks for asking -- yes!"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"

    fetched = client.get(f"/api/comments/{comment_id}", headers=headers).json()
    assert fetched["replies"][0]["body"] == "Thanks for asking -- yes!"
    assert fetched["replies"][0]["send_status"] == "sent"


def test_a_failed_publish_keeps_the_reply_and_leaves_the_comment_open(
    client, service, platform, alpha, monkeypatch
):
    import backend.api.routes.comments as comments_module

    monkeypatch.setattr(
        comments_module,
        "publish_comment_reply",
        lambda **kwargs: {"ok": False, "error": "Comment no longer exists"},
    )

    comment_id = _make_comment(platform, alpha)
    _, headers = _employee(client, service, platform, alpha, "agent4@alpha.example.com")

    response = client.post(
        f"/api/comments/{comment_id}/reply",
        headers=headers,
        json={"message": "This will not go out"},
    )

    assert response.status_code == 502

    fetched = client.get(f"/api/comments/{comment_id}", headers=headers).json()
    assert fetched["status"] == "open"
    assert fetched["replies"][0]["body"] == "This will not go out"
    assert fetched["replies"][0]["send_status"] == "failed"


def test_the_status_can_be_updated(client, service, platform, alpha):
    comment_id = _make_comment(platform, alpha)
    _, headers = _employee(client, service, platform, alpha, "agent5@alpha.example.com")

    response = client.patch(
        f"/api/comments/{comment_id}/status",
        headers=headers,
        json={"status": "ignored"},
    )

    assert response.status_code == 200, response.text

    fetched = client.get(f"/api/comments/{comment_id}", headers=headers).json()
    assert fetched["status"] == "ignored"
