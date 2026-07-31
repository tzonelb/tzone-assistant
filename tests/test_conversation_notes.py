"""
Tests for internal conversation notes with @mention support — an
employee can leave a note visible only to staff, optionally tagging
colleagues, who then get notified.

Run with: python -m pytest tests/test_conversation_notes.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
CHANNEL = "messenger"
CUSTOMER_ID = "test_customer_1"


@pytest.fixture()
def fresh_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.notification_service import notification_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    notification_service.ensure_schema()

    with db.connect() as conn:
        for uid, email in ((101, "emp1@test.local"), (202, "emp2@test.local")):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, full_name, status) VALUES (?, ?, ?, 'active')",
                (uid, email, email),
            )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 101, 'active')")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 202, 'active')")
        conn.commit()

    yield conversation_control_service

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


def test_add_note_without_mentions(fresh_db):
    svc = fresh_db
    note = svc.add_note(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        author_user_id=101, note="Customer wants a refund.",
    )
    assert note["note"] == "Customer wants a refund."
    assert note["mentioned_user_ids"] == []


def test_add_note_rejects_empty_text(fresh_db):
    svc = fresh_db
    with pytest.raises(ValueError):
        svc.add_note(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID, author_user_id=101, note="   ")


def test_add_note_with_valid_mention(fresh_db):
    svc = fresh_db
    note = svc.add_note(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        author_user_id=101, note="@Agent Two can you check this?", mentioned_user_ids=[202],
    )
    assert note["mentioned_user_ids"] == [202]


def test_add_note_filters_out_users_not_in_company(fresh_db):
    svc = fresh_db
    note = svc.add_note(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        author_user_id=101, note="hey", mentioned_user_ids=[202, 9999],
    )
    assert note["mentioned_user_ids"] == [202]


def test_mentioning_someone_creates_a_notification(fresh_db):
    from backend.services.notification_service import notification_service
    svc = fresh_db
    svc.add_note(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        author_user_id=101, note="please check this", mentioned_user_ids=[202],
    )
    notifications = notification_service.list_for_user(company_id=COMPANY_ID, user_id=202)
    assert any(n.get("notification_type") == "conversation_mention" for n in notifications)


def test_mentioning_self_does_not_notify(fresh_db):
    from backend.services.notification_service import notification_service
    svc = fresh_db
    svc.add_note(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        author_user_id=101, note="note to self", mentioned_user_ids=[101],
    )
    notifications = notification_service.list_for_user(company_id=COMPANY_ID, user_id=101)
    assert not any(n.get("notification_type") == "conversation_mention" for n in notifications)


def test_notes_list_includes_mentions(fresh_db):
    svc = fresh_db
    svc.add_note(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        author_user_id=101, note="hey @Agent Two", mentioned_user_ids=[202],
    )
    history = svc.timeline(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert len(history["notes"]) == 1
    assert history["notes"][0]["mentioned_user_ids"] == [202]
