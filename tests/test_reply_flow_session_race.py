"""Regression test for a session-corruption race between the
message-triggered path (maybe_handle) and the event-triggered path
(fire_event) in core/reply_flow_engine.py.

fire_event() (used by conversation_closed, appointment_created/completed,
call_logged, task_completed, and the periodic appointment_reminder /
customer_no_reply / team_no_reply workers) used to call
self._start_session(...) / self._advance(...) directly, WITHOUT taking the
per-(company,channel,user) _session_lock that maybe_handle holds for its
whole read-compute-write. An event source firing for a customer who is
simultaneously mid-answer on a message-triggered flow could interleave with
maybe_handle's critical section and corrupt the session row: the unlocked
_start_session upsert would change flow_id (resetting current_node_id to
NULL) while maybe_handle's in-flight write landed a current_node_id from the
OLD flow on top of it -- a mismatched pair that doesn't exist in the new
flow's graph, sometimes ending the row permanently ('status=ended') and
silently killing the flow for that customer forever.

Fixed by having fire_event()/fire_event_for_customer() take the same
_session_lock before touching a session. This test proves the fix holds
under REAL concurrent threads (not a same-thread injection hack, which would
now self-deadlock on the non-reentrant lock -- a stronger proof the lock is
actually exclusive): a message-triggered flow answer and an event-triggered
flow race for the same customer many times, and the session row must always
end up self-consistent (current_node_id valid for whatever flow_id it
belongs to; no session_row.flow_id X + current_node_id-only-in-flow-Y state).
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

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


def _create_flow(*, nodes, edges, trigger_type=None, status="active"):
    from backend.services.reply_flow_service import reply_flow_service
    flow = reply_flow_service.create(
        company_id=COMPANY_ID, name="Test Flow", trigger_type=trigger_type,
    )
    return reply_flow_service.update(company_id=COMPANY_ID, flow_id=flow["id"], nodes=nodes, edges=edges, status=status)


def _node(node_id, node_type, config=None):
    return {"id": node_id, "type": "step", "position": {"x": 0, "y": 0}, "data": {"nodeType": node_type, "label": node_type, "config": config or {}}}


def _edge(source, target):
    return {"id": f"{source}->{target}", "source": source, "target": target}


def test_fire_event_and_maybe_handle_serialize_under_real_concurrency(flow_env):
    """A message-triggered answer (maybe_handle) and an event-triggered flow
    (fire_event) race for the SAME customer on real threads, repeatedly. The
    session row must always end up self-consistent afterward: whatever
    flow_id it has, current_node_id must be a real node in THAT flow (or
    None) -- never a node id from a different flow. Before the fix, this
    reliably corrupted the row (see the module docstring); after the fix,
    the shared _session_lock serializes the two paths instead of racing."""
    from database.database import db
    from core.reply_flow_engine import reply_flow_engine
    from backend.services.reply_flow_service import reply_flow_service

    flow_a = _create_flow(
        nodes=[
            _node("g1", "greeting", {"text": "Hi!"}),
            _node("q1", "ask_question", {"question": "What's your name?", "save_as": "name"}),
            _node("q2", "ask_question", {"question": "What's your email?", "save_as": "email"}),
            _node("e1", "end"),
        ],
        edges=[_edge("g1", "q1"), _edge("q1", "q2"), _edge("q2", "e1")],
        trigger_type="new_conversation",
    )
    flow_b = _create_flow(
        nodes=[
            _node("fb1", "canned_reply", {"text": "Thanks for chatting -- rate us!"}),
            _node("fbe", "end"),
        ],
        edges=[_edge("fb1", "fbe")],
        trigger_type="conversation_closed",
    )

    # Start the customer on flow A so there's a real in-progress session to
    # race against on every iteration.
    first = reply_flow_engine.maybe_handle(_make_request("hi"))
    assert first.text == "Hi!\n\nWhat's your name?"

    errors = []

    def racer():
        try:
            reply_flow_engine.fire_event(
                company_id=COMPANY_ID, trigger_type="conversation_closed",
                channel=CHANNEL, external_user_id=USER_ID,
            )
        except Exception as exc:  # pragma: no cover - fire_event itself never raises
            errors.append(exc)

    def answerer():
        try:
            reply_flow_engine.maybe_handle(_make_request("John"))
        except Exception as exc:
            errors.append(exc)

    # Run both paths concurrently, back-to-back, several times -- if the lock
    # were missing (or reentrancy broke it), this reliably produces a
    # mismatched flow_id/current_node_id pair or hangs (deadlock).
    for _ in range(20):
        t1 = threading.Thread(target=racer)
        t2 = threading.Thread(target=answerer)
        t1.start(); t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive(), "a racing thread did not finish -- deadlock"

    assert not errors, f"unexpected exceptions during the race: {errors}"

    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM reply_flow_sessions WHERE company_id=? AND channel=? AND external_user_id=?",
            (COMPANY_ID, CHANNEL, USER_ID),
        ).fetchone()
    assert row is not None
    row = dict(row)

    # The core invariant the lock protects: current_node_id, if set, must be
    # a real node in whichever flow flow_id now points to.
    if row["current_node_id"] is not None:
        flow_now = reply_flow_service.get(company_id=COMPANY_ID, flow_id=row["flow_id"])
        node = reply_flow_engine._node_by_id(flow_now, row["current_node_id"])
        assert node is not None, (
            f"CORRUPTED session row: current_node_id={row['current_node_id']!r} does not "
            f"exist in flow_id={row['flow_id']} ({flow_now.get('name')!r}) -- the "
            f"message-triggered and event-triggered paths raced without serializing."
        )
