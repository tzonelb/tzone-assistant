"""Tests for the appointment calendar.

The defect this module exists to prevent is double booking: two customers
holding the same staff member at the same moment, each having been told the
booking succeeded. That failure is invisible until the day itself, when one of
them is turned away, so nothing but a test can catch it before a customer does.

Every test below names the concrete defect it protects against.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the appointment service at the test platform's databases.

    The service module is imported *before* the sweep so that its module-level
    `database_manager` is the live singleton the sweep is looking for. The
    assertion is the point of the fixture: if the rebinding silently missed the
    module, every test below would pass against the developer's real database
    instead of the fixture's, and prove nothing.
    """
    import sys

    import backend.services.appointment_service  # noqa: F401
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.appointment_service" in rebound

    from backend.services.appointment_service import appointment_service

    return appointment_service


# A Monday, so weekday() is 0 and the availability rules below are easy to read.
DAY = date(2026, 9, 14)

ALICE = 101
BOB = 202


def at(hour: int, minute: int = 0, *, day: date = DAY) -> str:
    """A UTC instant on the test day, in the format the service stores."""
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc
    ).isoformat()


def book(service, company, *, staff=ALICE, start=(10, 0), end=(11, 0), **extra):
    return service.create(
        company_id=company["id"],
        staff_user_id=staff,
        starts_at=at(*start),
        ends_at=at(*end),
        title=extra.pop("title", "Consultation"),
        **extra,
    )


# ----------------------------------------------------------------------
# Double booking
# ----------------------------------------------------------------------


def test_a_slot_can_be_booked_once(service, alpha):
    """The base case. Everything below is meaningless if a plain booking does
    not actually reach the company's database."""
    created = book(service, alpha)

    assert created["id"]
    assert created["status"] == "scheduled"
    assert created["starts_at"] == "2026-09-14T10:00:00+00:00"

    listing = service.list(company_id=alpha["id"])
    assert listing["total"] == 1


def test_an_overlapping_booking_for_the_same_staff_is_rejected(service, alpha):
    """The defect: two customers are both told 10:30 is theirs, and one of them
    is turned away on the day. A booking that overlaps a slot the same staff
    member already holds must be refused, not accepted and sorted out later."""
    from backend.services.appointment_service import SlotConflict

    book(service, alpha, start=(10, 0), end=(11, 0))

    with pytest.raises(SlotConflict):
        book(service, alpha, start=(10, 30), end=(11, 30))

    assert service.list(company_id=alpha["id"])["total"] == 1


@pytest.mark.parametrize(
    ("start", "end", "shape"),
    [
        ((10, 15), (10, 45), "wholly inside the existing appointment"),
        ((9, 30), (12, 0), "wholly containing the existing appointment"),
        ((9, 30), (10, 30), "overlapping the start"),
        ((10, 30), (11, 30), "overlapping the end"),
        ((10, 0), (11, 0), "exactly the existing appointment"),
    ],
)
def test_every_shape_of_overlap_is_rejected(service, alpha, start, end, shape):
    """The defect: an overlap check written as a single comparison catches only
    one of these five shapes. A booking nested inside an existing one, or one
    that swallows it whole, is just as much a double booking as a partial
    collision, and each has to be refused."""
    from backend.services.appointment_service import SlotConflict

    book(service, alpha, start=(10, 0), end=(11, 0))

    with pytest.raises(SlotConflict):
        book(service, alpha, start=start, end=end)

    assert service.list(company_id=alpha["id"])["total"] == 1, shape


def test_back_to_back_bookings_are_allowed(service, alpha):
    """The defect at the other boundary: an overlap test written with `<=`
    instead of `<` treats an appointment ending at 11:00 as colliding with one
    starting at 11:00, and refuses every consecutive booking in the day. Touching
    at a boundary is not overlapping — the interval is half-open."""
    first = book(service, alpha, start=(10, 0), end=(11, 0))
    second = book(service, alpha, start=(11, 0), end=(12, 0))
    third = book(service, alpha, start=(9, 0), end=(10, 0))

    assert len({first["id"], second["id"], third["id"]}) == 3
    assert service.list(company_id=alpha["id"])["total"] == 3


def test_cancelling_an_appointment_frees_its_slot(service, alpha):
    """The defect: a cancelled appointment that keeps holding its slot makes the
    calendar permanently un-rebookable — the customer cancels, the time is gone
    anyway, and nobody can find out why the slot will not take a booking."""
    from backend.services.appointment_service import SlotConflict

    first = book(service, alpha, start=(10, 0), end=(11, 0))

    with pytest.raises(SlotConflict):
        book(service, alpha, start=(10, 0), end=(11, 0))

    cancelled = service.cancel(
        company_id=alpha["id"],
        appointment_id=first["id"],
        reason="Customer called to cancel",
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_reason"] == "Customer called to cancel"

    replacement = book(service, alpha, start=(10, 0), end=(11, 0))

    assert replacement["id"] != first["id"]
    assert replacement["status"] == "scheduled"


def test_a_no_show_still_holds_its_slot(service, alpha):
    """The defect: treating every non-scheduled status as free. Only cancelling
    releases time. A customer who did not turn up still consumed the hour, and
    letting a second booking be written into the past would rewrite history in
    the day's records."""
    from backend.services.appointment_service import SlotConflict

    booked = book(service, alpha, start=(10, 0), end=(11, 0))
    service.set_status(
        company_id=alpha["id"], appointment_id=booked["id"], status="no_show"
    )

    with pytest.raises(SlotConflict):
        book(service, alpha, start=(10, 0), end=(11, 0))


def test_two_different_staff_can_hold_the_same_time(service, alpha):
    """The defect: an overlap check that forgets to filter on the staff member
    turns the whole company into a single chair — the second employee can never
    be booked while the first is busy, which is the opposite failure and just as
    damaging."""
    first = book(service, alpha, staff=ALICE, start=(10, 0), end=(11, 0))
    second = book(service, alpha, staff=BOB, start=(10, 0), end=(11, 0))

    assert first["id"] != second["id"]
    assert service.list(company_id=alpha["id"])["total"] == 2
    assert service.list(company_id=alpha["id"], staff_user_id=BOB)["total"] == 1


def test_simultaneous_bookings_of_one_slot_produce_exactly_one_appointment(
    service, alpha
):
    """The defect the whole module is built around: check-then-insert. Two
    requests that both read an empty calendar and then both insert leave two
    appointments in the same slot. Both threads are released at the same
    instant here, so a naive implementation loses this test rather than merely
    being unlikely to."""
    from backend.services.appointment_service import SlotConflict

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt():
        barrier.wait()
        try:
            book(service, alpha, start=(14, 0), end=(15, 0))
            result = "booked"
        except SlotConflict:
            result = "refused"
        except Exception as exc:  # pragma: no cover - surfaces a real crash
            result = f"error: {exc!r}"

        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt) for _ in range(2)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["booked", "refused"], outcomes
    assert service.list(company_id=alpha["id"])["total"] == 1


# ----------------------------------------------------------------------
# Rescheduling
# ----------------------------------------------------------------------


def test_rescheduling_onto_a_taken_slot_is_rejected(service, alpha):
    """The defect: guarding only the create path. Rescheduling writes the same
    two columns and races in exactly the same way, so an unguarded move is a
    double booking arriving through the back door."""
    from backend.services.appointment_service import SlotConflict

    first = book(service, alpha, start=(10, 0), end=(11, 0))
    second = book(service, alpha, start=(12, 0), end=(13, 0))

    with pytest.raises(SlotConflict):
        service.reschedule(
            company_id=alpha["id"],
            appointment_id=second["id"],
            starts_at=at(10, 30),
            ends_at=at(11, 30),
        )

    unchanged = service.get(company_id=alpha["id"], appointment_id=second["id"])
    assert unchanged["starts_at"] == "2026-09-14T12:00:00+00:00"
    assert first["starts_at"] == "2026-09-14T10:00:00+00:00"


def test_an_appointment_can_be_moved_within_its_own_time(service, alpha):
    """The defect: an overlap check on reschedule that forgets to exclude the
    row being moved. The appointment then collides with itself and can never be
    nudged by ten minutes."""
    booked = book(service, alpha, start=(10, 0), end=(11, 0))

    moved = service.reschedule(
        company_id=alpha["id"],
        appointment_id=booked["id"],
        starts_at=at(10, 10),
        ends_at=at(11, 10),
    )

    assert moved["starts_at"] == "2026-09-14T10:10:00+00:00"
    assert moved["ends_at"] == "2026-09-14T11:10:00+00:00"


def test_rescheduling_can_hand_the_appointment_to_another_staff_member(service, alpha):
    """The defect: moving the appointment to a colleague while still checking
    the original staff member's calendar, which books the colleague over an
    appointment they already have."""
    from backend.services.appointment_service import SlotConflict

    book(service, alpha, staff=BOB, start=(10, 0), end=(11, 0))
    mine = book(service, alpha, staff=ALICE, start=(15, 0), end=(16, 0))

    with pytest.raises(SlotConflict):
        service.reschedule(
            company_id=alpha["id"],
            appointment_id=mine["id"],
            starts_at=at(10, 0),
            ends_at=at(11, 0),
            staff_user_id=BOB,
        )

    moved = service.reschedule(
        company_id=alpha["id"],
        appointment_id=mine["id"],
        starts_at=at(11, 0),
        ends_at=at(12, 0),
        staff_user_id=BOB,
    )

    assert moved["staff_user_id"] == BOB


def test_a_cancelled_appointment_cannot_be_rescheduled_or_reactivated(service, alpha):
    """The defect: reviving a cancelled appointment silently retakes a slot
    another customer may already have booked in the meantime."""
    booked = book(service, alpha, start=(10, 0), end=(11, 0))
    service.cancel(company_id=alpha["id"], appointment_id=booked["id"])
    book(service, alpha, start=(10, 0), end=(11, 0))

    with pytest.raises(ValueError):
        service.reschedule(
            company_id=alpha["id"],
            appointment_id=booked["id"],
            starts_at=at(10, 0),
            ends_at=at(11, 0),
        )

    with pytest.raises(ValueError):
        service.set_status(
            company_id=alpha["id"], appointment_id=booked["id"], status="scheduled"
        )


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def test_an_appointment_must_end_after_it_starts(service, alpha):
    """The defect: a zero-length or reversed window overlaps nothing, so it
    slips past every guard and sits in the calendar as an unbookable ghost."""
    with pytest.raises(ValueError):
        book(service, alpha, start=(11, 0), end=(10, 0))

    with pytest.raises(ValueError):
        book(service, alpha, start=(10, 0), end=(10, 0))


def test_an_appointment_must_name_a_staff_member(service, alpha):
    """The defect: an appointment with no staff has no calendar to collide with,
    so it would be a permanent hole in the double-booking guarantee."""
    with pytest.raises(ValueError):
        service.create(
            company_id=alpha["id"],
            staff_user_id=None,
            starts_at=at(10, 0),
            ends_at=at(11, 0),
            title="Nobody's appointment",
        )


def test_times_are_stored_in_one_comparable_format(service, alpha):
    """The defect: `starts_at` is TEXT and every overlap test is a string
    comparison. Mixing `...Z`, `+00:00` and local offsets makes those
    comparisons lie, and a booking sorted wrong is a booking overlooked."""
    booked = service.create(
        company_id=alpha["id"],
        staff_user_id=ALICE,
        starts_at="2026-09-14T10:00:00Z",
        ends_at="2026-09-14T13:00:00+02:00",  # 11:00 UTC
        title="Mixed input formats",
    )

    assert booked["starts_at"] == "2026-09-14T10:00:00+00:00"
    assert booked["ends_at"] == "2026-09-14T11:00:00+00:00"

    from backend.services.appointment_service import SlotConflict

    # Stored as UTC, so an equivalent time written another way still collides.
    with pytest.raises(SlotConflict):
        service.create(
            company_id=alpha["id"],
            staff_user_id=ALICE,
            starts_at="2026-09-14T12:30:00+02:00",  # 10:30 UTC
            ends_at="2026-09-14T11:30:00Z",
            title="Same hour, different notation",
        )


# ----------------------------------------------------------------------
# Availability rules and free slots
# ----------------------------------------------------------------------


def _weekday_rule(service, company, *, staff=ALICE, start="09:00", end="12:00", minutes=60):
    return service.create_rule(
        company_id=company["id"],
        staff_user_id=staff,
        weekday=DAY.weekday(),
        start_time=start,
        end_time=end,
        slot_minutes=minutes,
    )


def test_free_slots_come_from_the_availability_rules(service, alpha):
    """The defect: offering the customer times the staff member does not work.
    Slots exist only where a rule says so."""
    _weekday_rule(service, alpha)

    result = service.available_slots(alpha["id"], ALICE, DAY.isoformat())

    assert [slot["starts_at"][11:16] for slot in result["slots"]] == [
        "09:00",
        "10:00",
        "11:00",
    ]

    # A day with no rule for this weekday offers nothing at all.
    other_day = (DAY + timedelta(days=1)).isoformat()
    assert service.available_slots(alpha["id"], ALICE, other_day)["slots"] == []


def test_a_booked_slot_disappears_from_the_free_list(service, alpha):
    """The defect: the screen offering a slot the API then refuses. The slot
    list has to subtract existing appointments using the same overlap rule the
    booking guard uses, or the two disagree."""
    _weekday_rule(service, alpha)
    book(service, alpha, start=(10, 0), end=(11, 0))

    slots = service.available_slots(alpha["id"], ALICE, DAY.isoformat())["slots"]

    assert [slot["starts_at"][11:16] for slot in slots] == ["09:00", "11:00"]


def test_a_cancelled_appointment_returns_its_slot_to_the_free_list(service, alpha):
    """The defect: a cancellation that frees the slot for the booking guard but
    not for the slot list leaves the time bookable by API and invisible on the
    screen."""
    _weekday_rule(service, alpha)
    booked = book(service, alpha, start=(10, 0), end=(11, 0))

    service.cancel(company_id=alpha["id"], appointment_id=booked["id"])

    slots = service.available_slots(alpha["id"], ALICE, DAY.isoformat())["slots"]

    assert [slot["starts_at"][11:16] for slot in slots] == ["09:00", "10:00", "11:00"]


def test_one_staff_members_bookings_do_not_hide_anothers_slots(service, alpha):
    """The defect: subtracting the whole company's appointments from one
    person's calendar, which empties the schedule of everyone who is free."""
    _weekday_rule(service, alpha, staff=ALICE)
    _weekday_rule(service, alpha, staff=BOB)

    book(service, alpha, staff=ALICE, start=(10, 0), end=(11, 0))

    alice_slots = service.available_slots(alpha["id"], ALICE, DAY.isoformat())["slots"]
    bob_slots = service.available_slots(alpha["id"], BOB, DAY.isoformat())["slots"]

    assert len(alice_slots) == 2
    assert len(bob_slots) == 3


def test_an_inactive_rule_stops_producing_slots(service, alpha):
    """The defect: turning a working day off in the editor and still having
    customers offered times on it."""
    rule = _weekday_rule(service, alpha)

    service.update_rule(
        company_id=alpha["id"], rule_id=rule["id"], status="inactive"
    )

    assert service.available_slots(alpha["id"], ALICE, DAY.isoformat())["slots"] == []


def test_a_working_window_must_end_after_it_starts(service, alpha):
    """The defect: a reversed window generates no slots and reports no error, so
    the day quietly vanishes from the calendar with nothing to explain it."""
    with pytest.raises(ValueError):
        _weekday_rule(service, alpha, start="17:00", end="09:00")

    with pytest.raises(ValueError):
        service.create_rule(
            company_id=alpha["id"],
            staff_user_id=ALICE,
            weekday=9,
            start_time="09:00",
            end_time="17:00",
        )


def test_deleting_a_rule_removes_it(service, alpha):
    """The defect: an editor whose delete button leaves the rule in place keeps
    generating slots the company thought it had withdrawn."""
    from backend.services.appointment_service import AppointmentNotFound

    rule = _weekday_rule(service, alpha)

    assert service.delete_rule(company_id=alpha["id"], rule_id=rule["id"]) is True
    assert service.list_rules(company_id=alpha["id"]) == []

    with pytest.raises(AppointmentNotFound):
        service.delete_rule(company_id=alpha["id"], rule_id=rule["id"])


# ----------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------


def test_the_date_range_keeps_appointments_late_on_the_last_day(service, alpha):
    """The defect: comparing an instant against a bare `YYYY-MM-DD` end date
    drops everything after midnight on that day, so the last day of every week
    view looks empty."""
    book(service, alpha, start=(23, 30), end=(23, 59))
    book(service, alpha, start=(10, 0), end=(11, 0))

    listing = service.list(
        company_id=alpha["id"],
        start_date=DAY.isoformat(),
        end_date=DAY.isoformat(),
    )

    assert listing["total"] == 2

    earlier = service.list(
        company_id=alpha["id"],
        start_date=(DAY - timedelta(days=3)).isoformat(),
        end_date=(DAY - timedelta(days=1)).isoformat(),
    )

    assert earlier["total"] == 0


def test_listing_can_exclude_cancelled_appointments(service, alpha):
    """The defect: a calendar that keeps drawing cancelled appointments over the
    slots that are now free."""
    booked = book(service, alpha, start=(10, 0), end=(11, 0))
    service.cancel(company_id=alpha["id"], appointment_id=booked["id"])

    assert service.list(company_id=alpha["id"])["total"] == 1
    assert (
        service.list(company_id=alpha["id"], include_cancelled=False)["total"] == 0
    )


# ----------------------------------------------------------------------
# Tenant isolation
# ----------------------------------------------------------------------


def test_one_company_cannot_see_anothers_appointments(service, alpha, beta):
    """The defect: appointments name customers and the times they will be at the
    company's address. A listing that reached across companies would hand one
    business its competitor's diary."""
    book(service, alpha, start=(10, 0), end=(11, 0), title="Alpha booking")

    assert service.list(company_id=beta["id"])["total"] == 0
    assert service.list(company_id=alpha["id"])["total"] == 1
    assert service.get(company_id=beta["id"], appointment_id=1) is None


def test_one_company_cannot_modify_anothers_appointments(service, alpha, beta):
    """The defect: an id guessed from another company's calendar being enough to
    cancel or move that company's appointment. The id must be meaningless
    outside the company that owns it."""
    from backend.services.appointment_service import AppointmentNotFound

    booked = book(service, alpha, start=(10, 0), end=(11, 0))

    with pytest.raises(AppointmentNotFound):
        service.cancel(company_id=beta["id"], appointment_id=booked["id"])

    with pytest.raises(AppointmentNotFound):
        service.reschedule(
            company_id=beta["id"],
            appointment_id=booked["id"],
            starts_at=at(15, 0),
            ends_at=at(16, 0),
        )

    with pytest.raises(AppointmentNotFound):
        service.set_status(
            company_id=beta["id"], appointment_id=booked["id"], status="completed"
        )

    untouched = service.get(company_id=alpha["id"], appointment_id=booked["id"])
    assert untouched["status"] == "scheduled"
    assert untouched["starts_at"] == "2026-09-14T10:00:00+00:00"


def test_the_same_slot_can_be_booked_by_each_company(service, alpha, beta):
    """The defect: an overlap check that reached across databases would let one
    company's fully booked day block another company's calendar entirely."""
    first = book(service, alpha, start=(10, 0), end=(11, 0))
    second = book(service, beta, start=(10, 0), end=(11, 0))

    assert first["id"] == second["id"]  # separate databases, separate sequences
    assert service.list(company_id=alpha["id"])["total"] == 1
    assert service.list(company_id=beta["id"])["total"] == 1


def test_availability_rules_are_company_data_too(service, alpha, beta):
    """The defect: one company's working hours leaking into another's slot
    generator, offering customers times nobody works."""
    _weekday_rule(service, alpha)

    assert service.list_rules(company_id=beta["id"]) == []
    assert service.available_slots(beta["id"], ALICE, DAY.isoformat())["slots"] == []
    assert len(service.available_slots(alpha["id"], ALICE, DAY.isoformat())["slots"]) == 3
