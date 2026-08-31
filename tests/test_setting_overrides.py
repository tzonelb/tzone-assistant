"""Tests for the operator override that nothing could write.

`super_admin_setting_overrides` has been read by `company_settings_service`
since the table shipped: it pins a value for one company and can mark a key
locked, and `update_section` already refuses to write a locked key.

Nothing ever wrote a row. The read side worked, the enforcement worked, and the
feature was unreachable — every company's `locked_keys` was `[]` for ever
because there was no way to put anything in it. That is the same shape as the
switches that saved and decided nothing, arriving from the other direction: a
guard that was enforced and could never be armed.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.company_settings_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.company_settings_service" in rebound

    from backend.services.company_settings_service import company_settings_service

    return company_settings_service


# ---------------------------------------------------------------- the writer


def test_an_override_pins_a_value(wired, alpha):
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        value=45,
    )

    section = wired.get_section(alpha["id"], "ai_behavior")

    assert section["values"]["collect_message_delay_seconds"] == 45


def test_locking_a_key_stops_the_company_changing_it(wired, alpha):
    """The enforcement already existed in `update_section`. Until now there was
    no way to arm it."""
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        is_locked=True,
    )

    assert "collect_message_delay_seconds" in wired.get_section(
        alpha["id"], "ai_behavior"
    )["locked_keys"]

    with pytest.raises(Exception):
        wired.update_section(
            alpha["id"],
            "ai_behavior",
            {"collect_message_delay_seconds": 5},
            None,
        )


def test_a_company_can_still_change_an_unlocked_key(wired, alpha):
    """Locking one setting must not freeze the section."""
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        is_locked=True,
    )

    result = wired.update_section(
        alpha["id"], "ai_behavior", {"reply_language": "en"}, None
    )

    assert result["values"]["reply_language"] == "en"


def test_a_value_can_be_pinned_without_being_locked(wired, alpha):
    """An operator may want to correct a value without taking the control
    away."""
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        value=45,
    )

    section = wired.get_section(alpha["id"], "ai_behavior")

    assert section["values"]["collect_message_delay_seconds"] == 45
    assert section["locked_keys"] == []


def test_a_key_can_be_locked_without_pinning_a_value(wired, alpha):
    """And the gentler action: lock a company to whatever it has already
    chosen, without deciding for them. Forcing both would make this
    impossible."""
    wired.update_section(
        alpha["id"], "ai_behavior", {"collect_message_delay_seconds": 30}, None
    )

    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        is_locked=True,
    )

    section = wired.get_section(alpha["id"], "ai_behavior")

    assert section["values"]["collect_message_delay_seconds"] == 30
    assert "collect_message_delay_seconds" in section["locked_keys"]


def test_locking_later_does_not_disturb_the_pinned_value(wired, alpha):
    """`value` omitted leaves the pin untouched, which is why the default is a
    sentinel: `None` is a legitimate thing to pin."""
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        value=45,
    )
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        is_locked=True,
    )

    section = wired.get_section(alpha["id"], "ai_behavior")

    assert section["values"]["collect_message_delay_seconds"] == 45
    assert "collect_message_delay_seconds" in section["locked_keys"]


def test_clearing_an_override_hands_the_setting_back(wired, alpha):
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        value=45,
        is_locked=True,
    )

    wired.clear_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
    )

    section = wired.get_section(alpha["id"], "ai_behavior")

    assert section["locked_keys"] == []
    assert section["values"]["collect_message_delay_seconds"] != 45
    assert wired.list_overrides(alpha["id"]) == []


# ---------------------------------------------------------------- validation


def test_an_unknown_setting_key_is_refused(wired, alpha):
    """A key no section defines would sit in the table for ever, pinning
    nothing and locking nothing, while the console showed it as applied."""
    with pytest.raises(ValueError, match="not a setting"):
        wired.set_override(
            company_id=alpha["id"],
            section="ai_behavior",
            setting_key="collect_mesage_delay",  # typo
            value=45,
        )


def test_an_unknown_section_is_refused(wired, alpha):
    with pytest.raises(ValueError, match="not a settings section"):
        wired.set_override(
            company_id=alpha["id"],
            section="nonsense",
            setting_key="anything",
            value=1,
        )


def test_an_empty_key_is_refused(wired, alpha):
    with pytest.raises(ValueError):
        wired.set_override(
            company_id=alpha["id"],
            section="ai_behavior",
            setting_key="   ",
            value=1,
        )


# ----------------------------------------------------------------- isolation


def test_an_override_reaches_only_the_company_it_names(wired, alpha, beta):
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        value=45,
        is_locked=True,
    )

    other = wired.get_section(beta["id"], "ai_behavior")

    assert other["locked_keys"] == []
    assert other["values"]["collect_message_delay_seconds"] != 45


def test_the_override_list_shows_what_is_pinned_and_what_is_locked(wired, alpha):
    wired.set_override(
        company_id=alpha["id"],
        section="ai_behavior",
        setting_key="collect_message_delay_seconds",
        value=45,
        is_locked=True,
    )

    items = wired.list_overrides(alpha["id"])

    assert len(items) == 1
    assert items[0]["setting_key"] == "collect_message_delay_seconds"
    assert items[0]["value"] == 45
    assert items[0]["is_locked"] is True
