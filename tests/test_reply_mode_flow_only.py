"""
Real test for the inert "reply_mode" bug found while auditing the Reply
Flow Builder settings: core/response_policy.py defines DEFAULT_POLICY with
a "reply_mode" key and config/response_policy.json sets per-channel values
("grounded_ai" for most channels, "flow_only" for telegram), and
get_channel_policy(...) correctly merges that value in — but nothing that
actually decides how a reply gets produced ever read policy["reply_mode"].
compose_reply() only branched on welcome_enabled/welcome_mode and
show_buttons, so every channel behaved identically (AI/knowledge-driven
replies) no matter what an operator configured reply_mode to be in the
Reply Flow Builder.

This test proves that core/engine.py's Engine.should_ai_take_priority()
(the existing branch point that already chooses between the AI/knowledge
reply pipeline and the scripted flow/menu state machine) now consults
reply_mode: "flow_only" must force the turn through the flow/menu state
machine only, skipping AI/knowledge reply generation entirely, while the
default ("grounded_ai" / "knowledge_then_ai") keeps producing an
AI-pipeline reply exactly as before.

It also covers the message == "start" special case in Engine.handle_start,
which is a second, independent AI-vs-flow decision point in engine.py.
handle_start must also honor reply_mode="flow_only" even when
automation_policy's ai_mode is independently set to "auto_reply" (a
config combination an operator can legitimately create), instead of only
looking at automation_policy the way should_ai_take_priority() used to.

Run with: python3 -m pytest tests/test_reply_mode_flow_only.py -v
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import engine  # noqa: E402
from core.request import Request  # noqa: E402
from core.session import session  # noqa: E402
from core.response_policy import response_policy  # noqa: E402
from core.automation_policy import automation_policy  # noqa: E402
from database.database import db  # noqa: E402


CHANNEL = "website_chat"
MESSAGE = "Do you have iPhone 15 in stock?"


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test
    (engine.handle() calls db.create_tables() on every request)."""
    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    yield

    db.db_path = original_path

    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _write_response_policy(monkeypatch, tmp_path, channel, reply_mode):
    """Redirect response_policy.POLICY_FILE to a throwaway file under
    tmp_path instead of mutating the real checked-in
    config/response_policy.json, so a crash/interrupt mid-test can never
    corrupt the production policy config on disk."""
    original_text = response_policy.POLICY_FILE.read_text(encoding="utf-8")
    policy = json.loads(original_text)
    policy["channels"].setdefault(channel, {})["reply_mode"] = reply_mode

    policy_file = tmp_path / "response_policy.json"
    policy_file.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    monkeypatch.setattr(response_policy, "POLICY_FILE", policy_file)


def _write_automation_policy(monkeypatch, tmp_path, channel, ai_mode):
    """Redirect automation_policy.POLICY_FILE to a throwaway file under
    tmp_path instead of mutating the real checked-in
    config/automation_policy.json."""
    original_text = automation_policy.POLICY_FILE.read_text(encoding="utf-8")
    policy = json.loads(original_text)
    channel_policy = policy["channels"].setdefault(channel, {})
    channel_policy["ai_mode"] = ai_mode
    channel_policy.setdefault("bot_enabled", True)
    channel_policy["ai_enabled"] = True

    policy_file = tmp_path / "automation_policy.json"
    policy_file.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    monkeypatch.setattr(automation_policy, "POLICY_FILE", policy_file)


@pytest.fixture()
def default_reply_mode_policy(monkeypatch, tmp_path):
    """Force website_chat's reply_mode to the default AI-pipeline value
    for the duration of the test, without touching the real config file."""
    _write_response_policy(monkeypatch, tmp_path, "website_chat", "grounded_ai")
    yield


@pytest.fixture()
def flow_only_reply_mode_policy(monkeypatch, tmp_path):
    """Force website_chat's reply_mode to "flow_only" for the duration of
    the test, without touching the real config file."""
    _write_response_policy(monkeypatch, tmp_path, "website_chat", "flow_only")
    yield


def _send(user_id):
    request = Request(
        channel=CHANNEL,
        user_id=user_id,
        message=MESSAGE,
    )

    return engine.handle(request)


def test_default_reply_mode_uses_ai_pipeline(fresh_db, default_reply_mode_policy):
    """Baseline / regression guard: the default reply_mode ("grounded_ai")
    must keep going through the AI/knowledge reply pipeline exactly like
    before this fix (no OPENAI_API_KEY in this environment -> the AI
    pipeline safely falls back to its own "not enough confirmed
    information" reply, never to the flow/menu state machine's text)."""
    session.sessions.pop("reply_mode_test_default", None)

    response = _send("reply_mode_test_default")

    assert "T-ZONE IPTV" not in response.text
    assert "enough confirmed information" in response.text


def test_flow_only_reply_mode_skips_ai_pipeline(fresh_db, flow_only_reply_mode_policy):
    """The actual bug fix: reply_mode="flow_only" must skip AI/knowledge
    reply generation entirely and produce a reply from the existing
    flow/menu state machine instead."""
    session.sessions.pop("reply_mode_test_flow_only", None)

    response = _send("reply_mode_test_flow_only")

    assert "T-ZONE IPTV" in response.text
    assert "enough confirmed information" not in response.text


def test_should_ai_take_priority_true_for_default_reply_mode(default_reply_mode_policy):
    """Direct unit check of the branch point itself, independent of the
    exact wording of any fallback text."""
    request = Request(channel=CHANNEL, user_id="reply_mode_unit_test_default", message=MESSAGE)

    assert engine.should_ai_take_priority(request) is True


def test_should_ai_take_priority_false_for_flow_only_reply_mode(flow_only_reply_mode_policy):
    """Direct unit check of the branch point itself, independent of the
    exact wording of any fallback text."""
    request = Request(channel=CHANNEL, user_id="reply_mode_unit_test_flow_only", message=MESSAGE)

    assert engine.should_ai_take_priority(request) is False


def test_handle_start_flow_only_reply_mode_overrides_independent_ai_mode(
    fresh_db, monkeypatch, tmp_path
):
    """The second AI-vs-flow decision point: message == "start" is routed
    through Engine.handle_start(), which used to gate purely on
    automation_policy.should_auto_reply_with_ai() and ignore reply_mode
    entirely. This reproduces the exact scenario from review: telegram's
    response_policy.reply_mode is "flow_only" while automation_policy's
    ai_mode is independently "auto_reply" (a legitimate, separate config)
    -> /start must still land in the scripted telegram_iptv_start flow
    state, not the AI/business-module main menu."""
    _write_response_policy(monkeypatch, tmp_path, "telegram", "flow_only")
    _write_automation_policy(monkeypatch, tmp_path, "telegram", "auto_reply")

    session.sessions.pop("reply_mode_test_start_flow_only", None)

    request = Request(
        channel="telegram",
        user_id="reply_mode_test_start_flow_only",
        message="start",
    )

    response = engine.handle(request)

    assert "T-ZONE IPTV" in response.text
    assert "Welcome to T-ZONE" not in response.text
    assert "أهلاً وسهلاً بك في T-ZONE" not in response.text


def test_handle_start_default_reply_mode_uses_ai_main_menu(
    fresh_db, monkeypatch, tmp_path
):
    """Regression guard: when reply_mode is not "flow_only" and
    automation_policy allows auto_reply, "start" must still produce the
    AI/business-module main menu exactly as before this fix."""
    _write_response_policy(monkeypatch, tmp_path, "website_chat", "grounded_ai")
    _write_automation_policy(monkeypatch, tmp_path, "website_chat", "auto_reply")

    session.sessions.pop("reply_mode_test_start_default", None)

    request = Request(
        channel="website_chat",
        user_id="reply_mode_test_start_default",
        message="start",
    )

    response = engine.handle(request)

    assert "T-ZONE IPTV" not in response.text
