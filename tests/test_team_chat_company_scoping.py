"""Regression tests for the Team Chat API company-scoping and RBAC gate.

The Team Chat API (backend/api/routes/team_chat.py) manages a company's
internal chat rooms and messages. It must enforce:

  1. Multi-tenant isolation: a user in company A can never list rooms,
     read messages, post, or delete a room that belongs to company B.
  2. RBAC: reading requires "team_chat.view"; posting requires
     "team_chat.post"; creating/deleting rooms requires
     "team_chat.manage".
  3. A default "General" room is auto-created per company on first
     visit; the default room cannot be deleted; duplicate room names
     (case-insensitive) are rejected.

Run with: python3 -m pytest tests/test_team_chat_company_scoping.py -v
"""
import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_env():
    """Point the shared db singleton at a throwaway SQLite file per test."""
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    company_settings_service.ensure_schema()

    yield db, auth_service

    db.db_path = original_path

    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def _make_company(db, name, slug):
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO workspaces (name, slug, status) VALUES (?, ?, 'active')",
            (f"{name} workspace", f"{slug}-ws"),
        )
        workspace_id = cursor.lastrowid

        cursor = conn.execute(
            """
            INSERT INTO companies (workspace_id, name, slug, status)
            VALUES (?, ?, ?, 'active')
            """,
            (workspace_id, name, slug),
        )
        company_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, 'Owner', 'owner', 'Full access', 1)
            """,
            (company_id,),
        )
        conn.commit()

    return company_id


def _make_role(db, company_id, code, name, permission_codes):
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, ?, ?, '', 0)
            """,
            (company_id, name, code),
        )
        role_id = cursor.lastrowid
        for permission_code in permission_codes:
            row = conn.execute(
                "SELECT id FROM permissions WHERE code = ?", (permission_code,)
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    (role_id, row["id"]),
                )
        conn.commit()
    return role_id


def _make_user(db, auth_service, company_id, email, role_code="owner"):
    user_id = auth_service.create_user(
        email=email, password="a-strong-password", full_name=email
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code=role_code)
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/team-chat/rooms").status_code == 401
        assert (
            client.post("/api/team-chat/rooms", json={"name": "x"}).status_code == 401
        )
        assert client.get("/api/team-chat/rooms/1/messages").status_code == 401
        assert (
            client.post(
                "/api/team-chat/rooms/1/messages", json={"body": "hi"}
            ).status_code
            == 401
        )
        assert client.delete("/api/team-chat/rooms/1").status_code == 401


def test_view_without_permission_is_403(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "guest", "Guest", [])
    _, token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")

    with TestClient(app) as client:
        assert client.get("/api/team-chat/rooms", headers=_headers(token)).status_code == 403


def test_post_and_manage_permissions_are_separate(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, owner_token = _make_user(db, auth_service, company, "owner@test.local")
    # Role that can view and post but NOT manage rooms.
    _make_role(db, company, "member", "Member", ["team_chat.view", "team_chat.post"])
    _, member_token = _make_user(db, auth_service, company, "member@test.local", role_code="member")
    # Role that can only view.
    _make_role(db, company, "reader", "Reader", ["team_chat.view"])
    _, reader_token = _make_user(db, auth_service, company, "reader@test.local", role_code="reader")

    with TestClient(app) as client:
        rooms = client.get("/api/team-chat/rooms", headers=_headers(owner_token)).json()["items"]
        general = rooms[0]

        # Member can post.
        posted = client.post(
            f"/api/team-chat/rooms/{general['id']}/messages",
            headers=_headers(member_token),
            json={"body": "hello team"},
        )
        assert posted.status_code == 201

        # Member cannot create or delete rooms.
        assert (
            client.post(
                "/api/team-chat/rooms",
                headers=_headers(member_token),
                json={"name": "Members Only"},
            ).status_code
            == 403
        )

        # Reader can read but cannot post.
        listed = client.get(
            f"/api/team-chat/rooms/{general['id']}/messages",
            headers=_headers(reader_token),
        )
        assert listed.status_code == 200
        assert any(m["body"] == "hello team" for m in listed.json()["items"])

        assert (
            client.post(
                f"/api/team-chat/rooms/{general['id']}/messages",
                headers=_headers(reader_token),
                json={"body": "should fail"},
            ).status_code
            == 403
        )


def test_default_room_is_auto_created_and_undeletable(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner2@test.local")

    with TestClient(app) as client:
        rooms = client.get("/api/team-chat/rooms", headers=_headers(token)).json()["items"]
        assert len(rooms) == 1
        assert rooms[0]["name"] == "General"
        assert rooms[0]["is_default"] == 1

        delete = client.delete(
            f"/api/team-chat/rooms/{rooms[0]['id']}", headers=_headers(token)
        )
        assert delete.status_code == 422


def test_duplicate_room_name_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner3@test.local")

    with TestClient(app) as client:
        client.get("/api/team-chat/rooms", headers=_headers(token))

        first = client.post(
            "/api/team-chat/rooms", headers=_headers(token), json={"name": "Sales"}
        )
        assert first.status_code == 201

        duplicate = client.post(
            "/api/team-chat/rooms", headers=_headers(token), json={"name": "sales"}
        )
        assert duplicate.status_code == 422


def test_rooms_and_messages_are_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    with TestClient(app) as client:
        rooms_a = client.get("/api/team-chat/rooms", headers=_headers(token_a)).json()["items"]
        general_a = rooms_a[0]

        posted = client.post(
            f"/api/team-chat/rooms/{general_a['id']}/messages",
            headers=_headers(token_a),
            json={"body": "company A secret"},
        )
        assert posted.status_code == 201

        # Company B gets its own separate General room, not company A's.
        rooms_b = client.get("/api/team-chat/rooms", headers=_headers(token_b)).json()["items"]
        assert all(room["id"] != general_a["id"] for room in rooms_b)

        # Company B cannot read, post into, or delete company A's room.
        assert (
            client.get(
                f"/api/team-chat/rooms/{general_a['id']}/messages",
                headers=_headers(token_b),
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/team-chat/rooms/{general_a['id']}/messages",
                headers=_headers(token_b),
                json={"body": "intruder"},
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/team-chat/rooms/{general_a['id']}", headers=_headers(token_b)
            ).status_code
            == 404
        )


def test_message_pagination_cursors(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner4@test.local")

    with TestClient(app) as client:
        rooms = client.get("/api/team-chat/rooms", headers=_headers(token)).json()["items"]
        room_id = rooms[0]["id"]

        ids = []
        for index in range(5):
            posted = client.post(
                f"/api/team-chat/rooms/{room_id}/messages",
                headers=_headers(token),
                json={"body": f"message {index}"},
            )
            ids.append(posted.json()["id"])

        # after_id returns only newer messages, oldest-first.
        after = client.get(
            f"/api/team-chat/rooms/{room_id}/messages",
            headers=_headers(token),
            params={"after_id": ids[2]},
        ).json()["items"]
        assert [m["id"] for m in after] == ids[3:]

        # before_id returns only older messages, oldest-first.
        before = client.get(
            f"/api/team-chat/rooms/{room_id}/messages",
            headers=_headers(token),
            params={"before_id": ids[2], "limit": 2},
        ).json()["items"]
        assert [m["id"] for m in before] == ids[0:2]


def test_empty_message_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner5@test.local")

    with TestClient(app) as client:
        rooms = client.get("/api/team-chat/rooms", headers=_headers(token)).json()["items"]
        room_id = rooms[0]["id"]

        whitespace_only = client.post(
            f"/api/team-chat/rooms/{room_id}/messages",
            headers=_headers(token),
            json={"body": "   "},
        )
        assert whitespace_only.status_code == 422
