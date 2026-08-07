"""Tests for the two time-based no-reply Reply Flow triggers,
customer_no_reply and team_no_reply (ported from the parallel
development branch's time-based Bot Triggers, re-expressed in this
branch's Reply Flow trigger system).

Both are scanned by reply_flow_engine.check_no_reply_triggers() on the
same reminder_worker cadence as check_appointment_reminders, with the
same claim-then-act discipline:

- customer_no_reply fires when the ball is in the customer's court
  (unread_count = 0) and the conversation has had no activity for
  trigger_config.minutes_of_silence minutes -- once per silence period
  (the claim marker stores the updated_at it fired for; fresh activity
  re-arms it).
- team_no_reply fires when a customer is waiting on a human
  (workflow_state = 'waiting_agent') for trigger_config.minutes_waiting
  minutes -- once per waiting period.

Run with: python3 -m pytest tests/test_no_reply_triggers.py -v
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COMPANY_ID = 1
CHANNEL = "whatsapp"
USER_ID = "96170000001"


@pytest.fixture()
def flow_env():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.customer_service import customer_service
    from backend.services.department_service import department_service
    from backend.services.diagnostics_service import diagnostics_service
    from backend.services.notification_service import notification_service
    from backend.services.reply_flow_service import reply_flow_service
    from core.reply_flow_engine import reply_flow_engine

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    department_service.ensure_schema()
    notification_service.ensure_schema()
    diagnostics_service.ensure_schema()
    reply_flow_service.ensure_schema()
    reply_flow_engine.ensure_schema()
    customer_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) "
            "VALUES (1, 'Test Co', 'test-co', 1)"
        )
        conn.commit()

    yield db

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


def _create_flow(*, trigger_type, trigger_config, text):
    from backend.services.reply_flow_service import reply_flow_service

    flow = reply_flow_service.create(
        company_id=COMPANY_ID,
        name="No-reply Flow",
        trigger_type=trigger_type,
        trigger_config=trigger_config,
    )
    nodes = [
        {
            "id": "n1",
            "type": "step",
            "position": {"x": 0, "y": 0},
            "data": {"nodeType": "canned_reply", "label": "canned_reply", "config": {"text": text}},
        },
        {
            "id": "n2",
            "type": "step",
            "position": {"x": 0, "y": 0},
            "data": {"nodeType": "end", "label": "end", "config": {}},
        },
    ]
    edges = [{"id": "n1->n2", "source": "n1", "target": "n2"}]
    return reply_flow_service.update(
        company_id=COMPANY_ID, flow_id=flow["id"], nodes=nodes, edges=edges, status="active"
    )


def _seed_conversation(
    db,
    *,
    minutes_ago,
    unread_count=0,
    workflow_state="ai_active",
    external_user_id=USER_ID,
):
    """A conversation whose last activity was `minutes_ago` minutes ago."""
    stamp = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO conversations (
                company_id, channel, external_user_id, status,
                workflow_state, unread_count, last_message_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?)
            """,
            (
                COMPANY_ID,
                CHANNEL,
                external_user_id,
                workflow_state,
                unread_count,
                stamp,
                stamp,
                stamp,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def test_customer_no_reply_fires_once_after_silence(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        text="Still there? Happy to help!",
    )
    _seed_conversation(flow_env, minutes_ago=45, unread_count=0)

    with patch(
        "core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}
    ) as mock_send, patch("core.reply_flow_engine.save_conversation_message"):
        reply_flow_engine.check_no_reply_triggers()

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "Still there? Happy to help!"

    # Claimed -> a second scan of the SAME silence period must not re-fire.
    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send_again:
        reply_flow_engine.check_no_reply_triggers()
    mock_send_again.assert_not_called()


def test_customer_no_reply_respects_configured_minutes(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 120},
        text="Nudge",
    )
    # Only 45 minutes of silence -- under the 120-minute threshold.
    _seed_conversation(flow_env, minutes_ago=45, unread_count=0)

    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send:
        reply_flow_engine.check_no_reply_triggers()
    mock_send.assert_not_called()


def test_customer_no_reply_skips_conversations_with_pending_customer_message(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        text="Nudge",
    )
    # unread_count > 0 -- the ball is in OUR court, not the customer's.
    _seed_conversation(flow_env, minutes_ago=90, unread_count=2)

    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send:
        reply_flow_engine.check_no_reply_triggers()
    mock_send.assert_not_called()


def test_fresh_activity_rearms_customer_no_reply(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from database.database import db

    _create_flow(
        trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        text="Nudge",
    )
    conversation_id = _seed_conversation(flow_env, minutes_ago=45, unread_count=0)

    with patch(
        "core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}
    ), patch("core.reply_flow_engine.save_conversation_message"):
        reply_flow_engine.check_no_reply_triggers()

    # New activity (customer replied, then went silent again): updated_at
    # moves -> the marker no longer matches -> the trigger re-arms.
    new_stamp = (
        datetime.now(timezone.utc) - timedelta(minutes=40)
    ).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (new_stamp, conversation_id),
        )
        conn.commit()

    with patch(
        "core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}
    ) as mock_send, patch("core.reply_flow_engine.save_conversation_message"):
        reply_flow_engine.check_no_reply_triggers()
    mock_send.assert_called_once()


def test_team_no_reply_fires_for_waiting_customer_once(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        trigger_type="team_no_reply",
        trigger_config={"minutes_waiting": 15},
        text="Sorry for the wait — an agent will be with you shortly.",
    )
    _seed_conversation(
        flow_env, minutes_ago=20, workflow_state="waiting_agent", unread_count=1
    )

    with patch(
        "core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}
    ) as mock_send, patch("core.reply_flow_engine.save_conversation_message"):
        reply_flow_engine.check_no_reply_triggers()

    mock_send.assert_called_once()

    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send_again:
        reply_flow_engine.check_no_reply_triggers()
    mock_send_again.assert_not_called()


def test_team_no_reply_ignores_conversations_not_waiting_on_agent(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        trigger_type="team_no_reply",
        trigger_config={"minutes_waiting": 15},
        text="Sorry for the wait",
    )
    _seed_conversation(
        flow_env, minutes_ago=60, workflow_state="ai_active", unread_count=0
    )

    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send:
        reply_flow_engine.check_no_reply_triggers()
    mock_send.assert_not_called()
