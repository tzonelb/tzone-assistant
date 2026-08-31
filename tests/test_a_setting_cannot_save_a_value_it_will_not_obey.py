"""A setting must not accept a value the platform will quietly override.

Two settings took any value at all, stored it, showed it back on the screen as
saved, and then behaved differently:

* **`working_hours.timezone`.** A mistyped zone was accepted with a 200. The
  engine treats an unreadable timezone as *open* — deliberately, so that a bad
  row can never silence a company's assistant — which meant an owner who set
  their hours and fat-fingered the zone had an assistant answering customers at
  three in the morning, for ever, with one line in a log nobody reads as the
  only trace.
* **`ai_behavior.return_to_ai_timeout_minutes`.** Saved at `-5`, displayed as
  `-5`, and clamped to `1` every time it was read. The owner's screen and the
  platform's behaviour disagreed, and the screen was the one that was wrong.

Both are the defect this audit has closed everywhere else — a decision that
saves and does not decide — with the twist that here the platform disagrees
with *itself*.

**Refuse at the write, tolerate at the read.** The engine keeps failing open on
an unreadable value, because rows already stored and a server missing its
timezone database are real and must not take a company's assistant away. What
changes is that the value can no longer *get* there through the front door.
That split is the same one the branch checks and the settings-section check
use, and it is stated here because a future reader will otherwise see two
places disagreeing about strictness and "fix" one of them.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def settings(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    import backend.services.company_settings_service  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    assert (
        getattr(
            sys.modules["backend.services.company_settings_service"],
            "database_manager",
            None,
        )
        is test_manager
    )

    from backend.services.company_settings_service import company_settings_service

    return company_settings_service


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


# --------------------------------------------------------------- the timezone


@pytest.mark.parametrize(
    "bad", ["Mars/Olympus", "GMT+3", "Beirut", "Asia/Beiruut", "not a zone", "UTC+2"]
)
def test_a_timezone_the_server_does_not_know_is_refused(settings, platform, bad):
    with pytest.raises(ValueError, match="not a timezone"):
        settings.update_section(
            _alpha(platform), "working_hours", {"timezone": bad}, None
        )


@pytest.mark.parametrize(
    "good", ["Asia/Beirut", "Europe/Paris", "UTC", "America/New_York", "Asia/Riyadh"]
)
def test_a_real_timezone_still_saves(settings, platform, good):
    """The other half. A check that refused everything would satisfy every test
    above and stop any company from setting its own hours."""
    saved = settings.update_section(
        _alpha(platform), "working_hours", {"timezone": good}, None
    )

    assert saved["values"]["timezone"] == good


def test_leaving_the_timezone_alone_is_allowed(settings, platform):
    """A write names only what it changes; touching the days must not require
    re-sending the zone."""
    settings.update_section(
        _alpha(platform), "working_hours", {"timezone": "Asia/Beirut"}, None
    )

    saved = settings.update_section(
        _alpha(platform),
        "working_hours",
        {"days": {"monday": {"enabled": True, "open": "09:00", "close": "17:00"}}},
        None,
    )

    assert saved["values"]["timezone"] == "Asia/Beirut"


@pytest.mark.parametrize(
    "clock", ["25:00", "09:70", "nine", "9", "", None, "-1:00"]
)
def test_an_impossible_time_of_day_is_refused(settings, platform, clock):
    """`09:70` reads as a time and is not one. Stored, it would make the
    assistant's idea of closing time depend on how the parser rounds."""
    if clock in ("", None):
        # Blank means "no window", which is a legitimate way to say a day has
        # no fixed hours — asserted here so the check does not overreach.
        settings.update_section(
            _alpha(platform),
            "working_hours",
            {"days": {"monday": {"enabled": True, "open": clock, "close": clock}}},
            None,
        )
        return

    with pytest.raises(ValueError):
        settings.update_section(
            _alpha(platform),
            "working_hours",
            {"days": {"monday": {"enabled": True, "open": clock, "close": "17:00"}}},
            None,
        )


def test_ordinary_opening_hours_still_save(settings, platform):
    saved = settings.update_section(
        _alpha(platform),
        "working_hours",
        {
            "timezone": "Asia/Beirut",
            "days": {
                "monday": {"enabled": True, "open": "09:00", "close": "17:30"},
                "friday": {"enabled": False},
                "saturday": {"enabled": True, "open": "00:00", "close": "23:59"},
            },
        },
        None,
    )

    assert saved["values"]["days"]["monday"]["close"] == "17:30"


# ------------------------------------------------------------- numeric ranges


@pytest.mark.parametrize("value", [-5, 0, 10**9, 1441])
def test_a_timeout_outside_its_range_is_refused(settings, platform, value):
    """Saved at -5 and obeyed as 1 is a screen telling its owner something the
    platform does not do."""
    with pytest.raises(ValueError, match="must be between"):
        settings.update_section(
            _alpha(platform),
            "ai_behavior",
            {"return_to_ai_timeout_minutes": value},
            None,
        )


@pytest.mark.parametrize("value", [1, 5, 60, 1440])
def test_a_timeout_inside_its_range_saves(settings, platform, value):
    saved = settings.update_section(
        _alpha(platform),
        "ai_behavior",
        {"return_to_ai_timeout_minutes": value},
        None,
    )

    assert saved["values"]["return_to_ai_timeout_minutes"] == value


def test_the_refusal_says_what_would_have_happened(settings, platform):
    """An error that only says "invalid" makes an owner guess. This one names
    the range and the value the platform would have used instead."""
    with pytest.raises(ValueError) as refused:
        settings.update_section(
            _alpha(platform),
            "ai_behavior",
            {"return_to_ai_timeout_minutes": -5},
            None,
        )

    message = str(refused.value)

    assert "1" in message and "1440" in message, message


# ------------------------------------------------------- the read stays kind


def test_a_bad_value_already_stored_still_reads_as_open(platform):
    """The half that must not change.

    Rows written before this check exist, and a server without a timezone
    database is a real deployment. The engine keeps treating an unreadable
    zone as open, because the alternative is a company's assistant going silent
    over a typo somebody made months ago.
    """
    from core.working_hours import status
    from datetime import datetime, timezone as tz

    at_3am = datetime(2026, 9, 14, 3, 0, tzinfo=tz.utc)

    stored = {
        "timezone": "Mars/Olympus",
        "enabled": True,
        "days": {"monday": {"enabled": True, "open": "09:00", "close": "17:00"}},
    }

    result = status(stored, at_3am)

    assert result.open is True
    assert result.unreadable, "the engine no longer says why it fell back"
