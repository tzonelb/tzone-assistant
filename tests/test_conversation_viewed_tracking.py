"""
Real test for conversation_viewed event logging — added because management
had no visibility into who opened a conversation without acting on it.
Previously record_opened() did nothing at all for a non-owner viewer.

Run with: python3 -m pytest tests/test_conversation_viewed_tracking.py -v
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
CUSTOMER_ID = "test_customer_viewed"


@pytest.fixture()
def fresh_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        for uid, email in ((101, "emp1@test.local"), (202, "emp2@test.local")):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, full_name, status) VALUES (?, ?, ?, 'active')",
                (uid, email, email),
            )
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


def test_non_owner_opening_a_conversation_logs_a_viewed_event(fresh_db):
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )

    # Employee 202 opens a conversation owned by employee 101.
    svc.record_opened(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        actor_user_id=202,
    )

    result = svc.timeline(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    viewed_events = [e for e in result["events"] if e["event_type"] == "conversation_viewed"]
    assert len(viewed_events) == 1
    assert viewed_events[0]["actor_user_id"] == 202


def test_owner_opening_does_not_log_a_viewed_event(fresh_db):
    """The owner path is untouched — it already logs 'conversation_read'
    when there's something unread. Don't double-log for the owner."""
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )

    svc.record_opened(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        actor_user_id=101,
    )

    result = svc.timeline(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    viewed_events = [e for e in result["events"] if e["event_type"] == "conversation_viewed"]
    assert len(viewed_events) == 0
