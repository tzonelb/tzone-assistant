"""`customer_no_reply` / `team_no_reply`: starting a Reply Flow on silence.

Two trigger types this platform's own catalogue (`reply_flow_service
.TRIGGER_TYPES`) has named since the feature shipped, and that
`core/reply_flow_engine.py` never matched against -- a company could build
one of these flows, mark it active, and it would simply never run. This file
is the regression test for wiring that up: the threshold itself (a flow
configured for 30 minutes must not fire at 10 and must fire at 31), the
direction that decides which trigger applies, `team_no_reply`'s extra
`needs_human` condition, the one-fire-per-silence dedup, and the respect for
an already-running flow and the flow's own channel scope.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

import database.manager as manager_module


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _node(node_id, node_type, config=None):
    return {
        "id": node_id,
        "type": "step",
        "position": {"x": 0, "y": 0},
        "data": {"nodeType": node_type, "label": "", "config": config or {}},
    }


def _edge(source, target):
    return {"id": f"{source}-{target}", "source": source, "target": target}


@pytest.fixture()
def wired(platform, monkeypatch):
    """Rebinds `database_manager` for every module this feature touches --
    `message_service`, `reply_flow_service`, `reply_flow_silence_service`
    and `core.reply_flow_engine` are each only reached through a local
    import somewhere in the call chain, so (same reasoning as
    `test_reply_flows.py`'s `resumable` fixture) they must already be in
    `sys.modules` before the rebind loop runs, or it cannot see them."""
    import backend.services.message_service  # noqa: F401
    import backend.services.reply_flow_service  # noqa: F401
    import backend.services.reply_flow_silence_service  # noqa: F401
    import core.reply_flow_engine  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    # No test here configures a real connected channel account, so the real
    # sender would refuse every send with "no connected account" -- this
    # feature's own logic is what each test is checking, not the channel
    # layer underneath it.
    import channels.sender as sender_module

    monkeypatch.setattr(
        sender_module, "send_text",
        lambda **kwargs: {"ok": True},
    )

    return test_manager


def _make_flow(company_id, *, trigger_type, trigger_config, nodes, edges, channels=None):
    from backend.services.reply_flow_service import reply_flow_service

    flow = reply_flow_service.create(company_id=company_id, name="Silence flow")
    return reply_flow_service.update(
        company_id=company_id, flow_id=flow["id"], name="Silence flow",
        status="active", channels=channels or [], departments=[], reply_modes=[],
        trigger_type=trigger_type, trigger_config=trigger_config,
        nodes=nodes, edges=edges,
    )


def _seed_conversation(
    company_id, *, channel="messenger", external_user_id="cust-1",
    direction, minutes_ago, needs_human=False,
):
    from backend.services.message_service import message_service

    message_service.save_message(
        company_id=company_id, channel=channel, external_user_id=external_user_id,
        direction=direction, text="hi", sender_type=("ai" if direction == "out" else "customer"),
    )

    from database.manager import database_manager

    with database_manager.tenant(company_id) as conn:
        conn.execute(
            "UPDATE conversations SET last_message_at = ?, needs_human = ? "
            "WHERE company_id = ? AND channel = ? AND external_user_id = ?",
            (_ago(minutes_ago), 1 if needs_human else 0, company_id, channel, external_user_id),
        )
        conn.commit()


# --------------------------------------------------------- customer_no_reply


def test_a_quiet_conversation_past_the_threshold_starts_the_flow(wired, alpha):
    from backend.services import reply_flow_silence_service

    _make_flow(
        alpha["id"], trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        nodes=[_node("g", "greeting", {"text": "Still there?"})],
        edges=[],
    )
    _seed_conversation(alpha["id"], direction="out", minutes_ago=45)

    fired = reply_flow_silence_service.fire_due(alpha["id"])

    assert fired == 1

    from database.manager import database_manager

    with database_manager.tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE channel = 'messenger' "
            "AND external_user_id = 'cust-1' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row["direction"] == "out"
    assert row["body"] == "Still there?"
    assert row["sender_type"] == "ai"


def test_a_conversation_not_yet_past_the_threshold_is_left_alone(wired, alpha):
    from backend.services import reply_flow_silence_service

    _make_flow(
        alpha["id"], trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        nodes=[_node("g", "greeting", {"text": "Still there?"})],
        edges=[],
    )
    _seed_conversation(alpha["id"], direction="out", minutes_ago=10)

    assert reply_flow_silence_service.fire_due(alpha["id"]) == 0


def test_a_conversation_the_customer_already_answered_is_left_alone(wired, alpha):
    """direction='in' means the customer spoke last -- this is a
    `customer_no_reply` flow, not a `team_no_reply` one."""
    from backend.services import reply_flow_silence_service

    _make_flow(
        alpha["id"], trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        nodes=[_node("g", "greeting", {"text": "Still there?"})],
        edges=[],
    )
    _seed_conversation(alpha["id"], direction="in", minutes_ago=45)

    assert reply_flow_silence_service.fire_due(alpha["id"]) == 0


def test_the_same_silence_does_not_fire_a_second_time(wired, alpha):
    from backend.services import reply_flow_silence_service

    _make_flow(
        alpha["id"], trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        nodes=[_node("g", "greeting", {"text": "Still there?"})],
        edges=[],
    )
    _seed_conversation(alpha["id"], direction="out", minutes_ago=45)

    assert reply_flow_silence_service.fire_due(alpha["id"]) == 1
    # Same conversation, same silence -- the sweep runs again a moment
    # later and must not send a second follow-up for it.
    assert reply_flow_silence_service.fire_due(alpha["id"]) == 0


def test_a_new_outbound_message_makes_the_conversation_eligible_again(wired, alpha):
    """The flow's own follow-up is itself an outbound message -- it moves
    `last_message_at` forward, which is what lets a conversation that goes
    quiet a second time be found again, with no separate reset needed."""
    from backend.services import reply_flow_silence_service

    _make_flow(
        alpha["id"], trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        nodes=[_node("g", "greeting", {"text": "Still there?"})],
        edges=[],
    )
    _seed_conversation(alpha["id"], direction="out", minutes_ago=45)
    assert reply_flow_silence_service.fire_due(alpha["id"]) == 1

    # Back-date the flow's own just-sent message by another 45 minutes, as
    # if that much time has since passed with no reply.
    from database.manager import database_manager

    with database_manager.tenant(alpha["id"]) as conn:
        conn.execute(
            "UPDATE conversations SET last_message_at = ? "
            "WHERE company_id = ? AND channel = 'messenger' AND external_user_id = 'cust-1'",
            (_ago(45), alpha["id"]),
        )
        conn.commit()

    assert reply_flow_silence_service.fire_due(alpha["id"]) == 1


def test_channel_scope_is_respected(wired, alpha):
    from backend.services import reply_flow_silence_service

    _make_flow(
        alpha["id"], trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        nodes=[_node("g", "greeting", {"text": "Still there?"})],
        edges=[], channels=["whatsapp"],
    )
    _seed_conversation(alpha["id"], channel="messenger", direction="out", minutes_ago=45)

    assert reply_flow_silence_service.fire_due(alpha["id"]) == 0


def test_a_flow_already_running_is_not_restarted(wired, alpha):
    from backend.services import reply_flow_silence_service
    from core.session import SessionManager, session

    _make_flow(
        alpha["id"], trigger_type="customer_no_reply",
        trigger_config={"minutes_of_silence": 30},
        nodes=[_node("g", "greeting", {"text": "Still there?"})],
        edges=[],
    )
    _seed_conversation(alpha["id"], direction="out", minutes_ago=45)

    key = SessionManager.key("cust-1", "messenger", alpha["id"])
    session.create(key)["reply_flow"] = {"flow_id": 999, "node_id": "x", "variables": {}}

    assert reply_flow_silence_service.fire_due(alpha["id"]) == 0


# -------------------------------------------------------------- team_no_reply


def test_team_no_reply_fires_only_when_a_human_is_needed(wired, alpha):
    from backend.services import reply_flow_silence_service

    _make_flow(
        alpha["id"], trigger_type="team_no_reply",
        trigger_config={"minutes_waiting": 20},
        nodes=[_node("g", "greeting", {"text": "Sorry for the wait!"})],
        edges=[],
    )
    _seed_conversation(
        alpha["id"], direction="in", minutes_ago=30, needs_human=False,
    )
    assert reply_flow_silence_service.fire_due(alpha["id"]) == 0

    _seed_conversation(
        alpha["id"], external_user_id="cust-2", direction="in", minutes_ago=30,
        needs_human=True,
    )
    assert reply_flow_silence_service.fire_due(alpha["id"]) == 1


def test_a_company_with_no_silence_flow_is_a_cheap_no_op(wired, alpha):
    """No flow of either trigger type exists -- the sweep must not touch the
    conversations table at all, let alone send anything."""
    from backend.services import reply_flow_silence_service

    _seed_conversation(alpha["id"], direction="out", minutes_ago=999)

    assert reply_flow_silence_service.fire_due(alpha["id"]) == 0
