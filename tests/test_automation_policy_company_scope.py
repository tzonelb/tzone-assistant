"""Real tests for the "single static automation_policy.json shared by every
company" architectural bug found while auditing the live AI engine.

Before this change, core/automation_policy.py's get_channel_policy() /
is_bot_enabled() / should_auto_reply_with_ai() / get_ai_mode() read only
config/automation_policy.json, keyed solely by channel. That file is a
single static config shared by every company on the platform. So when an
admin for Company A changed AI Behavior through Company Settings
(PUT /api/company-settings/ai_behavior, which already writes to the
company-scoped company_settings DB table), it had ZERO effect on the actual
bot for that channel -- every company shared one global bot brain, because
core/engine.py's should_ai_take_priority() (the first gate deciding whether
the bot auto-replies at all) only ever consulted the static file.

This file proves the fix in core/automation_policy.py:

  (a) regression guard -- a company with nothing configured in the DB
      (company_settings has no "ai_behavior" row, or the row only holds
      company_settings_service's own untouched defaults) gets EXACTLY the
      same should_auto_reply_with_ai / get_ai_mode / is_bot_enabled results
      as calling the same functions with company_id=None (i.e. as before
      company-scoping existed at all) -- proving the static file's current
      per-channel values remain the default when a company hasn't
      configured anything.

  (b) a company that HAS configured ai_behavior in the DB (via
      company_settings_service.update_section, the same call path
      PUT /api/company-settings/ai_behavior uses) gets its own distinct,
      company-scoped behavior -- different from another company that has
      not configured anything, and without leaking between companies.

  (c) an unrecognized ai_behavior.mode value (company_settings_service's own
      DEFAULT_SETTINGS ships "mode": "ai_first", which is not one of
      AutomationPolicy.AI_MODES) is never applied as an ai_mode override --
      it is ignored in favor of the per-channel file default. This is what
      keeps a company that has *a* company_settings row, but never actually
      touched "mode", byte-for-byte identical to the static file default.

Run with: python3 -m pytest tests/test_automation_policy_company_scope.py -v
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.automation_policy import automation_policy  # noqa: E402
from core.engine import engine  # noqa: E402
from core.request import Request  # noqa: E402
from core.session import session  # noqa: E402


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Mirrors tests/test_meta_company_resolution.py's fresh_db fixture:
    every service's schema-init must run only after db.db_path is pointed
    at the temp file, since backend.services.company_settings_service is a
    process-wide singleton whose __init__ already ran ensure_schema()
    against whatever db.db_path was at import time.
    """
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


def _write_automation_policy_file(monkeypatch, tmp_path, channel, **channel_overrides):
    """Redirect automation_policy.POLICY_FILE to a throwaway file under
    tmp_path instead of mutating the real checked-in
    config/automation_policy.json, so a crash/interrupt mid-test can never
    corrupt the production policy config on disk. Mirrors the pattern in
    tests/test_reply_mode_flow_only.py's _write_automation_policy."""
    original_text = automation_policy.POLICY_FILE.read_text(encoding="utf-8")
    policy = json.loads(original_text)
    channel_policy = policy["channels"].setdefault(channel, {})
    channel_policy.update(channel_overrides)

    policy_file = tmp_path / "automation_policy.json"
    policy_file.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    monkeypatch.setattr(automation_policy, "POLICY_FILE", policy_file)


def _insert_extra_company(db, name: str) -> int:
    """Insert a second active company alongside the seeded default (id=1).

    db.create_tables() always seeds workspace id=1 and company id=1
    ("T-ZONE", status active) via _seed_platform_defaults.
    """
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO companies (workspace_id, name, slug, status)
            VALUES (1, ?, ?, 'active')
            """,
            (name, name.lower().replace(" ", "-")),
        )
        company_id = cursor.lastrowid
        conn.commit()

    return company_id


# --- (a) regression guard: unconfigured company == pre-existing behavior ---


def test_unconfigured_company_matches_file_default_website_chat(fresh_db):
    """website_chat is ai_enabled/auto_reply in config/automation_policy.json.
    A company with no company_settings row must get that exact result,
    identical to calling with company_id=None (today's behavior)."""
    baseline = automation_policy.should_auto_reply_with_ai("website_chat")
    scoped = automation_policy.should_auto_reply_with_ai("website_chat", 1)

    assert baseline is True
    assert scoped == baseline


def test_unconfigured_company_matches_file_default_telegram(fresh_db):
    """telegram is ai_enabled=False / ai_mode=flow_only in the static file.
    An unconfigured company must stay disabled for AI auto-reply on
    telegram exactly as before -- this is the critical regression guard:
    company-scoping must never *widen* a channel that was file-disabled."""
    baseline = automation_policy.should_auto_reply_with_ai("telegram")
    scoped = automation_policy.should_auto_reply_with_ai("telegram", 1)

    assert baseline is False
    assert scoped == baseline

    assert automation_policy.get_ai_mode("telegram", 1) == "flow_only"
    assert automation_policy.is_bot_enabled("telegram", 1) is True


def test_unconfigured_company_id_none_is_untouched(fresh_db):
    """Callers that don't have a company_id yet (company_id=None, the
    parameter default) must be completely unaffected by this change."""
    for channel in ["website_chat", "telegram", "whatsapp", "messenger"]:
        assert automation_policy.get_channel_policy(channel, None) == (
            automation_policy.get_channel_policy(channel)
        )


def test_file_level_kill_switch_is_not_resurrected_by_unconfigured_company(
    fresh_db, monkeypatch, tmp_path
):
    """CONFIRMED BUG regression guard: company_settings_service.get_section()
    always returns a DEFAULT_SETTINGS-merged dict, so an UNCONFIGURED
    company's default "enabled": True must never override an ops-level
    file "this channel is administratively disabled" kill switch back to
    enabled. Before the AND-combination fix, the company-override dict was
    merged into the channel policy with plain dict.update(), so this
    unconfigured company's implicit "enabled": True silently flipped
    bot_enabled back on. The correct semantics: a channel/company is
    enabled only if BOTH the file-level policy AND the company-level
    setting say enabled -- the file is an ops-level ceiling that nothing
    below it can raise."""
    _write_automation_policy_file(
        monkeypatch, tmp_path, "website_chat", bot_enabled=False
    )

    # company_id=1 is the seeded default company with zero ai_behavior
    # configuration in company_settings -- get_section() still returns
    # DEFAULT_SETTINGS["ai_behavior"]["enabled"] == True for it.
    from backend.services.company_settings_service import company_settings_service

    default_values = company_settings_service.get_section(1, "ai_behavior")["values"]
    assert default_values["enabled"] is True  # sanity: unconfigured default

    assert automation_policy.is_bot_enabled("website_chat", 1) is False
    assert automation_policy.is_ai_enabled("website_chat", 1) is False
    assert automation_policy.should_auto_reply_with_ai("website_chat", 1) is False

    # And company_id=None (no company context at all) must see the same
    # file-level kill switch, proving the fix didn't just special-case
    # company_id=1.
    assert automation_policy.is_bot_enabled("website_chat") is False


def test_file_level_kill_switch_still_wins_even_if_company_explicitly_enables(
    fresh_db, monkeypatch, tmp_path
):
    """The file-level gate cannot be raised by a company explicitly setting
    enabled=True either -- AND-combination means the file is a ceiling,
    not just a default."""
    _write_automation_policy_file(
        monkeypatch, tmp_path, "website_chat", bot_enabled=False, ai_enabled=False
    )

    from backend.services.company_settings_service import company_settings_service

    other_company_id = _insert_extra_company(fresh_db, "Explicitly Enabled Co")
    company_settings_service.update_section(
        company_id=other_company_id,
        section="ai_behavior",
        values={"enabled": True},
        actor_user_id=None,
    )

    assert automation_policy.is_bot_enabled("website_chat", other_company_id) is False
    assert automation_policy.is_ai_enabled("website_chat", other_company_id) is False


def test_company_can_disable_itself_even_when_file_allows_it(fresh_db):
    """The other direction of AND-combination, unchanged by this fix: a
    company can still turn itself off even though the file-level default
    allows the channel -- this is today's existing, desired behavior and
    must keep working."""
    from backend.services.company_settings_service import company_settings_service

    other_company_id = _insert_extra_company(fresh_db, "Self Disabled Co")
    company_settings_service.update_section(
        company_id=other_company_id,
        section="ai_behavior",
        values={"enabled": False},
        actor_user_id=None,
    )

    assert automation_policy.is_bot_enabled("website_chat", other_company_id) is False
    assert automation_policy.is_ai_enabled("website_chat", other_company_id) is False


def test_company_row_with_untouched_defaults_still_matches_file(fresh_db):
    """A company_settings row can exist (e.g. created by some other section
    write) while ai_behavior itself was never explicitly configured.
    company_settings_service.get_section always returns DEFAULT_SETTINGS's
    "mode": "ai_first" in that case -- prove that unrecognized value is
    ignored rather than clobbering the per-channel file's ai_mode."""
    from backend.services.company_settings_service import company_settings_service

    default_values = company_settings_service.get_section(1, "ai_behavior")["values"]
    assert default_values["mode"] == "ai_first"  # sanity: still the raw default
    assert "ai_first" not in automation_policy.AI_MODES  # sanity: unrecognized

    assert automation_policy.get_ai_mode("website_chat", 1) == "auto_reply"
    assert automation_policy.should_auto_reply_with_ai("website_chat", 1) is True


# --- (b) configured company gets its own distinct behavior ---


def test_company_with_ai_disabled_differs_from_unconfigured_company(fresh_db):
    """Company 2 explicitly disables AI via the same update_section() call
    path PUT /api/company-settings/ai_behavior uses. Company 1 (default,
    seeded, unconfigured) must be unaffected -- proving the override is
    scoped per-company and does not leak."""
    db = fresh_db
    from backend.services.company_settings_service import company_settings_service

    other_company_id = _insert_extra_company(db, "Other Company")

    company_settings_service.update_section(
        company_id=other_company_id,
        section="ai_behavior",
        values={"enabled": False},
        actor_user_id=None,
    )

    # Company 1: never configured anything -> unchanged file-default behavior.
    assert automation_policy.should_auto_reply_with_ai("website_chat", 1) is True

    # Company 2: explicitly disabled -> AI auto-reply off, on every channel,
    # even one the static file itself enables by default.
    assert automation_policy.should_auto_reply_with_ai(
        "website_chat", other_company_id
    ) is False
    assert automation_policy.is_ai_enabled("website_chat", other_company_id) is False


def test_company_with_recognized_mode_override_differs_from_default(fresh_db):
    """Company 2 sets ai_behavior.mode to a recognized AutomationPolicy value
    ("flow_only") that differs from website_chat's file default
    ("auto_reply"). Company 1 must keep the file default."""
    db = fresh_db
    from backend.services.company_settings_service import company_settings_service

    other_company_id = _insert_extra_company(db, "Flow Only Co")

    company_settings_service.update_section(
        company_id=other_company_id,
        section="ai_behavior",
        values={"mode": "flow_only"},
        actor_user_id=None,
    )

    assert automation_policy.get_ai_mode("website_chat", 1) == "auto_reply"
    assert automation_policy.should_auto_reply_with_ai("website_chat", 1) is True

    assert automation_policy.get_ai_mode("website_chat", other_company_id) == "flow_only"
    assert automation_policy.should_auto_reply_with_ai(
        "website_chat", other_company_id
    ) is False


def test_two_configured_companies_stay_independent(fresh_db):
    """Two companies that both configure ai_behavior, in opposite
    directions, must never influence each other."""
    db = fresh_db
    from backend.services.company_settings_service import company_settings_service

    disabled_company_id = _insert_extra_company(db, "Disabled Co")
    enabled_company_id = _insert_extra_company(db, "Enabled Co")

    company_settings_service.update_section(
        company_id=disabled_company_id,
        section="ai_behavior",
        values={"enabled": False},
        actor_user_id=None,
    )
    company_settings_service.update_section(
        company_id=enabled_company_id,
        section="ai_behavior",
        values={"enabled": True},
        actor_user_id=None,
    )

    assert automation_policy.should_auto_reply_with_ai(
        "website_chat", disabled_company_id
    ) is False
    assert automation_policy.should_auto_reply_with_ai(
        "website_chat", enabled_company_id
    ) is True
    # Unconfigured default company is untouched by either of the above.
    assert automation_policy.should_auto_reply_with_ai("website_chat", 1) is True


# --- engine.py integration: the actual first-gate consumer ---


def test_engine_should_ai_take_priority_threads_company_id(fresh_db):
    """core/engine.py's Engine.should_ai_take_priority() is the first gate
    deciding whether the bot auto-replies at all. Prove it now reflects a
    company's own DB-configured ai_behavior instead of only the static
    per-channel file, for two different companies on the same channel."""
    db = fresh_db
    from backend.services.company_settings_service import company_settings_service

    other_company_id = _insert_extra_company(db, "Engine Test Co")
    company_settings_service.update_section(
        company_id=other_company_id,
        section="ai_behavior",
        values={"enabled": False},
        actor_user_id=None,
    )

    default_request = Request(
        channel="website_chat",
        user_id="engine_scope_test_default",
        message="hello",
        company_id=1,
    )
    other_request = Request(
        channel="website_chat",
        user_id="engine_scope_test_other",
        message="hello",
        company_id=other_company_id,
    )

    assert engine.should_ai_take_priority(default_request) is True
    assert engine.should_ai_take_priority(other_request) is False


def test_engine_handle_uses_company_scoped_ai_gate(fresh_db):
    """End-to-end: a company with AI disabled via Company Settings must not
    get an AI/business-module reply through Engine.handle(), while an
    unconfigured company on the same channel still does."""
    db = fresh_db
    from backend.services.company_settings_service import company_settings_service

    other_company_id = _insert_extra_company(db, "End To End Co")
    company_settings_service.update_section(
        company_id=other_company_id,
        section="ai_behavior",
        values={"enabled": False},
        actor_user_id=None,
    )

    session.sessions.pop("automation_scope_default", None)
    session.sessions.pop("automation_scope_other", None)

    default_request = Request(
        channel="website_chat",
        user_id="automation_scope_default",
        message="hi",
        company_id=1,
    )
    other_request = Request(
        channel="website_chat",
        user_id="automation_scope_other",
        message="hi",
        company_id=other_company_id,
    )

    default_response = engine.handle(default_request)
    other_response = engine.handle(other_request)

    # Default (unconfigured) company: "hi" is a greeting -> handled via the
    # AI/business-module pipeline exactly as it does today, never reaching
    # the scripted flow/menu state machine's own text.
    assert "T-ZONE IPTV" not in default_response.text

    # AI-disabled company: should_ai_take_priority() is False, so the turn
    # falls through to the scripted flow/menu state machine instead and
    # renders its "telegram_iptv_start" state text (used across channels,
    # not just telegram) -- the same marker
    # tests/test_reply_mode_flow_only.py uses to prove the flow path ran.
    assert "T-ZONE IPTV" in other_response.text


def test_file_level_kill_switch_stops_rule_based_flow_too(
    fresh_db, monkeypatch, tmp_path
):
    """CONFIRMED BUG regression guard: the ops-level file kill switch
    (config/automation_policy.json's per-channel bot_enabled=False) must
    stop BOTH the AI-generation branch AND the rule-based flow/menu state
    machine. Before this fix, is_bot_enabled() was defined but never
    consulted by Engine.handle() at all -- only is_ai_enabled() gated the
    AI branch, so a channel "disabled" at the ops level kept auto-replying
    via the scripted flow (proven here by the "T-ZONE IPTV" menu marker
    that would otherwise appear, exactly as in
    test_engine_handle_uses_company_scoped_ai_gate above)."""
    _write_automation_policy_file(
        monkeypatch, tmp_path, "website_chat", bot_enabled=False
    )

    session.sessions.pop("kill_switch_flow_user", None)

    request = Request(
        channel="website_chat",
        user_id="kill_switch_flow_user",
        message="hi",
        company_id=1,
    )

    response = engine.handle(request)

    assert response is None
