"""Whether a company is open right now, from the hours it set.

The whole `working_hours` section — `enabled`, `timezone` and a row per day —
has been seeded into every company's database since the settings shipped, and
nothing has ever read it. A company could set its opening hours, watch them
save, and have the assistant answer at three in the morning exactly as it does
at noon.

What the hours change is *escalation*, not the assistant. A bot that stops
working outside office hours is worse for the customer than one that keeps
answering; what is wrong is handing a conversation to a human at 3am, which
tells the customer somebody is coming when nobody is until morning. So the
conversation is still escalated — it still needs a person — and the customer is
told when that person will be there.

Kept apart from `core/engine.py` and free of any database, session or request,
so every rule below can be asserted directly. Times, timezones and midnight
crossings are exactly the kind of thing that is wrong in one branch nobody
runs.

**Every failure is open.** An unreadable timezone, a malformed time, a day that
is not a mapping — each one answers "open". A company whose hours are corrupt
gets today's behaviour, which is an assistant that escalates normally. The
opposite default would have a broken config telling that company's customers it
is closed while the team sits there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


logger = logging.getLogger(__name__)


# Indexed by `datetime.weekday()`: Monday is 0.
DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

FALLBACK_TIMEZONE = "UTC"

# How far ahead to look for the next opening before giving up. Seven days plus
# one covers a company that is closed every day of the week, which is a
# configuration somebody will eventually save by accident; without the bound
# the search would not terminate.
SEARCH_DAYS = 8


@dataclass(frozen=True)
class OpeningStatus:
    """Whether the company is open, and when it next is if not."""

    open: bool
    opens_at: datetime | None = None
    # Set only when the hours could not be read. The caller treats it as open,
    # and the field exists so a diagnostic can say why rather than reporting a
    # company as open with no explanation.
    unreadable: str | None = None


def _zone(section: dict[str, Any]) -> ZoneInfo | None:
    """The company's timezone, or None when it cannot be read.

    None is not "use UTC". Evaluating a Beirut company's hours in UTC would
    shift its whole day by three hours and tell its customers it is closed
    during business hours — a worse answer than not applying hours at all.
    """
    name = str(section.get("timezone") or "").strip() or FALLBACK_TIMEZONE

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("Unknown working-hours timezone %r", name)
        return None


def _parse(value: Any) -> time | None:
    """`"09:00"` to a time. Anything else is None, which reads as open."""
    text = str(value or "").strip()

    if not text:
        return None

    try:
        hour, _, minute = text.partition(":")
        return time(hour=int(hour), minute=int(minute or 0))
    except (TypeError, ValueError):
        return None


def _day(section: dict[str, Any], name: str) -> dict[str, Any] | None:
    days = section.get("days")

    if not isinstance(days, dict):
        return None

    entry = days.get(name)

    return entry if isinstance(entry, dict) else None


# What one calendar day says. The three answers are deliberately distinct:
# "closed" is a decision the company made and is honoured, "unreadable" is a
# config this code cannot act on and must not act on.
DAY_OPEN = "open"
DAY_CLOSED = "closed"
DAY_UNREADABLE = "unreadable"


def _opens_on(
    section: dict[str, Any], moment: datetime
) -> tuple[str, tuple[time, time] | None]:
    """How this calendar day reads.

    The first version collapsed "the company marked this day closed" and "this
    day's entry is missing or malformed" into the same answer, so a typo in one
    day's opening time closed the company on that day for ever. Only the first
    of those is something the company asked for.
    """
    entry = _day(section, DAY_NAMES[moment.weekday()])

    if entry is None:
        return DAY_UNREADABLE, None

    if bool(entry.get("closed")):
        return DAY_CLOSED, None

    opens = _parse(entry.get("open"))
    closes = _parse(entry.get("close"))

    if opens is None or closes is None:
        return DAY_UNREADABLE, None

    return DAY_OPEN, (opens, closes)


def _covers(opens: time, closes: time, moment: datetime) -> bool:
    """Whether a local time falls inside one day's opening range.

    `closes <= opens` is read as crossing midnight — 18:00 to 02:00 is a real
    shift, and reading it as an empty range would report a company that is open
    all evening as closed all evening.
    """
    current = moment.time()

    if opens <= closes:
        return opens <= current < closes

    return current >= opens or current < closes


def status(section: Any, now: datetime | None = None) -> OpeningStatus:
    """Whether this company is open at ``now``.

    ``section`` is the stored `working_hours` values. ``now`` defaults to the
    current instant and is a parameter so the rules can be tested at a fixed
    time rather than only at whatever time the suite happens to run.
    """
    if not isinstance(section, dict):
        return OpeningStatus(open=True, unreadable="working_hours is not a mapping")

    if not section.get("enabled"):
        # The default, and the reason implementing this changes nothing for a
        # company that has not asked for it.
        return OpeningStatus(open=True)

    zone = _zone(section)

    if zone is None:
        return OpeningStatus(open=True, unreadable="the timezone is not known")

    moment = (now or datetime.now(timezone.utc)).astimezone(zone)

    if not isinstance(section.get("days"), dict):
        return OpeningStatus(open=True, unreadable="no days are configured")

    verdict, hours = _opens_on(section, moment)

    if verdict == DAY_UNREADABLE:
        return OpeningStatus(open=True, unreadable="today's hours cannot be read")

    if hours and _covers(hours[0], hours[1], moment):
        return OpeningStatus(open=True)

    # A shift that began yesterday and has not closed yet. Without this, 01:00
    # on a company that opens 18:00-02:00 reads as closed.
    _, before = _opens_on(section, moment - timedelta(days=1))

    if before and before[1] <= before[0] and moment.time() < before[1]:
        return OpeningStatus(open=True)

    return OpeningStatus(open=False, opens_at=_next_opening(section, moment))


def _next_opening(section: dict[str, Any], moment: datetime) -> datetime | None:
    """When the company is next open, in its own timezone.

    None when it is open on no day at all, which is a configuration rather than
    a moment — the caller says "we will be in touch" instead of naming a time
    that does not exist.
    """
    for offset in range(SEARCH_DAYS):
        day = moment + timedelta(days=offset)
        _, hours = _opens_on(section, day)

        if not hours:
            continue

        candidate = day.replace(
            hour=hours[0].hour,
            minute=hours[0].minute,
            second=0,
            microsecond=0,
        )

        if candidate > moment:
            return candidate

    return None
