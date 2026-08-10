"""
Real test for channels/telegram/processor.py — verifies an incoming
Telegram message goes through the same unified pipeline Messenger uses
(conversation state, notifications, AI reply queueing), instead of the
old standalone core.engine path that never touched
conversation_control_service at all.

Run with: python3 -m pytest tests/test_telegram_incoming.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
CUSTOMER_ID = "555444333"


@pytest.fixture()
def fresh_env(tmp_path):
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.notification_service import notification_service
    from backend.services.customer_service import customer_service
    import core.conversation_store as conversation_store

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    original_base_dir = conversation_store.BASE_DIR
    conversation_store.BASE_DIR = tmp_path / "conversations"
    conversation_store.BASE_DIR.mkdir(parents=True, exist_ok=True)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()
    notification_service.ensure_schema()
    customer_service.ensure_schema()

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name) VALUES (1, 'Test Co')")
        conn.commit()

    yield

    conversation_store.BASE_DIR = original_base_dir
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


def test_incoming_telegram_message_creates_conversation_state(fresh_env):
    from channels.telegram.processor import process_telegram_message
    from backend.services.conversation_control_service import conversation_control_service

    process_telegram_message(
        user_id=CUSTOMER_ID,
        text="I need help with my IPTV subscription",
        customer_name="Jean Test",
        company_id=COMPANY_ID,
    )

    state = conversation_control_service.get_state(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
    )
    assert state is not None
    assert state["id"] is not None


def test_incoming_telegram_message_creates_notification(fresh_env):
    from channels.telegram.processor import process_telegram_message
    from backend.services.notification_service import notification_service

    process_telegram_message(
        user_id=CUSTOMER_ID,
        text="Hello",
        customer_name="Jean Test",
        company_id=COMPANY_ID,
    )

    notifications = notification_service.list_for_user(
        company_id=COMPANY_ID, user_id=1,
    )
    matching = [n for n in notifications if n["channel"] == "telegram" and n["external_user_id"] == CUSTOMER_ID]
    assert len(matching) == 1
    assert matching[0]["notification_type"] == "customer_message"


def test_incoming_telegram_message_is_saved_to_conversation_store(fresh_env):
    from channels.telegram.processor import process_telegram_message
    import core.conversation_store as conversation_store

    process_telegram_message(
        user_id=CUSTOMER_ID,
        text="What's my balance?",
        customer_name="Jean Test",
        company_id=COMPANY_ID,
    )

    saved_file = conversation_store.BASE_DIR / str(COMPANY_ID) / "telegram" / f"{CUSTOMER_ID}.jsonl"
    assert saved_file.exists()
    content = saved_file.read_text(encoding="utf-8")
    assert "What's my balance?" in content


def test_incoming_telegram_message_queues_ai_reply(fresh_env):
    from channels.telegram.processor import process_telegram_message

    result = process_telegram_message(
        user_id=CUSTOMER_ID,
        text="Hi there",
        customer_name="Jean Test",
        company_id=COMPANY_ID,
    )
    assert result["queue_result"]["queued"] is True


def test_username_is_saved_to_customer_record(fresh_env):
    from channels.telegram.processor import process_telegram_message
    from backend.services.customer_service import customer_service

    result = process_telegram_message(
        user_id=CUSTOMER_ID,
        text="hello",
        customer_name="Jean Test",
        username="jean_telegram",
        company_id=COMPANY_ID,
    )
    customer_id = result["customer"]["id"]
    customer = customer_service.get_customer(company_id=COMPANY_ID, customer_id=customer_id)
    identities = [i for i in customer.get("identities", []) if i.get("channel") == "telegram"]
    assert identities and identities[0]["username"] == "jean_telegram"


def test_phone_is_saved_when_shared(fresh_env):
    from channels.telegram.processor import process_telegram_message
    from backend.services.customer_service import customer_service

    result = process_telegram_message(
        user_id=CUSTOMER_ID,
        text="[shared phone number]",
        customer_name="Jean Test",
        username="jean_telegram",
        phone="+96170123456",
        company_id=COMPANY_ID,
    )
    customer_id = result["customer"]["id"]
    customer = customer_service.get_customer(company_id=COMPANY_ID, customer_id=customer_id)
    assert customer["phone"] == "+96170123456"


def test_no_phone_provided_leaves_it_unset(fresh_env):
    from channels.telegram.processor import process_telegram_message
    from backend.services.customer_service import customer_service

    result = process_telegram_message(
        user_id=CUSTOMER_ID,
        text="hi, no phone shared",
        customer_name="Jean Test",
        company_id=COMPANY_ID,
    )
    customer_id = result["customer"]["id"]
    customer = customer_service.get_customer(company_id=COMPANY_ID, customer_id=customer_id)
    assert not customer.get("phone")


def test_message_received_while_employee_owns_conversation_does_not_get_lost(fresh_env):
    """If an employee already owns this conversation, the AI must not
    barge in — schedule_smart_reply already handles this (verified
    elsewhere), this just confirms the Telegram path reaches it."""
    from channels.telegram.processor import process_telegram_message
    from backend.services.conversation_control_service import conversation_control_service

    with __import__("database.database", fromlist=["db"]).db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) "
            "VALUES (101, 'agent@test.local', 'Agent', 'active')"
        )
        conn.commit()

    conversation_control_service.set_ai_mode(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )

    result = process_telegram_message(
        user_id=CUSTOMER_ID,
        text="Still waiting for help",
        customer_name="Jean Test",
        company_id=COMPANY_ID,
    )
    # Message still gets saved/notified even though AI won't answer.
    assert result["incoming_message"] is not None
    state = conversation_control_service.get_state(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
    )
    assert state["handled_by_ai"] is False
    assert state["assigned_user_id"] == 101
