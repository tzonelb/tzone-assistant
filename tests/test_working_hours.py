"""A company's opening hours, which were stored and read by nothing.

The whole `working_hours` section — `enabled`, `timezone`, and a row per day —
has been seeded into every company's database since the settings shipped. No
code consulted it. A company could set its hours, watch them save, and have the
assistant answer at three in the morning exactly as it does at noon.

What the hours change is escalation, not the assistant. A bot that stops
answering outside office hours is worse for the customer than one that keeps
going; what is wrong is telling a customer "our team can check it for you" at
3am, which promises somebody is coming when nobody is until the shop opens. The
conversation is still handed over. The sentence after it says when.

Times, timezones and midnight crossings are exactly the sort of thing that is
wrong in one branch nobody runs, so the rules live in `core/working_hours.py`
with no database, session or request, and are asserted here at fixed instants
rather than at whatever time the suite happens to run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core import working_hours


def _hours(**overrides):
    section = {
        "enabled": True,
        "timezone": "Asia/Beirut",
        "days": {
            day: {"open": "09:00", "close": "18:00", "closed": False}
            for day in working_hours.DAY_NAMES
        },
    }
    section.update(overrides)
    return section


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# 2026-08-17 is a Monday.
MONDAY = (2026, 8, 17)


# ----------------------------------------------------------------- the default


def test_hours_switched_off_are_always_open():
    """The default, and the reason implementing this changed nothing for any
    company that has not asked for it."""
    assert working_hours.status(_hours(enabled=False), _utc(*MONDAY, 2)).open


def test_a_missing_section_is_open():
    assert working_hours.status({}, _utc(*MONDAY, 2)).open
    assert working_hours.status(None, _utc(*MONDAY, 2)).open


# -------------------------------------------------------------- inside and out


def test_inside_the_working_day_is_open():
    # 09:00 UTC is 12:00 in Beirut.
    assert working_hours.status(_hours(), _utc(*MONDAY, 9)).open


def test_before_opening_is_closed():
    # 03:00 UTC is 06:00 in Beirut, three hours before opening.
    assert not working_hours.status(_hours(), _utc(*MONDAY, 3)).open


def test_after_closing_is_closed():
    # 20:00 UTC is 23:00 in Beirut.
    assert not working_hours.status(_hours(), _utc(*MONDAY, 20)).open


def test_the_moment_of_opening_is_open():
    # 06:00 UTC is exactly 09:00 in Beirut.
    assert working_hours.status(_hours(), _utc(*MONDAY, 6)).open


def test_the_moment_of_closing_is_closed():
    """A range that included its own closing minute would keep a company open
    for a minute longer than it said."""
    # 15:00 UTC is exactly 18:00 in Beirut.
    assert not working_hours.status(_hours(), _utc(*MONDAY, 15)).open


# ------------------------------------------------------------------- timezones


def test_two_timezones_disagree_about_the_same_instant():
    at = _utc(*MONDAY, 1)  # 04:00 Beirut, 10:00 Tokyo

    assert not working_hours.status(_hours(), at).open
    assert working_hours.status(_hours(timezone="Asia/Tokyo"), at).open


def test_an_unknown_timezone_leaves_the_company_open():
    """Fail open. A typo in a timezone name must not tell a company's customers
    it is closed while the team is sitting there."""
    status = working_hours.status(
        _hours(timezone="Mars/Olympus_Mons"), _utc(*MONDAY, 2)
    )

    assert status.open


# ---------------------------------------------------------------- closed days


def test_a_day_marked_closed_is_closed_all_day():
    hours = _hours()
    hours["days"]["monday"] = {"open": "09:00", "close": "18:00", "closed": True}

    assert not working_hours.status(hours, _utc(*MONDAY, 9)).open


def test_a_closed_day_does_not_close_the_next_one():
    hours = _hours()
    hours["days"]["monday"] = {"open": "09:00", "close": "18:00", "closed": True}

    assert working_hours.status(hours, _utc(2026, 8, 18, 9)).open


# ------------------------------------------------------------- crossing midnight


def test_an_evening_shift_that_runs_past_midnight_stays_open():
    """18:00 to 02:00 is a real shift. Read as an empty range it would report a
    company that is open all evening as closed all evening."""
    hours = _hours()
    for day in working_hours.DAY_NAMES:
        hours["days"][day] = {"open": "18:00", "close": "02:00", "closed": False}

    # 20:00 UTC is 23:00 in Beirut, inside the shift.
    assert working_hours.status(hours, _utc(*MONDAY, 20)).open


def test_the_small_hours_of_a_shift_started_yesterday_are_open():
    hours = _hours()
    for day in working_hours.DAY_NAMES:
        hours["days"][day] = {"open": "18:00", "close": "02:00", "closed": False}

    # 22:00 UTC Monday is 01:00 Beirut on Tuesday, still inside Monday's shift.
    assert working_hours.status(hours, _utc(*MONDAY, 22)).open


def test_after_an_overnight_shift_closes_is_closed():
    hours = _hours()
    for day in working_hours.DAY_NAMES:
        hours["days"][day] = {"open": "18:00", "close": "02:00", "closed": False}

    # 04:00 UTC is 07:00 Beirut, after the 02:00 close.
    assert not working_hours.status(hours, _utc(2026, 8, 18, 4)).open


# --------------------------------------------------------- malformed, fails open


@pytest.mark.parametrize(
    "days",
    [
        "not a mapping",
        {"monday": "not a mapping"},
        {"monday": {"open": "nonsense", "close": "18:00"}},
        {"monday": {"open": "09:00"}},
        {},
    ],
)
def test_hours_that_cannot_be_read_leave_the_company_open(days):
    """Every failure is open, deliberately. A corrupt config gets the behaviour
    the platform had before any of this existed."""
    assert working_hours.status(_hours(days=days), _utc(*MONDAY, 12)).open


# --------------------------------------------------------------- when it reopens


def test_a_closed_company_says_when_it_next_opens():
    status = working_hours.status(_hours(), _utc(*MONDAY, 3))

    assert not status.open
    assert status.opens_at is not None
    assert status.opens_at.hour == 9
    assert status.opens_at.day == 17


def test_after_closing_the_next_opening_is_tomorrow():
    status = working_hours.status(_hours(), _utc(*MONDAY, 20))

    assert status.opens_at.day == 18


def test_the_next_opening_skips_a_closed_day():
    hours = _hours()
    hours["days"]["tuesday"] = {"open": "09:00", "close": "18:00", "closed": True}

    status = working_hours.status(hours, _utc(*MONDAY, 20))

    assert status.opens_at.day == 19


def test_a_company_open_on_no_day_names_no_time():
    """Rather than searching for ever, or naming a moment that does not exist."""
    hours = _hours()
    for day in working_hours.DAY_NAMES:
        hours["days"][day] = {"open": "09:00", "close": "18:00", "closed": True}

    status = working_hours.status(hours, _utc(*MONDAY, 12))

    assert not status.open
    assert status.opens_at is None


# ------------------------------------------------------ what the customer reads
#
# The rules above decide open or closed. These decide what that does to the one
# reply the hours are allowed to change: the escalating fallback, where the
# assistant says a person will pick this up.


def _closed_at_3am():
    return working_hours.status(_hours(), _utc(*MONDAY, 0))  # 03:00 Beirut


def _open_at_noon():
    return working_hours.status(_hours(), _utc(*MONDAY, 9))  # 12:00 Beirut


@pytest.mark.parametrize("language", ["en", "ar"])
def test_an_open_company_reads_exactly_as_it_did_before(language):
    """The reply for an open company must be byte-for-byte what it was before
    working hours existed, or this became a change to every company's wording
    rather than a feature one company switched on."""
    from core.engine import engine

    with_hours = engine.build_safe_result(
        language=language,
        current_department=None,
        escalate=True,
        opening=_open_at_noon(),
    )
    without = engine.build_safe_result(
        language=language, current_department=None, escalate=True
    )

    assert with_hours["reply"] == without["reply"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_closed_company_says_when_the_team_is_back(language):
    from core.engine import engine

    result = engine.build_safe_result(
        language=language,
        current_department=None,
        escalate=True,
        opening=_closed_at_3am(),
    )

    assert "09:00" in result["reply"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_conversation_is_still_handed_over(language):
    """Being closed does not cancel the escalation. The customer still needs a
    person; they are only told when that person will be there. Dropping the
    hand-over would lose the conversation until somebody noticed it."""
    from core.engine import engine

    result = engine.build_safe_result(
        language=language,
        current_department=None,
        escalate=True,
        opening=_closed_at_3am(),
    )

    assert result["needs_human"] is True
    assert result["buttons"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_company_that_does_not_escalate_says_nothing_about_hours(language):
    """`fallback_to_human` off means the conversation stays where it is. There
    is no hand-over to qualify, so naming opening hours would be answering a
    question the customer did not ask."""
    from core.engine import engine

    result = engine.build_safe_result(
        language=language,
        current_department=None,
        escalate=False,
        opening=_closed_at_3am(),
    )

    assert "09:00" not in result["reply"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_company_open_on_no_day_names_no_time_to_the_customer(language):
    """It still says the team is away — it just cannot name an hour, and
    inventing one would be worse than the vaguer sentence."""
    from core.engine import engine

    hours = _hours()
    for day in working_hours.DAY_NAMES:
        hours["days"][day] = {"open": "09:00", "close": "18:00", "closed": True}

    result = engine.build_safe_result(
        language=language,
        current_department=None,
        escalate=True,
        opening=working_hours.status(hours, _utc(*MONDAY, 12)),
    )

    assert ":" not in result["reply"].split(".")[-2] or "09:00" not in result["reply"]


def test_hours_that_cannot_be_read_leave_the_reply_alone():
    from core.engine import engine

    unreadable = working_hours.status(
        _hours(timezone="Mars/Olympus_Mons"), _utc(*MONDAY, 0)
    )

    assert engine.build_safe_result(
        language="en", current_department=None, escalate=True, opening=unreadable
    )["reply"] == engine.build_safe_result(
        language="en", current_department=None, escalate=True
    )["reply"]


# --------------------------------------------- the wiring between the two
#
# Everything above tests the rules with a section handed to them, and the reply
# with a status handed to it. Neither touches the database, which leaves the
# join between them — the engine reading this company's stored hours — untested.
# That join is where a working feature and a stored setting stop meeting.


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.company_settings_service  # noqa: F401
    import core.engine  # noqa: F401

    original = manager_module.database_manager
    monkeypatch.setattr(manager_module, "database_manager", platform["manager"])

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", platform["manager"])
            rebound.append(module.__name__)

    assert "backend.services.company_settings_service" in rebound

    from core.engine import engine

    return engine


def test_a_company_that_has_not_set_hours_is_open(wired, alpha):
    """`enabled` is False in the defaults, so every company that has never
    opened the screen keeps the behaviour it had."""
    assert wired.opening_status(alpha["id"]).open


def test_the_engine_reads_this_companys_stored_hours(wired, alpha):
    """The join. Without this, `working_hours` could be stored, validated and
    served correctly and still never reach a decision — which is the exact
    shape of the defect the whole section was."""
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        alpha["id"],
        "working_hours",
        {
            "enabled": True,
            "timezone": "Asia/Beirut",
            "days": {
                day: {"open": "09:00", "close": "18:00", "closed": True}
                for day in working_hours.DAY_NAMES
            },
        },
        None,
    )

    assert not wired.opening_status(alpha["id"]).open


def test_one_companys_hours_do_not_close_another(wired, alpha, beta):
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        alpha["id"],
        "working_hours",
        {
            "enabled": True,
            "days": {
                day: {"open": "09:00", "close": "18:00", "closed": True}
                for day in working_hours.DAY_NAMES
            },
        },
        None,
    )

    assert not wired.opening_status(alpha["id"]).open
    assert wired.opening_status(beta["id"]).open


def test_hours_the_database_will_not_give_up_leave_the_company_open(
    wired, alpha, monkeypatch
):
    import backend.services.company_settings_service as settings_module

    def _broken(*args, **kwargs):
        raise RuntimeError("the tenant database is busy")

    monkeypatch.setattr(
        settings_module.company_settings_service, "get_section", _broken
    )

    assert wired.opening_status(alpha["id"]).open
