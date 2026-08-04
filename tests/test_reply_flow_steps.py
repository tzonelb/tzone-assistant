"""
Real tests for the "reply_flow" company setting found dead while auditing
the Reply Flow Builder: backend/services/company_settings_service.py
defines a "reply_flow" section (default steps: welcome,
language_detection, intent_detection, knowledge_lookup, answer,
escalation), fully readable/writable via the generic company-settings
API, but nothing in core/ ever read it -- every company's AI pipeline
ran the exact same fixed sequence of calls inside
core/engine.py's Engine.handle_ai(), identical to the already-fixed
"reply_mode" dead-setting bug (see tests/test_reply_mode_flow_only.py).

This file proves that handle_ai() now actually consults
company_settings_service.get_section(company_id, "reply_flow")["values"]
["steps"]:

  (a) regression guard -- a company with the untouched default steps
      list behaves identically to the pre-fix pipeline (AI/knowledge
      pipeline still runs, safe-fallback reply still includes the
      escalation button, since no OPENAI_API_KEY is configured in this
      environment).
  (b) removing "escalation" from a company's steps suppresses the
      "Contact support" handoff button even though the AI/safe-fallback
      result still sets needs_human=True internally.
  (c) removing "knowledge_lookup" from a company's steps skips the
      knowledge-matching call entirely (proved by monkeypatching
      ai_knowledge_matcher.match to raise if invoked, and knowledge_manager
      .list_for_ai to raise if invoked, then asserting the request still
      completes without error).

Run with: python3 -m pytest tests/test_reply_flow_steps.py -v
"""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHANNEL = "website_chat"
MESSAGE = "Do you have iPhone 15 in stock?"
COMPANY_ID = 1


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Mirrors tests/test_meta_company_resolution.py's fresh_db fixture:
    company_settings_service is a process-wide singleton whose __init__
    already ran ensure_schema() against whatever db.db_path was at import
    time, not necessarily this test's temp file, so its schema setup is
    re-run explicitly once db.db_path points at the temp file. This never
    touches the real database file or any checked-in config.
    """
    from pathlib import Path
    from database.database import db
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    company_settings_service.ensure_schema()

    yield db

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


def _set_reply_flow_steps(steps):
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        company_id=COMPANY_ID,
        section="reply_flow",
        values={"steps": steps},
        actor_user_id=None,
    )


def _send(user_id):
    from core.engine import engine
    from core.request import Request

    request = Request(
        channel=CHANNEL,
        user_id=user_id,
        message=MESSAGE,
        company_id=COMPANY_ID,
    )

    return engine.handle(request)


def _reset_session(user_id):
    from core.session import session

    session.sessions.pop(user_id, None)


def test_default_steps_matches_pre_fix_behavior(fresh_db):
    """Regression guard: a company that never touched the reply_flow
    setting must behave exactly like before this change -- AI/knowledge
    pipeline runs, and since this environment has no OPENAI_API_KEY the
    safe fallback reply is produced, WITH its escalation button (the
    default steps list includes "escalation")."""
    _reset_session("reply_flow_default_user")

    response = _send("reply_flow_default_user")

    assert "enough confirmed information" in response.text
    assert (
        "Contact support" in response.buttons
        or "التواصل مع الدعم" in response.buttons
    )


def test_escalation_removed_suppresses_button_even_when_needs_human(fresh_db):
    """The real behavior change: removing "escalation" from a company's
    configured steps must suppress the "Contact support" handoff button
    even though the underlying result (the safe fallback, in this
    environment) still sets needs_human=True internally."""
    _set_reply_flow_steps(
        ["welcome", "language_detection", "intent_detection", "knowledge_lookup", "answer"]
    )

    _reset_session("reply_flow_no_escalation_user")

    response = _send("reply_flow_no_escalation_user")

    assert "enough confirmed information" in response.text
    assert "Contact support" not in response.buttons
    assert "التواصل مع الدعم" not in response.buttons


def test_escalation_present_keeps_button_for_comparison(fresh_db):
    """Direct counterpart to the previous test using the exact same
    steps list minus only "escalation" being added back, to make sure
    the difference is really attributable to the "escalation" entry and
    not some other side effect of calling update_section()."""
    _set_reply_flow_steps(
        [
            "welcome",
            "language_detection",
            "intent_detection",
            "knowledge_lookup",
            "answer",
            "escalation",
        ]
    )

    _reset_session("reply_flow_with_escalation_user")

    response = _send("reply_flow_with_escalation_user")

    assert "enough confirmed information" in response.text
    assert (
        "Contact support" in response.buttons
        or "التواصل مع الدعم" in response.buttons
    )


def test_knowledge_lookup_removed_skips_matcher_entirely(fresh_db, monkeypatch):
    """Removing "knowledge_lookup" from steps must skip the knowledge
    matching call entirely -- proved by making both the matcher and the
    knowledge item loader raise if they are invoked, then asserting the
    request still completes normally."""
    from core.ai_knowledge_matcher import ai_knowledge_matcher
    from core.knowledge_manager import knowledge_manager

    def _boom_match(*args, **kwargs):
        raise AssertionError(
            "ai_knowledge_matcher.match must not be called when "
            "'knowledge_lookup' is excluded from reply_flow steps"
        )

    def _boom_list_for_ai(*args, **kwargs):
        raise AssertionError(
            "knowledge_manager.list_for_ai must not be called when "
            "'knowledge_lookup' is excluded from reply_flow steps"
        )

    monkeypatch.setattr(ai_knowledge_matcher, "match", _boom_match)
    monkeypatch.setattr(knowledge_manager, "list_for_ai", _boom_list_for_ai)

    _set_reply_flow_steps(
        ["welcome", "language_detection", "intent_detection", "answer", "escalation"]
    )

    _reset_session("reply_flow_no_knowledge_user")

    response = _send("reply_flow_no_knowledge_user")

    assert "enough confirmed information" in response.text


def test_knowledge_lookup_present_calls_matcher(fresh_db, monkeypatch):
    """Counterpart to the previous test: with "knowledge_lookup" left in
    steps (the default), the matcher IS invoked -- confirms the previous
    test's silence is really due to the gate, not some unrelated reason
    the matcher never gets called (e.g. an early return elsewhere)."""
    from core.ai_knowledge_matcher import ai_knowledge_matcher

    calls = []
    original_match = ai_knowledge_matcher.match

    def _tracking_match(*args, **kwargs):
        calls.append(True)
        return original_match(*args, **kwargs)

    monkeypatch.setattr(ai_knowledge_matcher, "match", _tracking_match)

    _set_reply_flow_steps(
        [
            "welcome",
            "language_detection",
            "intent_detection",
            "knowledge_lookup",
            "answer",
            "escalation",
        ]
    )

    _reset_session("reply_flow_with_knowledge_user")

    _send("reply_flow_with_knowledge_user")

    assert calls, "ai_knowledge_matcher.match should have been called"


def test_explicit_empty_steps_list_runs_zero_steps(fresh_db):
    """CONFIRMED BUG regression guard: get_reply_flow_steps() must treat
    an explicitly-saved empty steps list ("steps": []) as "run zero
    steps", not silently substitute the full default sequence -- the two
    are different company intents (never configured vs. deliberately
    disabled the entire pipeline) and were previously indistinguishable
    because `not steps` is True for both a missing key and an empty
    list."""
    from core.engine import engine

    _set_reply_flow_steps([])

    assert engine.get_reply_flow_steps(COMPANY_ID) == []


def test_answer_removed_still_produces_safe_reply(fresh_db):
    """If "answer" is excluded from steps, no AI call is made, but the
    engine must still produce a reply via the existing safe-fallback
    path rather than crashing or returning nothing."""
    _set_reply_flow_steps(
        ["welcome", "language_detection", "intent_detection", "knowledge_lookup", "escalation"]
    )

    _reset_session("reply_flow_no_answer_user")

    response = _send("reply_flow_no_answer_user")

    assert response is not None
    assert response.text
    assert "enough confirmed information" in response.text
