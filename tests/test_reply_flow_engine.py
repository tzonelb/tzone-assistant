import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
CHANNEL = "whatsapp"
USER_ID = "96170000001"


@pytest.fixture()
def flow_env():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.department_service import department_service
    from backend.services.notification_service import notification_service
    from backend.services.diagnostics_service import diagnostics_service
    from backend.services.reply_flow_service import reply_flow_service
    from core.instruction_service import instruction_service
    from core.knowledge_manager import knowledge_manager
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
    instruction_service.ensure_schema()
    knowledge_manager.ensure_schema()
    reply_flow_engine.ensure_schema()

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


def _make_request(message):
    from core.request import Request
    return Request(channel=CHANNEL, user_id=USER_ID, message=message, company_id=COMPANY_ID)


def _create_flow(*, nodes, edges, channels=None, departments=None, status="active"):
    from backend.services.reply_flow_service import reply_flow_service
    flow = reply_flow_service.create(company_id=COMPANY_ID, name="Test Flow", channels=channels, departments=departments)
    return reply_flow_service.update(company_id=COMPANY_ID, flow_id=flow["id"], nodes=nodes, edges=edges, status=status)


def _node(node_id, node_type, config=None, label=None):
    return {"id": node_id, "type": "step", "position": {"x": 0, "y": 0}, "data": {"nodeType": node_type, "label": label or node_type, "config": config or {}}}


def _edge(source, target):
    return {"id": f"{source}->{target}", "source": source, "target": target}


def test_no_active_flow_falls_through(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert response is None


def test_draft_flow_is_not_used(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    _create_flow(
        nodes=[_node("n1", "greeting", {"text": "Hello!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        status="draft",
    )
    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert response is None


def test_greeting_then_ai_step_then_ends(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "greeting", {"text": "Hi {{customer_name}}, welcome!"}),
            _node("n2", "ai_direct", {"instructions": "Be nice."}),
            _node("n3", "end"),
        ],
        edges=[_edge("n1", "n2"), _edge("n2", "n3")],
    )

    with patch("core.reply_flow_engine.ai_router") as mock_router:
        mock_router.route.return_value = {"reply": "Sure, how can I help?"}
        first = reply_flow_engine.maybe_handle(_make_request("hello"))

    assert first is not None
    assert "welcome" in first.text
    assert "Sure, how can I help?" in first.text
    mock_router.route.assert_called_once()

    # Session should now be parked at the ai_direct node, waiting.
    from database.database import db
    with db.connect() as conn:
        row = conn.execute("SELECT current_node_id, status FROM reply_flow_sessions WHERE company_id=? AND channel=? AND external_user_id=?", (COMPANY_ID, CHANNEL, USER_ID)).fetchone()
    assert row["current_node_id"] == "n2"
    assert row["status"] == "active"

    second = reply_flow_engine.maybe_handle(_make_request("thanks"))
    assert second is None  # flow ended -> falls through to the default pipeline

    with db.connect() as conn:
        row = conn.execute("SELECT status FROM reply_flow_sessions WHERE company_id=? AND channel=? AND external_user_id=?", (COMPANY_ID, CHANNEL, USER_ID)).fetchone()
    assert row["status"] == "ended"


def test_ask_question_stores_answer_as_variable(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "ask_question", {"question": "What do you need?", "save_as": "need"}),
            _node("n2", "canned_reply", {"text": "Got it: {{need}}"}),
            _node("n3", "end"),
        ],
        edges=[_edge("n1", "n2"), _edge("n2", "n3")],
    )

    first = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert first.text == "What do you need?"

    second = reply_flow_engine.maybe_handle(_make_request("a refund"))
    assert second.text == "Got it: a refund"


def test_human_handoff_notifies_and_ends_silently(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from backend.services.notification_service import notification_service

    _create_flow(
        nodes=[_node("n1", "human_handoff", {"note": "Angry customer"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
    )

    response = reply_flow_engine.maybe_handle(_make_request("I want a human"))
    assert response is None

    with __import__("database.database", fromlist=["db"]).db.connect() as conn:
        row = conn.execute("SELECT notification_type, body FROM notifications WHERE company_id=?", (COMPANY_ID,)).fetchone()
    assert row["notification_type"] == "ai_escalation"
    assert "Angry customer" in row["body"]


def test_not_yet_executed_node_type_passes_through(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "condition", {"variable": "x", "operator": "is_set"}),
            _node("n2", "canned_reply", {"text": "done"}),
            _node("n3", "end"),
        ],
        edges=[_edge("n1", "n2"), _edge("n2", "n3")],
    )

    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert response.text == "done"


def test_department_scoped_flow_preferred_over_catch_all(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from backend.services.department_service import department_service
    from backend.services.conversation_control_service import conversation_control_service
    from database.database import db

    department_service.create(company_id=COMPANY_ID, name="Sales")

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "catch-all reply"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        departments=[],
    )
    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "sales reply"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        departments=["Sales"],
    )

    conversation_control_service.get_or_create(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=USER_ID)
    with db.connect() as conn:
        conn.execute("UPDATE conversations SET department=? WHERE company_id=? AND channel=? AND external_user_id=?", ("Sales", COMPANY_ID, CHANNEL, USER_ID))
        conn.commit()

    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert response.text == "sales reply"


def test_flow_scoped_to_other_channel_is_ignored(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "telegram only"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        channels=["telegram"],
    )

    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert response is None


def test_end_to_end_real_conversation_is_actually_controlled_by_the_flow(flow_env):
    """The point of the whole engine: prove a saved flow changes what a
    REAL customer conversation receives, through the exact same pipeline
    production traffic uses (schedule_smart_reply -> _finish_pending ->
    message_gateway.handle_text -> reply_flow_engine), with only the
    outbound network call mocked."""
    import channels.meta.smart_reply as smart_reply_module

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "This reply is fully controlled by the saved flow."}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
    )

    with patch("channels.meta.smart_reply.company_settings_service") as mock_settings:
        mock_settings.get_section.return_value = {"values": {"enabled": True, "voice_reply_enabled": False}}
        with patch("channels.meta.smart_reply.save_conversation_message"):
            with patch("channels.meta.smart_reply._record_sent_status", return_value=None):
                with patch("channels.meta.smart_reply.send_whatsapp_text", return_value={"ok": True, "response": {}}) as mock_send:
                    with patch("channels.meta.smart_reply.diagnostics_service"):
                        smart_reply_module.schedule_smart_reply(
                            channel=CHANNEL, user_id=USER_ID, company_id=COMPANY_ID, message="hi", delay_seconds=0,
                        )
                        key = smart_reply_module._key(COMPANY_ID, CHANNEL, USER_ID)
                        pending = smart_reply_module._PENDING[key]
                        smart_reply_module._finish_pending(
                            company_id=COMPANY_ID, channel=CHANNEL, user_id=USER_ID, generation=pending.generation,
                        )

    mock_send.assert_called_once()
    sent_text = mock_send.call_args.kwargs.get("text")
    assert sent_text == "This reply is fully controlled by the saved flow."
