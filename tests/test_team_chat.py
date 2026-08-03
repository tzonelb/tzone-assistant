import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.team_chat_service import team_chat_service
    from backend.services.team_chat_rooms_service import team_chat_rooms_service
    from backend.services.department_service import department_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    team_chat_service.ensure_schema()
    team_chat_rooms_service.ensure_schema()
    department_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent One', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'agent2@test.local', 'Agent Two', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (3, 'agent3@test.local', 'Agent Three', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute("SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'").fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 1, ?, 'active')",
            (owner_role_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 2, ?, 'active')",
            (owner_role_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status, departments_json) "
            "VALUES (1, 3, ?, 'active', '[\"Sales\"]')",
            (owner_role_id,),
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app)

    app.dependency_overrides.clear()
    db.db_path = original_db_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_send_and_list_message(client_and_db):
    client = client_and_db
    resp = client.post("/api/team-chat", json={"text": "hello team"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == "hello team"
    assert body["sender_name"] == "Agent One"
    assert body["mentioned_user_ids"] == []

    listed = client.get("/api/team-chat").json()
    assert listed["total"] == 1
    assert listed["items"][0]["text"] == "hello team"


def test_send_rejects_empty_text(client_and_db):
    client = client_and_db
    resp = client.post("/api/team-chat", json={"text": "   "})
    assert resp.status_code == 400


def test_mentions_filtered_to_active_company_employees(client_and_db):
    client = client_and_db
    resp = client.post("/api/team-chat", json={"text": "hey @Agent Two", "mentioned_user_ids": [2, 9999]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mentioned_user_ids"] == [2]


def test_list_ordering_oldest_first(client_and_db):
    client = client_and_db
    client.post("/api/team-chat", json={"text": "first"})
    client.post("/api/team-chat", json={"text": "second"})
    items = client.get("/api/team-chat").json()["items"]
    assert [item["text"] for item in items] == ["first", "second"]


def test_delete_own_message(client_and_db):
    client = client_and_db
    created = client.post("/api/team-chat", json={"text": "delete me"}).json()
    resp = client.delete(f"/api/team-chat/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/team-chat").json()["items"] == []


def test_delete_unknown_message_404s(client_and_db):
    client = client_and_db
    resp = client.delete("/api/team-chat/9999")
    assert resp.status_code == 404


def test_cannot_delete_others_message(client_and_db):
    from backend.services.team_chat_service import team_chat_service
    other_message = team_chat_service.send_message(company_id=COMPANY_ID, sender_user_id=2, text="not yours")

    client = client_and_db
    resp = client.delete(f"/api/team-chat/{other_message['id']}")
    assert resp.status_code == 403


def test_messages_isolated_per_company(client_and_db):
    from backend.services.team_chat_service import team_chat_service
    team_chat_service.send_message(company_id=2, sender_user_id=1, text="other company message")

    client = client_and_db
    items = client.get("/api/team-chat").json()["items"]
    assert all(item["text"] != "other company message" for item in items)


def test_options_returns_company_employees(client_and_db):
    client = client_and_db
    resp = client.get("/api/team-chat/options")
    assert resp.status_code == 200
    names = {employee["full_name"] for employee in resp.json()["employees"]}
    assert names == {"Agent One", "Agent Two", "Agent Three"}


# -- DMs and groups (additive rooms feature) --------------------------------

def test_create_dm_and_send_message(client_and_db):
    client = client_and_db
    room = client.post("/api/team-chat/rooms/dm", json={"user_id": 2}).json()
    assert room["kind"] == "dm"
    assert room["display_name"] == "Agent Two"

    resp = client.post(f"/api/team-chat/rooms/{room['id']}/messages", json={"text": "hi there"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "hi there"

    listed = client.get(f"/api/team-chat/rooms/{room['id']}/messages").json()
    assert listed["items"][0]["text"] == "hi there"
    assert listed["items"][0]["sender_name"] == "Agent One"


def test_get_or_create_dm_is_idempotent(client_and_db):
    client = client_and_db
    first = client.post("/api/team-chat/rooms/dm", json={"user_id": 2}).json()
    second = client.post("/api/team-chat/rooms/dm", json={"user_id": 2}).json()
    assert first["id"] == second["id"]


def test_dm_with_self_is_rejected(client_and_db):
    client = client_and_db
    resp = client.post("/api/team-chat/rooms/dm", json={"user_id": 1})
    assert resp.status_code == 400


def test_create_group_with_explicit_members(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/team-chat/rooms/group",
        json={"name": "Launch Squad", "member_user_ids": [2, 3]},
    )
    assert resp.status_code == 200, resp.text
    room = resp.json()
    assert room["kind"] == "group"
    assert room["display_name"] == "Launch Squad"
    member_names = {m["display_name"] for m in room["members"]}
    assert member_names == {"Agent One", "Agent Two", "Agent Three"}


def test_create_group_by_department(client_and_db):
    """Agent Three is the only one scoped to Sales — the group should
    snapshot them in alongside the creator, without needing member_user_ids.
    Agent Two (no department at all) must NOT be swept in — group membership
    is strict, unlike department-picker filters elsewhere in the codebase."""
    client = client_and_db
    resp = client.post(
        "/api/team-chat/rooms/group",
        json={"name": "Sales Team", "department": "Sales"},
    )
    assert resp.status_code == 200, resp.text
    member_names = {m["display_name"] for m in resp.json()["members"]}
    assert member_names == {"Agent One", "Agent Three"}


def test_create_group_rejects_too_few_members(client_and_db):
    client = client_and_db
    resp = client.post("/api/team-chat/rooms/group", json={"name": "Solo"})
    assert resp.status_code == 400


def test_list_rooms_only_shows_rooms_you_belong_to(client_and_db):
    client = client_and_db
    client.post("/api/team-chat/rooms/dm", json={"user_id": 2})

    from backend.services.team_chat_rooms_service import team_chat_rooms_service
    team_chat_rooms_service.create_group(
        company_id=COMPANY_ID, created_by_user_id=2, name="Without Agent One", member_user_ids=[3],
    )

    rooms = client.get("/api/team-chat/rooms").json()["rooms"]
    names = {room["display_name"] for room in rooms}
    assert names == {"Agent Two"}


def test_non_member_cannot_read_or_send_room_messages(client_and_db):
    from backend.services.team_chat_rooms_service import team_chat_rooms_service
    room = team_chat_rooms_service.create_group(
        company_id=COMPANY_ID, created_by_user_id=2, name="Without Agent One", member_user_ids=[3],
    )

    client = client_and_db
    assert client.get(f"/api/team-chat/rooms/{room['id']}/messages").status_code == 403
    assert client.post(f"/api/team-chat/rooms/{room['id']}/messages", json={"text": "sneaky"}).status_code == 403


def test_delete_own_room_message(client_and_db):
    client = client_and_db
    room = client.post("/api/team-chat/rooms/dm", json={"user_id": 2}).json()
    message = client.post(f"/api/team-chat/rooms/{room['id']}/messages", json={"text": "bye"}).json()

    resp = client.delete(f"/api/team-chat/rooms/{room['id']}/messages/{message['id']}")
    assert resp.status_code == 200
    assert client.get(f"/api/team-chat/rooms/{room['id']}/messages").json()["items"] == []


def test_cannot_delete_others_room_message(client_and_db):
    client = client_and_db
    room = client.post("/api/team-chat/rooms/dm", json={"user_id": 2}).json()

    from backend.services.team_chat_rooms_service import team_chat_rooms_service
    other_message = team_chat_rooms_service.send_room_message(
        company_id=COMPANY_ID, room_id=room["id"], sender_user_id=2, text="not yours",
    )

    resp = client.delete(f"/api/team-chat/rooms/{room['id']}/messages/{other_message['id']}")
    assert resp.status_code == 403


def test_room_message_with_attachment_and_no_text(client_and_db):
    client = client_and_db
    room = client.post("/api/team-chat/rooms/dm", json={"user_id": 2}).json()
    resp = client.post(
        f"/api/team-chat/rooms/{room['id']}/messages",
        json={"text": "", "attachment_url": "https://example.test/a.png", "attachment_type": "image", "attachment_filename": "a.png"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["attachment_url"] == "https://example.test/a.png"
