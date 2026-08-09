"""
Tests for direct Instagram/Facebook sessions feeding the Comments module
(no Meta developer app):

  * sync_all with nothing connected is a clean no-op
  * an Instagram direct sync ingests posts + comments through the same
    social_posts/social_post_comments tables (stubbed instagrapi client)
  * replying to an instagram_direct comment goes through the session,
    not the Graph API
  * facebook_direct is read-only: replying returns a clear error

Run with: python3 -m pytest tests/test_social_direct_sessions.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1


@pytest.fixture()
def fresh_db():
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
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.commit()

    yield

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


def _insert_direct_account(channel: str, external_id: str) -> int:
    from database.database import db
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO channel_accounts (company_id, channel, name, external_account_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', '2026-01-01', '2026-01-01')",
            (COMPANY_ID, channel, f"{channel} test", external_id),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_sync_all_with_no_direct_accounts_is_noop(fresh_db):
    from backend.services.social_session_service import social_session_service
    result = social_session_service.sync_all(company_id=COMPANY_ID)
    assert result == {"accounts": []}


def test_instagram_2fa_gives_actionable_message(fresh_db):
    """A 2FA-protected account with no code returns a clear 'enter your code'
    message, not a raw instagrapi exception string."""
    from backend.services import social_session_service as mod
    from backend.services.social_session_service import social_session_service, SocialSessionError

    class TwoFactorRequired(Exception):
        pass

    class _FakeClient:
        def login(self, username, password, verification_code=""):
            raise TwoFactorRequired("two-factor required")

    with patch.object(mod, "_require_instagrapi", return_value=_FakeClient):
        with pytest.raises(SocialSessionError, match="two-factor"):
            social_session_service.connect_instagram(
                company_id=COMPANY_ID, username="shop", password="pw", verification_code=None,
            )

    # With a (wrong) code supplied, the message says the code was wrong/expired.
    with patch.object(mod, "_require_instagrapi", return_value=_FakeClient):
        with pytest.raises(SocialSessionError, match="wrong or expired"):
            social_session_service.connect_instagram(
                company_id=COMPANY_ID, username="shop", password="pw", verification_code="000000",
            )


def test_reply_to_facebook_direct_comment_is_refused(fresh_db):
    from backend.services.comment_service import comment_service

    account_id = _insert_direct_account("facebook_direct", "fb-cookies-1")
    comment_service._upsert_post(
        company_id=COMPANY_ID, channel_account_id=account_id, channel="facebook_direct",
        post_external_id="post1", caption="hello", media_url=None, permalink=None,
    )
    comment_service._upsert_comment(
        company_id=COMPANY_ID, channel_account_id=account_id, channel="facebook_direct",
        post_external_id="post1", comment_external_id="c1", parent_comment_external_id=None,
        author_name="Visitor", author_external_id="777", text="How much?",
        platform_created_at=None, is_from_business=0,
    )
    from database.database import db
    with db.connect() as conn:
        comment_row_id = conn.execute(
            "SELECT id FROM social_post_comments WHERE comment_external_id = 'c1'"
        ).fetchone()["id"]

    with pytest.raises(ValueError, match="read-only"):
        comment_service.reply_to_comment(
            company_id=COMPANY_ID, comment_id=comment_row_id, text="reply", actor_user_id=None,
        )


def test_reply_to_instagram_direct_comment_uses_session(fresh_db):
    from backend.services.comment_service import comment_service

    account_id = _insert_direct_account("instagram_direct", "12345")
    comment_service._upsert_post(
        company_id=COMPANY_ID, channel_account_id=account_id, channel="instagram_direct",
        post_external_id="m1", caption="post", media_url=None, permalink=None,
    )
    comment_service._upsert_comment(
        company_id=COMPANY_ID, channel_account_id=account_id, channel="instagram_direct",
        post_external_id="m1", comment_external_id="c9", parent_comment_external_id=None,
        author_name="fan", author_external_id="42", text="nice!",
        platform_created_at=None, is_from_business=0,
    )
    from database.database import db
    with db.connect() as conn:
        comment_row_id = conn.execute(
            "SELECT id FROM social_post_comments WHERE comment_external_id = 'c9'"
        ).fetchone()["id"]

    calls = {}

    def _fake_reply(*, account_id, post_external_id, comment_external_id, text):
        calls.update({
            "account_id": account_id, "post": post_external_id,
            "comment": comment_external_id, "text": text,
        })
        return {"comment_external_id": "new-reply-pk"}

    with patch(
        "backend.services.social_session_service.social_session_service.reply_instagram",
        side_effect=_fake_reply,
    ):
        result = comment_service.reply_to_comment(
            company_id=COMPANY_ID, comment_id=comment_row_id, text="Thank you!", actor_user_id=7,
        )

    assert result["status"] == "answered"
    assert calls["post"] == "m1"
    assert calls["comment"] == "c9"

    with db.connect() as conn:
        parent = conn.execute(
            "SELECT status, replied_by_user_id FROM social_post_comments WHERE id = ?",
            (comment_row_id,),
        ).fetchone()
        stored_reply = conn.execute(
            "SELECT is_from_business FROM social_post_comments WHERE comment_external_id = 'new-reply-pk'"
        ).fetchone()
    assert parent["status"] == "answered"
    assert parent["replied_by_user_id"] == 7
    assert stored_reply["is_from_business"] == 1


def test_instagram_sync_ingests_posts_and_comments(fresh_db):
    from backend.services.social_session_service import social_session_service
    from backend.services.comment_service import comment_service

    account_id = _insert_direct_account("instagram_direct", "500")

    class _User:
        pk = 999
        username = "a_customer"

    class _OwnUser:
        pk = 500
        username = "the_business"

    class _Comment:
        def __init__(self, pk, user, text):
            self.pk = pk
            self.user = user
            self.text = text
            self.replied_to_comment_id = None
            self.created_at_utc = None

    class _Media:
        pk = 111
        id = "111_500"
        code = "ABC"
        caption_text = "Our new product"
        thumbnail_url = "https://cdn.example/img.jpg"

    class _FakeClient:
        def user_medias(self, pk, amount):
            return [_Media()]

        def media_comments(self, media_id, amount):
            return [_Comment(1, _User(), "price?"), _Comment(2, _OwnUser(), "DM sent")]

    with patch.object(social_session_service, "_instagram_client", return_value=_FakeClient()):
        outcome = social_session_service.sync_instagram(company_id=COMPANY_ID, account_id=account_id)

    assert outcome == {"posts_synced": 1, "comments_synced": 2}
    posts = comment_service.list_posts(company_id=COMPANY_ID)
    assert len(posts) == 1
    assert posts[0]["post_external_id"] == "111"
    comments = comment_service.list_comments(company_id=COMPANY_ID, post_external_id="111")
    assert len(comments) == 2
    business_flags = {c["comment_external_id"]: c["is_from_business"] for c in comments}
    assert business_flags["1"] == 0
    assert business_flags["2"] == 1
