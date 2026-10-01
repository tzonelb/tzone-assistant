"""Reply Flows: stored per company, and actually run for a customer.

Two failure classes are guarded. The leak: one company's flow reaching
another's customers. And the hollow feature: a flow an owner drew that never
runs. So alongside the service's CRUD and scoping, the engine tests drive a real
graph a turn at a time and assert the customer gets the scripted words -- and,
just as important, that a company with no active flow is left completely alone
(the safety the live reply path depends on).
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module


@pytest.fixture()
def bound(platform, monkeypatch):
    import backend.services.reply_flow_service as flow_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    # Bind the module every assertion reads through by name, not by luck of the
    # identity loop above (see tests/test_ai_instructions.py for why).
    monkeypatch.setattr(flow_module, "database_manager", test_manager)

    return test_manager


def _make_active(company_id, *, nodes, edges, channels=None, departments=None):
    """Create an active flow with a given graph, returning its full row."""
    from backend.services.reply_flow_service import reply_flow_service

    flow = reply_flow_service.create(company_id=company_id, name="Test Flow")
    return reply_flow_service.update(
        company_id=company_id,
        flow_id=flow["id"],
        name="Test Flow",
        status="active",
        channels=channels or [],
        departments=departments or [],
        reply_modes=[],
        trigger_type="new_conversation",
        trigger_config={},
        nodes=nodes,
        edges=edges,
    )


def _node(node_id, node_type, config=None):
    return {
        "id": node_id,
        "type": "step",
        "position": {"x": 0, "y": 0},
        "data": {"nodeType": node_type, "label": "", "config": config or {}},
    }


def _edge(source, target):
    return {"id": f"{source}-{target}", "source": source, "target": target}


# ----------------------------------------------------------------- the service

def test_create_list_and_isolation(bound, alpha, beta):
    from backend.services.reply_flow_service import reply_flow_service

    reply_flow_service.create(company_id=alpha["id"], name="Alpha flow")

    assert [f["name"] for f in reply_flow_service.list(alpha["id"])] == ["Alpha flow"]
    assert reply_flow_service.list(beta["id"]) == []


def test_duplicate_is_always_a_draft(bound, alpha):
    from backend.services.reply_flow_service import reply_flow_service

    active = _make_active(
        alpha["id"], nodes=[_node("a", "end")], edges=[]
    )
    assert active["status"] == "active"

    copy = reply_flow_service.duplicate(company_id=alpha["id"], flow_id=active["id"])
    assert copy["status"] == "draft"
    assert copy["name"].endswith("(copy)")


def test_only_active_flows_are_matched(bound, alpha):
    from backend.services.reply_flow_service import reply_flow_service

    draft = reply_flow_service.create(company_id=alpha["id"], name="Draft")
    assert reply_flow_service.active_flow_for(alpha["id"]) is None

    reply_flow_service.update(
        company_id=alpha["id"], flow_id=draft["id"], name="Draft", status="active",
        channels=[], departments=[], reply_modes=[], trigger_type="new_conversation",
        trigger_config={}, nodes=[_node("a", "end")], edges=[],
    )
    assert reply_flow_service.active_flow_for(alpha["id"]) is not None


def test_the_most_specific_active_flow_wins(bound, alpha):
    from backend.services.reply_flow_service import reply_flow_service

    catch_all = reply_flow_service.create(company_id=alpha["id"], name="Catch all")
    reply_flow_service.update(
        company_id=alpha["id"], flow_id=catch_all["id"], name="Catch all",
        status="active", channels=[], departments=[], reply_modes=[],
        trigger_type="new_conversation", trigger_config={},
        nodes=[_node("a", "end")], edges=[],
    )
    specific = reply_flow_service.create(company_id=alpha["id"], name="WhatsApp only")
    reply_flow_service.update(
        company_id=alpha["id"], flow_id=specific["id"], name="WhatsApp only",
        status="active", channels=["whatsapp"], departments=[], reply_modes=[],
        trigger_type="new_conversation", trigger_config={},
        nodes=[_node("a", "end")], edges=[],
    )

    matched = reply_flow_service.active_flow_for(alpha["id"], channel="whatsapp")
    assert matched["name"] == "WhatsApp only"

    other = reply_flow_service.active_flow_for(alpha["id"], channel="telegram")
    assert other["name"] == "Catch all"


# ------------------------------------------------------------------ the engine

def test_no_active_flow_leaves_the_default_path_alone(bound, alpha):
    """The safety the live reply path rests on: nothing to run, nothing to do."""
    from core.reply_flow_engine import reply_flow_engine

    session: dict = {}
    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hello", user_session=session, language="en",
    )
    assert result is None
    assert "reply_flow" not in session


def test_a_scripted_flow_runs_and_captures_an_answer(bound, alpha):
    from core.response import Response
    from core.reply_flow_engine import reply_flow_engine

    _make_active(
        alpha["id"],
        nodes=[
            _node("g", "greeting", {"text": "Hi there!"}),
            _node("q", "ask_question", {"question": "What's your name?", "save_as": "name"}),
            _node("c", "canned_reply", {"text": "Thanks, {{name}}."}),
            _node("e", "end"),
        ],
        edges=[_edge("g", "q"), _edge("q", "c"), _edge("c", "e")],
    )

    session: dict = {}
    # First turn: greet, then ask, then wait for the answer.
    first = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hey", user_session=session, language="en",
    )
    assert isinstance(first, Response)
    assert "Hi there!" in first.text
    assert "What's your name?" in first.text
    assert session.get("reply_flow", {}).get("node_id") == "q"

    # Second turn: the message is the saved answer; the flow finishes.
    second = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="Sara", user_session=session, language="en",
    )
    assert isinstance(second, Response)
    assert "Thanks, Sara." in second.text
    # The run ended, so it does not restart on the next sentence.
    assert "reply_flow" not in session


def test_ask_question_offers_buttons_when_configured(bound, alpha):
    from core.reply_flow_engine import reply_flow_engine

    _make_active(
        alpha["id"],
        nodes=[
            _node("q", "ask_question", {
                "question": "Pick one",
                "save_as": "choice",
                "mode": "buttons",
                "options": [{"label": "Sales", "value": "sales"}, {"label": "Support"}],
            }),
            _node("e", "end"),
        ],
        edges=[_edge("q", "e")],
    )

    session: dict = {}
    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hi", user_session=session, language="en",
    )
    assert result.buttons == ["Sales", "Support"]


def test_condition_branches_on_a_saved_answer(bound, alpha):
    from core.reply_flow_engine import reply_flow_engine

    _make_active(
        alpha["id"],
        nodes=[
            _node("q", "ask_question", {"question": "VIP?", "save_as": "tier"}),
            _node("cond", "condition", {"variable": "tier", "operator": "equals", "value": "vip"}),
            _node("yes", "canned_reply", {"text": "Welcome, VIP!"}),
            _node("no", "canned_reply", {"text": "Welcome!"}),
            _node("e", "end"),
        ],
        # First outgoing edge = true branch, second = false branch.
        edges=[
            _edge("q", "cond"),
            _edge("cond", "yes"),
            _edge("cond", "no"),
            _edge("yes", "e"),
            _edge("no", "e"),
        ],
    )

    session: dict = {}
    reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="start", user_session=session, language="en",
    )
    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="vip", user_session=session, language="en",
    )
    assert "Welcome, VIP!" in result.text
    assert "Welcome!" not in result.text


def test_human_handoff_flags_a_person_and_ends(bound, alpha):
    from core.reply_flow_engine import reply_flow_engine

    _make_active(
        alpha["id"],
        nodes=[_node("h", "human_handoff", {"note": "Please help this customer."})],
        edges=[],
    )

    session: dict = {}
    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="I need a person", user_session=session, language="en",
    )
    assert "Please help this customer." in result.text
    assert result.buttons == ["Contact support"]
    assert session.get("last_ai_needs_human") is True
    assert "reply_flow" not in session


def test_an_ai_step_defers_to_the_ai_with_its_instructions(bound, alpha):
    from core.reply_flow_engine import reply_flow_engine, FlowDefer

    _make_active(
        alpha["id"],
        nodes=[_node("ai", "ai_knowledge_only", {"instructions": "Only use the KB."})],
        edges=[],
    )

    session: dict = {}
    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="a question", user_session=session, language="en",
    )
    assert isinstance(result, FlowDefer)
    assert result.instructions == "Only use the KB."


def test_a_flow_never_runs_for_another_company(bound, alpha, beta):
    from core.reply_flow_engine import reply_flow_engine

    _make_active(
        alpha["id"],
        nodes=[_node("g", "greeting", {"text": "Alpha only"})],
        edges=[],
    )

    session: dict = {}
    result = reply_flow_engine.handle(
        company_id=beta["id"], channel="messenger", department=None,
        message="hi", user_session=session, language="en",
    )
    assert result is None


# ------------------------------------------------------- timeout_followup


class _Req:
    """Stands in for `core.request.Request`: `handle()` only reads `user_id`
    off it, to resolve who a `timeout_followup` wait is for."""

    def __init__(self, user_id):
        self.user_id = user_id


@pytest.fixture()
def resumable(platform, monkeypatch):
    """Same shape as `bound`, with the extra modules `timeout_followup`
    touches imported first: `reply_flow_resume_service`, `work_index_service`
    and `message_service` are only reached through a local import inside
    `core.reply_flow_engine`, which a rebind loop run before they exist in
    `sys.modules` cannot see into. Importing them up front is what makes the
    test database the one they all actually open."""
    import backend.services.message_service  # noqa: F401
    import backend.services.reply_flow_resume_service  # noqa: F401
    import backend.services.reply_flow_service  # noqa: F401
    import backend.services.work_index_service  # noqa: F401
    import core.reply_flow_engine  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    return test_manager


def test_reaching_timeout_followup_schedules_a_wait_and_pauses(resumable, alpha):
    """No scheduler yet used to mean this step was a silent pass-through --
    the one documented gap in the module's own docstring. It must now pause
    the turn and leave something durable behind for the sweep to find."""
    import backend.services.reply_flow_resume_service as resume_module
    from backend.services.work_index_service import (
        KIND_REPLY_FLOW_RESUME,
        work_index_service,
    )
    from core.reply_flow_engine import reply_flow_engine

    flow = _make_active(
        alpha["id"],
        nodes=[
            _node("g", "greeting", {"text": "Hang tight."}),
            _node("t", "timeout_followup", {
                "wait_minutes": 30, "text": "Still there?",
            }),
            _node("c", "canned_reply", {"text": "Following up!"}),
            _node("e", "end"),
        ],
        edges=[_edge("g", "t"), _edge("t", "c"), _edge("c", "e")],
    )

    session: dict = {}
    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hi", user_session=session, language="en",
        request=_Req("cust-1"),
    )

    assert result is not None
    assert "Hang tight." in result.text
    assert session["reply_flow"]["node_id"] == "t"

    pending = resume_module.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    )
    assert pending is not None
    assert pending["flow_id"] == flow["id"]
    assert pending["node_id"] == "t"

    due_companies = work_index_service.due_companies(
        KIND_REPLY_FLOW_RESUME, now=pending["fire_at"]
    )
    assert alpha["id"] in due_companies


def test_a_reply_before_the_wait_elapses_cancels_it(resumable, alpha):
    """The customer did not go quiet after all -- the wait must not also
    fire later and send a redundant follow-up."""
    import backend.services.reply_flow_resume_service as resume_module
    from core.reply_flow_engine import reply_flow_engine

    _make_active(
        alpha["id"],
        nodes=[
            _node("t", "timeout_followup", {"wait_minutes": 30}),
            _node("c", "canned_reply", {"text": "Welcome back!"}),
            _node("e", "end"),
        ],
        edges=[_edge("t", "c"), _edge("c", "e")],
    )

    session: dict = {}
    reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hi", user_session=session, language="en",
        request=_Req("cust-1"),
    )
    assert resume_module.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is not None

    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="still here", user_session=session, language="en",
        request=_Req("cust-1"),
    )

    assert "Welcome back!" in result.text
    assert resume_module.get(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    ) is None


def test_an_unanswered_wait_fires_and_sends_the_follow_up(resumable, alpha, monkeypatch):
    """The sweep's own path: `fire_due` resumes the flow with no customer
    message to answer, sends what comes next through the normal dispatcher,
    and records it on the conversation's Timeline the way every other
    automated reply is."""
    import channels.sender as sender_module
    from backend.services import reply_flow_resume_service
    from core.reply_flow_engine import reply_flow_engine

    flow = _make_active(
        alpha["id"],
        nodes=[
            _node("t", "timeout_followup", {"wait_minutes": 30}),
            _node("c", "canned_reply", {"text": "Still there? Let us know!"}),
            _node("e", "end"),
        ],
        edges=[_edge("t", "c"), _edge("c", "e")],
    )

    session: dict = {}
    reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hi", user_session=session, language="en",
        request=_Req("cust-1"),
    )

    sent = {}

    def fake_send_text(*, channel, recipient_id, company_id, text, buttons=None):
        sent["channel"] = channel
        sent["recipient_id"] = recipient_id
        sent["text"] = text
        return {"ok": True}

    monkeypatch.setattr(sender_module, "send_text", fake_send_text)

    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fired = reply_flow_resume_service.fire_due(alpha["id"], now=future)

    assert fired == 1
    assert sent == {
        "channel": "messenger",
        "recipient_id": "cust-1",
        "text": "Still there? Let us know!",
    }
    # Fires once, like a reminder, not a recurring timer.
    assert reply_flow_resume_service.due(company_id=alpha["id"]) == []

    from database.manager import database_manager

    with database_manager.tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE channel = ? AND external_user_id = ?"
            " ORDER BY id DESC LIMIT 1",
            ("messenger", "cust-1"),
        ).fetchone()

    assert row["direction"] == "out"
    assert row["body"] == "Still there? Let us know!"
    assert row["sender_type"] == "ai"
    assert row["source"] == "reply_flow_timeout_followup"


def test_the_nodes_own_follow_up_text_is_sent_only_on_a_real_timeout(
    resumable, alpha, monkeypatch
):
    """`nodeFieldsConfig.js` labels this node's `text` field "Follow-up
    message" -- it is the "are you still there?" itself, and must only ever
    be said to a customer who in fact went quiet, never to one who just
    replied."""
    import channels.sender as sender_module
    from backend.services import reply_flow_resume_service
    from core.reply_flow_engine import reply_flow_engine

    _make_active(
        alpha["id"],
        nodes=[
            _node("t", "timeout_followup", {
                "wait_minutes": 30, "text": "Just checking in, are you there?",
            }),
            _node("e", "end"),
        ],
        edges=[_edge("t", "e")],
    )

    session: dict = {}
    reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hi", user_session=session, language="en",
        request=_Req("cust-1"),
    )

    # The customer replies before the clock runs out: no follow-up text.
    def fail_if_called(*args, **kwargs):
        raise AssertionError("a reply that beat the clock must send nothing")

    monkeypatch.setattr(sender_module, "send_text", fail_if_called)

    result = reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="still here", user_session=session, language="en",
        request=_Req("cust-1"),
    )
    assert result is None

    # A second run, this time left unanswered until the sweep fires it.
    session2: dict = {}
    reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hi", user_session=session2, language="en",
        request=_Req("cust-2"),
    )

    sent = {}

    def fake_send_text(*, channel, recipient_id, company_id, text, buttons=None):
        sent["text"] = text
        return {"ok": True}

    monkeypatch.setattr(sender_module, "send_text", fake_send_text)

    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fired = reply_flow_resume_service.fire_due(alpha["id"], now=future)

    assert fired == 1
    assert sent["text"] == "Just checking in, are you there?"


def test_a_wait_for_an_archived_flow_sends_nothing(resumable, alpha, monkeypatch):
    import channels.sender as sender_module
    from backend.services import reply_flow_resume_service
    from backend.services.reply_flow_service import reply_flow_service
    from core.reply_flow_engine import reply_flow_engine

    flow = _make_active(
        alpha["id"],
        nodes=[
            _node("t", "timeout_followup", {"wait_minutes": 30}),
            _node("c", "canned_reply", {"text": "Should never send."}),
        ],
        edges=[_edge("t", "c")],
    )

    session: dict = {}
    reply_flow_engine.handle(
        company_id=alpha["id"], channel="messenger", department=None,
        message="hi", user_session=session, language="en",
        request=_Req("cust-1"),
    )

    reply_flow_service.update(
        company_id=alpha["id"], flow_id=flow["id"], name="Test Flow",
        status="archived", channels=[], departments=[], reply_modes=[],
        trigger_type="new_conversation", trigger_config={},
        nodes=flow["nodes"], edges=flow["edges"],
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("an archived flow must never send a message")

    monkeypatch.setattr(sender_module, "send_text", fail_if_called)

    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fired = reply_flow_resume_service.fire_due(alpha["id"], now=future)

    assert fired == 1
    assert reply_flow_resume_service.due(company_id=alpha["id"]) == []
