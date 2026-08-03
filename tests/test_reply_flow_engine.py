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
    from backend.services.appointment_service import appointment_service
    from backend.services.call_log_service import call_log_service
    from backend.services.catalogue_service import catalogue_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.customer_service import customer_service
    from backend.services.department_service import department_service
    from backend.services.notification_service import notification_service
    from backend.services.diagnostics_service import diagnostics_service
    from backend.services.reply_flow_service import reply_flow_service
    from backend.services.task_service import task_service
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
    task_service.ensure_schema()
    catalogue_service.ensure_schema()
    reply_flow_engine.ensure_schema()
    customer_service.ensure_schema()
    appointment_service.ensure_schema()
    call_log_service.ensure_schema()

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


def _create_flow(*, nodes, edges, channels=None, departments=None, status="active", trigger_type=None, trigger_config=None):
    from backend.services.reply_flow_service import reply_flow_service
    flow = reply_flow_service.create(
        company_id=COMPANY_ID, name="Test Flow", channels=channels, departments=departments,
        trigger_type=trigger_type, trigger_config=trigger_config,
    )
    return reply_flow_service.update(company_id=COMPANY_ID, flow_id=flow["id"], nodes=nodes, edges=edges, status=status)


def _node(node_id, node_type, config=None, label=None):
    return {"id": node_id, "type": "step", "position": {"x": 0, "y": 0}, "data": {"nodeType": node_type, "label": label or node_type, "config": config or {}}}


def _edge(source, target, source_handle=None):
    edge = {"id": f"{source}->{target}", "source": source, "target": target}
    if source_handle is not None:
        edge["sourceHandle"] = source_handle
    return edge


def _make_customer_with_identity(channel=CHANNEL, external_user_id=USER_ID):
    """Direct-SQL customer + linked channel identity, the minimum needed for
    appointment/call-log triggers to resolve a channel to send through."""
    from database.database import db
    now = "2026-01-01T00:00:00+00:00"
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO customers (company_id, display_name, first_seen_at, last_seen_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (COMPANY_ID, "Test Customer", now, now, now, now),
        )
        customer_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO customer_identities (company_id, customer_id, channel, external_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (COMPANY_ID, customer_id, channel, external_user_id, now, now),
        )
        conn.commit()
    return customer_id


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


def test_condition_with_single_edge_falls_through(flow_env):
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


def test_condition_branches_true_vs_false_edges(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "ask_question", {"question": "How many?", "save_as": "qty"}),
            _node("n2", "condition", {"variable": "qty", "operator": "greater_than", "value": "5"}),
            _node("yes", "canned_reply", {"text": "Bulk order path"}),
            _node("no", "canned_reply", {"text": "Regular order path"}),
        ],
        edges=[_edge("n1", "n2"), _edge("n2", "yes"), _edge("n2", "no")],
    )

    reply_flow_engine.maybe_handle(_make_request("hi"))  # lands on ask_question
    bulk = reply_flow_engine.maybe_handle(_make_request("10"))
    assert bulk.text == "Bulk order path"


def test_condition_false_branch(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "ask_question", {"question": "How many?", "save_as": "qty"}),
            _node("n2", "condition", {"variable": "qty", "operator": "greater_than", "value": "5"}),
            _node("yes", "canned_reply", {"text": "Bulk order path"}),
            _node("no", "canned_reply", {"text": "Regular order path"}),
        ],
        edges=[_edge("n1", "n2"), _edge("n2", "yes"), _edge("n2", "no")],
    )

    reply_flow_engine.maybe_handle(_make_request("hi"))
    regular = reply_flow_engine.maybe_handle(_make_request("2"))
    assert regular.text == "Regular order path"


def test_condition_true_branch_found_by_handle_even_when_drawn_second(flow_env):
    """Regression test: before sourceHandle-based branching, the true/false
    outcome was decided purely by which edge was drawn FIRST in the builder
    (edges[0] = true, edges[1] = false), with no visible label — a flow
    author who drew the "no" branch before "yes" got them silently swapped.
    Draw "no" FIRST here on purpose; the labeled sourceHandle must still
    resolve to the true branch regardless of array order."""
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "ask_question", {"question": "How many?", "save_as": "qty"}),
            _node("n2", "condition", {"variable": "qty", "operator": "greater_than", "value": "5"}),
            _node("yes", "canned_reply", {"text": "Bulk order path"}),
            _node("no", "canned_reply", {"text": "Regular order path"}),
        ],
        edges=[
            _edge("n1", "n2"),
            _edge("n2", "no", source_handle="false"),
            _edge("n2", "yes", source_handle="true"),
        ],
    )

    reply_flow_engine.maybe_handle(_make_request("hi"))
    bulk = reply_flow_engine.maybe_handle(_make_request("10"))
    assert bulk.text == "Bulk order path"


def test_condition_false_branch_found_by_handle_even_when_drawn_second(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "ask_question", {"question": "How many?", "save_as": "qty"}),
            _node("n2", "condition", {"variable": "qty", "operator": "greater_than", "value": "5"}),
            _node("yes", "canned_reply", {"text": "Bulk order path"}),
            _node("no", "canned_reply", {"text": "Regular order path"}),
        ],
        edges=[
            _edge("n1", "n2"),
            _edge("n2", "yes", source_handle="true"),
            _edge("n2", "no", source_handle="false"),
        ],
    )

    reply_flow_engine.maybe_handle(_make_request("hi"))
    regular = reply_flow_engine.maybe_handle(_make_request("2"))
    assert regular.text == "Regular order path"


def test_create_task_node_creates_a_real_task(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from backend.services.task_service import task_service

    _create_flow(
        nodes=[
            _node("n1", "create_task", {"task_type": "complaint", "note": "Customer is upset about a late delivery."}),
            _node("n2", "end"),
        ],
        edges=[_edge("n1", "n2")],
    )

    reply_flow_engine.maybe_handle(_make_request("I have a complaint"))

    tasks = task_service.list_tasks(company_id=COMPANY_ID)
    matching = [t for t in tasks["items"] if t["task_type"] == "complaint"]
    assert len(matching) == 1
    assert "late delivery" in matching[0]["description"]


def test_appointment_node_creates_a_follow_up_task_not_a_fake_appointment(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from backend.services.task_service import task_service

    _create_flow(
        nodes=[_node("n1", "appointment", {"note": "Wants a repair appointment next week"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
    )

    reply_flow_engine.maybe_handle(_make_request("I need an appointment"))

    tasks = task_service.list_tasks(company_id=COMPANY_ID)
    assert len(tasks["items"]) == 1
    assert "repair appointment" in tasks["items"][0]["description"]


def test_product_suggest_uses_real_catalogue_data(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from backend.services.catalogue_service import catalogue_service

    catalogue_service.create_product(company_id=COMPANY_ID, name="iPhone 15", sku="IP15", description="Latest model", price_cents=99900)

    _create_flow(
        nodes=[_node("n1", "product_suggest", {"note": "iPhone"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
    )

    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert "iPhone 15" in response.text
    assert "999.00" in response.text


def test_product_suggest_says_nothing_when_no_match(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[_node("n1", "product_suggest", {"note": "nonexistent gadget"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
    )

    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert response is None  # nothing invented, nothing sent


def test_timeout_followup_arms_a_real_reminder(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from backend.services.conversation_control_service import conversation_control_service

    _create_flow(
        nodes=[
            _node("n1", "timeout_followup", {"wait_minutes": 30, "text": "Still there?"}),
            _node("n2", "end"),
        ],
        edges=[_edge("n1", "n2")],
    )

    reply_flow_engine.maybe_handle(_make_request("hi"))

    state = conversation_control_service.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=USER_ID)
    assert state["reminder_at"] is not None
    assert state["reminder_auto_send"] == 1
    assert state["reminder_message_text"] == "Still there?"


def test_close_chat_sends_closing_text_and_ends(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[_node("n1", "close_chat", {"ask_reschedule": True}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
    )

    response = reply_flow_engine.maybe_handle(_make_request("bye"))
    assert "follow-up" in response.text.lower()

    second = reply_flow_engine.maybe_handle(_make_request("actually one more thing"))
    assert second is None  # flow ended


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


def test_exit_when_keeps_customer_on_same_ai_node_until_satisfied(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[
            _node("n1", "ai_direct", {"instructions": "Collect the customer's address.", "exit_when": "the customer has given a full address"}),
            _node("n2", "canned_reply", {"text": "Thanks, address noted."}),
            _node("n3", "end"),
        ],
        edges=[_edge("n1", "n2"), _edge("n2", "n3")],
    )

    with patch("core.reply_flow_engine.ai_router") as mock_router, \
            patch.object(reply_flow_engine, "_check_exit_condition") as mock_check:
        mock_router.route.return_value = {"reply": "What's your address?"}

        mock_check.return_value = False
        first = reply_flow_engine.maybe_handle(_make_request("hi, I need delivery"))
        assert first.text == "What's your address?"

        from database.database import db
        with db.connect() as conn:
            row = conn.execute("SELECT current_node_id, status FROM reply_flow_sessions WHERE company_id=? AND channel=? AND external_user_id=?", (COMPANY_ID, CHANNEL, USER_ID)).fetchone()
        assert row["current_node_id"] == "n1"
        assert row["status"] == "active"

        mock_router.route.return_value = {"reply": "Thanks, that's a valid address."}
        mock_check.return_value = True
        second = reply_flow_engine.maybe_handle(_make_request("123 Main St, Beirut"))
        assert second is not None
        assert second.text == "Thanks, that's a valid address."

        with db.connect() as conn:
            row = conn.execute("SELECT current_node_id, status FROM reply_flow_sessions WHERE company_id=? AND channel=? AND external_user_id=?", (COMPANY_ID, CHANNEL, USER_ID)).fetchone()
        assert row["current_node_id"] == "n1"
        assert row["status"] == "active"

        # exit condition was satisfied last turn -> this next customer
        # message advances past the AI node into the rest of the flow.
        third = reply_flow_engine.maybe_handle(_make_request("ok thanks"))

    assert third is not None
    assert third.text == "Thanks, address noted."
    assert mock_router.route.call_count == 2


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


# -- trigger registry / event-based triggers -------------------------------

def test_flow_with_non_default_trigger_does_not_fire_on_new_message(flow_env):
    """A flow scoped to conversation_closed (or any non-default trigger)
    must NOT be picked by an ordinary incoming customer message — only
    new_conversation (the default, today's only trigger) does that."""
    from core.reply_flow_engine import reply_flow_engine

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "should never send on a plain message"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="conversation_closed",
    )

    response = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert response is None


def _add_agent_user():
    from database.database import db
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.commit()


def test_conversation_closed_trigger_fires_via_folder_done(flow_env):
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service

    auth_service.create_tables()
    _add_agent_user()

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "How would you rate us, 1 to 5?"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="conversation_closed",
    )
    conversation_control_service.get_or_create(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=USER_ID)

    with patch("core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}) as mock_send, \
         patch("core.reply_flow_engine.save_conversation_message") as mock_save:
        conversation_control_service.update_workspace_state(
            company_id=COMPANY_ID, channel=CHANNEL, external_user_id=USER_ID, actor_user_id=1, folder="done",
        )

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "How would you rate us, 1 to 5?"
    mock_save.assert_called_once()

    from database.database import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM reply_flow_sessions WHERE company_id=? AND channel=? AND external_user_id=?",
            (COMPANY_ID, CHANNEL, USER_ID),
        ).fetchone()
    assert row["status"] == "ended"


def test_conversation_closed_trigger_fires_via_status_closed(flow_env):
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service

    auth_service.create_tables()
    _add_agent_user()

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Thanks, please rate us!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="conversation_closed",
    )
    conversation_control_service.get_or_create(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=USER_ID)

    with patch("core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}) as mock_send, \
         patch("core.reply_flow_engine.save_conversation_message"):
        conversation_control_service.update_state(
            company_id=COMPANY_ID, channel=CHANNEL, external_user_id=USER_ID, actor_user_id=1, status="closed",
        )

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "Thanks, please rate us!"


def test_appointment_created_trigger_fires_for_linked_customer(flow_env):
    from backend.services.appointment_service import appointment_service

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Your appointment is booked!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="appointment_created",
    )
    customer_id = _make_customer_with_identity()

    with patch("core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}) as mock_send, \
         patch("core.reply_flow_engine.save_conversation_message"):
        appointment_service.create_appointment(
            company_id=COMPANY_ID, title="Repair", scheduled_at="2026-01-02T10:00:00+00:00", customer_id=customer_id,
        )

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "Your appointment is booked!"


def test_appointment_completed_trigger_fires_once_on_status_transition(flow_env):
    from backend.services.appointment_service import appointment_service

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Please rate your visit!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="appointment_completed",
    )
    customer_id = _make_customer_with_identity()
    appt = appointment_service.create_appointment(
        company_id=COMPANY_ID, title="Repair", scheduled_at="2026-01-02T10:00:00+00:00", customer_id=customer_id,
    )

    with patch("core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}) as mock_send, \
         patch("core.reply_flow_engine.save_conversation_message"):
        appointment_service.update_appointment(company_id=COMPANY_ID, appointment_id=appt["id"], values={"status": "completed"})

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "Please rate your visit!"

    # Already completed -> completed again (or any other field update) must not re-fire.
    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send_again:
        appointment_service.update_appointment(company_id=COMPANY_ID, appointment_id=appt["id"], values={"notes": "all done"})
    mock_send_again.assert_not_called()


def test_appointment_reminder_fires_once_within_window(flow_env):
    from backend.services.appointment_service import appointment_service
    from core.reply_flow_engine import reply_flow_engine
    from datetime import datetime, timedelta, timezone

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Reminder: your appointment is coming up!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="appointment_reminder",
        trigger_config={"minutes_before": 60},
    )
    customer_id = _make_customer_with_identity()
    scheduled_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    appointment_service.create_appointment(
        company_id=COMPANY_ID, title="Repair", scheduled_at=scheduled_at, customer_id=customer_id,
    )

    with patch("core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}) as mock_send, \
         patch("core.reply_flow_engine.save_conversation_message"):
        reply_flow_engine.check_appointment_reminders()

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "Reminder: your appointment is coming up!"

    # Claimed via flow_reminder_sent_at -> a second scan must not re-fire.
    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send_again:
        reply_flow_engine.check_appointment_reminders()
    mock_send_again.assert_not_called()


def test_appointment_reminder_does_not_fire_outside_window(flow_env):
    from backend.services.appointment_service import appointment_service
    from core.reply_flow_engine import reply_flow_engine
    from datetime import datetime, timedelta, timezone

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Reminder!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="appointment_reminder",
        trigger_config={"minutes_before": 10},
    )
    customer_id = _make_customer_with_identity()
    scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    appointment_service.create_appointment(
        company_id=COMPANY_ID, title="Repair", scheduled_at=scheduled_at, customer_id=customer_id,
    )

    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send:
        reply_flow_engine.check_appointment_reminders()
    mock_send.assert_not_called()


def test_call_logged_trigger_fires_for_linked_customer(flow_env):
    from backend.services.call_log_service import call_log_service

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Thanks for calling!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="call_logged",
    )
    customer_id = _make_customer_with_identity()

    with patch("core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}) as mock_send, \
         patch("core.reply_flow_engine.save_conversation_message"):
        call_log_service.create_call_log(company_id=COMPANY_ID, direction="inbound", customer_id=customer_id)

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "Thanks for calling!"


def test_task_completed_trigger_fires_once_on_status_transition(flow_env):
    from backend.services.task_service import task_service

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Your request has been resolved!"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="task_completed",
    )
    customer_id = _make_customer_with_identity()
    task = task_service.create_task(company_id=COMPANY_ID, title="Fix router", customer_id=customer_id)

    with patch("core.reply_flow_engine.send_whatsapp_text", return_value={"sent": True}) as mock_send, \
         patch("core.reply_flow_engine.save_conversation_message"):
        task_service.update_task(company_id=COMPANY_ID, task_id=task["id"], values={"status": "done"})

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["text"] == "Your request has been resolved!"

    # Already done -> done again (or any other field update) must not re-fire.
    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send_again:
        task_service.update_task(company_id=COMPANY_ID, task_id=task["id"], values={"status": "done"})
    mock_send_again.assert_not_called()


def test_task_completed_trigger_is_noop_without_linked_customer(flow_env):
    from backend.services.task_service import task_service

    _create_flow(
        nodes=[_node("n1", "canned_reply", {"text": "Should never send"}), _node("n2", "end")],
        edges=[_edge("n1", "n2")],
        trigger_type="task_completed",
    )
    task = task_service.create_task(company_id=COMPANY_ID, title="Internal cleanup")

    with patch("core.reply_flow_engine.send_whatsapp_text") as mock_send:
        task_service.update_task(company_id=COMPANY_ID, task_id=task["id"], values={"status": "done"})
    mock_send.assert_not_called()


# -- ask_question button-choice mode ---------------------------------------

def _button_question_flow(mode):
    return _create_flow(
        nodes=[
            _node("n1", "ask_question", {
                "question": "Which department?", "save_as": "dept", "mode": mode,
                "options": [{"label": "Sales", "value": "sales"}, {"label": "Support", "value": "support"}],
            }),
            _node("n2", "canned_reply", {"text": "Routing to {{dept}}"}),
            _node("n3", "end"),
        ],
        edges=[_edge("n1", "n2"), _edge("n2", "n3")],
    )


def test_ask_question_buttons_mode_matches_by_label_and_advances(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _button_question_flow("buttons")

    first = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert first.text == "Which department?"
    assert first.buttons == ["Sales", "Support"]

    second = reply_flow_engine.maybe_handle(_make_request("Sales"))
    assert second.text == "Routing to sales"


def test_ask_question_buttons_mode_matches_by_position(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _button_question_flow("buttons")

    reply_flow_engine.maybe_handle(_make_request("hi"))
    second = reply_flow_engine.maybe_handle(_make_request("2"))
    assert second.text == "Routing to support"


def test_ask_question_buttons_mode_reprompts_on_free_text(flow_env):
    from core.reply_flow_engine import reply_flow_engine
    from database.database import db

    _button_question_flow("buttons")

    reply_flow_engine.maybe_handle(_make_request("hi"))
    second = reply_flow_engine.maybe_handle(_make_request("something random"))
    assert "Please choose one of the options below." in second.text
    assert "Which department?" in second.text
    assert second.buttons == ["Sales", "Support"]

    with db.connect() as conn:
        row = conn.execute(
            "SELECT current_node_id FROM reply_flow_sessions WHERE company_id=? AND channel=? AND external_user_id=?",
            (COMPANY_ID, CHANNEL, USER_ID),
        ).fetchone()
    assert row["current_node_id"] == "n1"  # still parked on the question, not advanced

    third = reply_flow_engine.maybe_handle(_make_request("Support"))
    assert third.text == "Routing to support"


def test_ask_question_both_mode_accepts_free_text_fallback(flow_env):
    from core.reply_flow_engine import reply_flow_engine

    _button_question_flow("both")

    reply_flow_engine.maybe_handle(_make_request("hi"))
    second = reply_flow_engine.maybe_handle(_make_request("Billing please"))
    assert second.text == "Routing to Billing please"


def test_ask_question_text_mode_unaffected_by_new_fields(flow_env):
    """Default mode ("text", or omitted entirely) must behave exactly as
    before — zero behavior change for every existing ask_question node."""
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
    assert first.buttons == []

    second = reply_flow_engine.maybe_handle(_make_request("a refund"))
    assert second.text == "Got it: a refund"
