import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.scheduled_post_service import scheduled_post_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    scheduled_post_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
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
            """
            INSERT INTO channel_accounts (id, company_id, channel, name, page_id, access_token_encrypted, status)
            VALUES (1, 1, 'messenger', 'T-Zone Page', 'page123', NULL, 'active')
            """
        )
        conn.execute(
            """
            INSERT INTO channel_accounts (id, company_id, channel, name, instagram_business_id, access_token_encrypted, status)
            VALUES (2, 1, 'instagram', 'tzone.lb', 'ig123', NULL, 'active')
            """
        )
        conn.execute(
            """
            INSERT INTO channel_accounts (id, company_id, channel, name, phone_number_id, access_token_encrypted, status)
            VALUES (3, 1, 'whatsapp', 'T-Zone WA', 'wa123', NULL, 'active')
            """
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


def test_create_draft_post(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={"text": "hello world", "channel_account_ids": [1]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["channel_account_ids"] == [1]


def test_create_rejects_empty_content(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={"channel_account_ids": [1]})
    assert resp.status_code == 400


def test_create_rejects_no_channels(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={"text": "hi", "channel_account_ids": []})
    assert resp.status_code == 400


def test_create_rejects_messaging_channel(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={"text": "hi", "channel_account_ids": [3]})
    assert resp.status_code == 400


def test_create_rejects_unknown_channel_account(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={"text": "hi", "channel_account_ids": [999]})
    assert resp.status_code == 404


def test_scheduled_without_datetime_rejected(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={"text": "hi", "channel_account_ids": [1], "status": "scheduled"})
    assert resp.status_code == 400


def test_publish_now_calls_messenger_sender(client_and_db):
    client = client_and_db
    with patch(
        "backend.services.scheduled_post_service.channel_account_service.get_decrypted_token",
        return_value="fake-token",
    ), patch("backend.services.scheduled_post_service.requests.post") as mock_post:
        mock_post.return_value.ok = True
        mock_post.return_value.content = b'{"id": "post_123"}'
        mock_post.return_value.json.return_value = {"id": "post_123"}

        created = client.post("/api/scheduled-posts", json={"text": "hi", "channel_account_ids": [1]}).json()
        resp = client.post(f"/api/scheduled-posts/{created['id']}/publish-now")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "sent"
    assert body["results"]["1"]["ok"] is True
    mock_post.assert_called_once()
    assert "/page123/feed" in mock_post.call_args.args[0]


def test_publish_instagram_requires_media(client_and_db):
    client = client_and_db
    with patch(
        "backend.services.scheduled_post_service.channel_account_service.get_decrypted_token",
        return_value="fake-token",
    ):
        created = client.post("/api/scheduled-posts", json={"text": "no media", "channel_account_ids": [2]}).json()
        resp = client.post(f"/api/scheduled-posts/{created['id']}/publish-now")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["results"]["2"]["ok"] is False


def test_list_filters_by_status(client_and_db):
    client = client_and_db
    client.post("/api/scheduled-posts", json={"text": "a", "channel_account_ids": [1], "status": "draft"})
    client.post("/api/scheduled-posts", json={
        "text": "b", "channel_account_ids": [1], "status": "scheduled", "scheduled_at": "2099-01-01T00:00:00Z",
    })

    resp = client.get("/api/scheduled-posts", params={"status": "draft"})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["text"] == "a"


def test_update_draft_post(client_and_db):
    client = client_and_db
    created = client.post("/api/scheduled-posts", json={"text": "a", "channel_account_ids": [1]}).json()
    resp = client.put(f"/api/scheduled-posts/{created['id']}", json={"text": "updated"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "updated"


def test_delete_post(client_and_db):
    client = client_and_db
    created = client.post("/api/scheduled-posts", json={"text": "a", "channel_account_ids": [1]}).json()
    resp = client.delete(f"/api/scheduled-posts/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/scheduled-posts").json()["items"] == []


def test_options_returns_postable_channels_only(client_and_db):
    client = client_and_db
    resp = client.get("/api/scheduled-posts/options")
    assert resp.status_code == 200
    channels = {account["channel"] for account in resp.json()["channel_accounts"]}
    assert channels == {"messenger", "instagram"}


def test_posts_isolated_per_company(client_and_db):
    from backend.services.scheduled_post_service import scheduled_post_service
    with pytest.raises(KeyError):
        scheduled_post_service.create_post(company_id=2, text="other company", channel_account_ids=[1])


def test_create_stores_content_overrides_and_post_types(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={
        "text": "shared text",
        "channel_account_ids": [1, 2],
        "content_overrides": {"2": "instagram-only caption"},
        "channel_post_types": {"2": "reels"},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content_overrides"] == {"2": "instagram-only caption"}
    assert body["channel_post_types"] == {"2": "reels"}


def test_create_rejects_invalid_post_type(client_and_db):
    client = client_and_db
    resp = client.post("/api/scheduled-posts", json={
        "text": "hi", "channel_account_ids": [1], "channel_post_types": {"1": "not-a-real-type"},
    })
    assert resp.status_code == 400


def test_publish_uses_per_channel_override_and_reel_type(client_and_db):
    client = client_and_db
    with patch(
        "backend.services.scheduled_post_service.channel_account_service.get_decrypted_token",
        return_value="fake-token",
    ), patch("backend.services.scheduled_post_service.requests.post") as mock_post:
        mock_post.return_value.ok = True
        mock_post.return_value.content = b'{"id": "post_123"}'
        mock_post.return_value.json.return_value = {"id": "post_123"}

        created = client.post("/api/scheduled-posts", json={
            "text": "shared text",
            "channel_account_ids": [1],
            "media_urls": ["https://cdn.test/video.mp4"],
            "media_type": "video",
            "content_overrides": {"1": "override just for facebook"},
            "channel_post_types": {"1": "reels"},
        }).json()
        client.post(f"/api/scheduled-posts/{created['id']}/publish-now")

    mock_post.assert_called_once()
    assert "/page123/video_reels" in mock_post.call_args.args[0]
    assert mock_post.call_args.kwargs["data"]["description"] == "override just for facebook"
