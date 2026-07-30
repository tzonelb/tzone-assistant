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

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    team_chat_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent One', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'agent2@test.local', 'Agent Two', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 2, 'active')")
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
    assert names == {"Agent One", "Agent Two"}
