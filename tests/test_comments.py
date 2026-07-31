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
    from backend.services.comment_service import comment_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    comment_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')")
        # A connected Facebook Page and an Instagram account.
        conn.execute(
            "INSERT INTO channel_accounts (id, company_id, channel, name, page_id, access_token_encrypted, status) "
            "VALUES (10, 1, 'messenger', 'T-Zone Page', 'PAGE_100', NULL, 'active')"
        )
        conn.execute(
            "INSERT INTO channel_accounts (id, company_id, channel, name, instagram_business_id, access_token_encrypted, status) "
            "VALUES (11, 1, 'instagram', 'tzone.lb', 'IG_200', NULL, 'active')"
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


def _fb_comment_payload(comment_id="cmt_1", post_id="POST_1", message="Nice!"):
    return {
        "entry": [{
            "id": "PAGE_100",
            "changes": [{
                "field": "feed",
                "value": {
                    "item": "comment",
                    "verb": "add",
                    "comment_id": comment_id,
                    "post_id": post_id,
                    "message": message,
                    "from": {"id": "user_9", "name": "Ali"},
                    "created_time": 1750000000,
                },
            }],
        }],
    }


def test_ingest_facebook_comment(client_and_db):
    from backend.services.comment_service import comment_service
    result = comment_service.ingest_webhook(_fb_comment_payload())
    assert result["stored"] == 1

    posts = comment_service.list_posts(company_id=COMPANY_ID)
    assert len(posts) == 1
    assert posts[0]["post_external_id"] == "POST_1"
    assert posts[0]["comment_count"] == 1
    assert posts[0]["unanswered_count"] == 1


def test_ingest_instagram_comment(client_and_db):
    from backend.services.comment_service import comment_service
    payload = {
        "entry": [{
            "id": "IG_200",
            "changes": [{
                "field": "comments",
                "value": {
                    "id": "ig_cmt_1",
                    "media": {"id": "IG_POST_1"},
                    "text": "🔥",
                    "from": {"id": "u1", "username": "fan"},
                },
            }],
        }],
    }
    result = comment_service.ingest_webhook(payload)
    assert result["stored"] == 1
    posts = comment_service.list_posts(company_id=COMPANY_ID)
    assert posts[0]["channel"] == "instagram"


def test_unmatched_account_is_skipped(client_and_db):
    from backend.services.comment_service import comment_service
    payload = _fb_comment_payload()
    payload["entry"][0]["id"] = "PAGE_UNKNOWN"
    result = comment_service.ingest_webhook(payload)
    assert result["stored"] == 0 and result["skipped"] == 1


def test_non_comment_change_skipped(client_and_db):
    from backend.services.comment_service import comment_service
    payload = {"entry": [{"id": "PAGE_100", "changes": [{"field": "feed", "value": {"item": "like", "verb": "add"}}]}]}
    result = comment_service.ingest_webhook(payload)
    assert result["stored"] == 0


def test_list_posts_endpoint(client_and_db):
    from backend.services.comment_service import comment_service
    comment_service.ingest_webhook(_fb_comment_payload())
    resp = client_and_db.get("/api/comments/posts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unanswered_total"] == 1
    assert body["posts"][0]["channel_account_name"] == "T-Zone Page"


def test_list_comments_endpoint(client_and_db):
    from backend.services.comment_service import comment_service
    comment_service.ingest_webhook(_fb_comment_payload(message="Hello there"))
    resp = client_and_db.get("/api/comments/posts/POST_1/comments")
    assert resp.status_code == 200
    comments = resp.json()["comments"]
    assert comments[0]["text"] == "Hello there"
    assert comments[0]["author_name"] == "Ali"


def test_reply_posts_to_graph_and_marks_answered(client_and_db):
    from backend.services.comment_service import comment_service
    comment_service.ingest_webhook(_fb_comment_payload())
    comment_id = comment_service.list_comments(company_id=COMPANY_ID, post_external_id="POST_1")[0]["id"]

    with patch(
        "backend.services.comment_service.channel_account_service.get_decrypted_token",
        return_value="fake-token",
    ), patch("backend.services.comment_service.requests.post") as mock_post:
        mock_post.return_value.ok = True
        mock_post.return_value.content = b'{"id": "reply_1"}'
        mock_post.return_value.json.return_value = {"id": "reply_1"}

        resp = client_and_db.post(f"/api/comments/{comment_id}/reply", json={"text": "Thank you!"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "answered"
    mock_post.assert_called_once()
    assert "/cmt_1/comments" in mock_post.call_args.args[0]

    # Parent now answered; our reply stored as a business comment.
    comments = comment_service.list_comments(company_id=COMPANY_ID, post_external_id="POST_1")
    parent = next(c for c in comments if c["comment_external_id"] == "cmt_1")
    assert parent["status"] == "answered"
    assert any(c["is_from_business"] == 1 and c["text"] == "Thank you!" for c in comments)


def test_reply_instagram_uses_replies_endpoint(client_and_db):
    from backend.services.comment_service import comment_service
    comment_service.ingest_webhook({
        "entry": [{"id": "IG_200", "changes": [{"field": "comments", "value": {
            "id": "ig_cmt_9", "media": {"id": "IG_POST_9"}, "text": "hi", "from": {"id": "x", "username": "y"}}}]}],
    })
    cid = comment_service.list_comments(company_id=COMPANY_ID, post_external_id="IG_POST_9")[0]["id"]
    with patch(
        "backend.services.comment_service.channel_account_service.get_decrypted_token",
        return_value="fake-token",
    ), patch("backend.services.comment_service.requests.post") as mock_post:
        mock_post.return_value.ok = True
        mock_post.return_value.content = b'{"id": "r2"}'
        mock_post.return_value.json.return_value = {"id": "r2"}
        client_and_db.post(f"/api/comments/{cid}/reply", json={"text": "shukran"})
    assert "/ig_cmt_9/replies" in mock_post.call_args.args[0]


def test_comments_isolated_per_company(client_and_db):
    from database.database import db
    from backend.services.comment_service import comment_service
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other', 'other', 1)")
        conn.commit()
    comment_service.ingest_webhook(_fb_comment_payload())
    # Company 2 sees nothing.
    assert comment_service.list_posts(company_id=2) == []
