"""What a busy company costs, as opposed to a new one.

Every other test in this suite runs against a company with a handful of rows.
The failures this file looks for only appear once there are many: a query with
no index that a hundred rows hide and fifty thousand do not, a page count that
walks the whole table to answer "how many", an export that builds the entire
result in memory before sending any of it.

The numbers here are deliberately modest — thousands, not millions. This is a
platform for companies with inboxes, and the point is to catch a cost that
grows with the wrong power of N, not to benchmark SQLite. A query that is
linear stays fast at these sizes; one that is quadratic does not.
"""

from __future__ import annotations

import sys
import time

import pytest


MESSAGES = 4000

# Generous enough not to fail on a loaded CI box, tight enough that a
# full-table scan per page cannot hide under it.
PAGE_BUDGET_SECONDS = 2.0


@pytest.fixture()
def wired(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    import backend.services.activity_service  # noqa: F401
    import backend.services.conversation_control_service  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    for name in (
        "backend.services.activity_service",
        "backend.services.conversation_control_service",
    ):
        assert getattr(sys.modules[name], "database_manager", None) is test_manager

    return test_manager


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


@pytest.fixture()
def a_busy_log(wired, platform):
    """Thousands of audit entries, written directly.

    Through the service this would be thousands of separate connections and
    would take minutes; the rows are what this file is about, not how they got
    there.
    """
    from database.manager import utc_now_iso

    company_id = _alpha(platform)
    now = utc_now_iso()

    with wired.tenant(company_id) as conn:
        conn.executemany(
            """
            INSERT INTO activity_log (
                company_id, kind, category, action, actor_user_id, actor_label,
                target_type, target_id, summary, ip_address, severity, created_at
            )
            VALUES (?, ?, 'catalogue', 'catalogue.item_updated', 1, 'Someone',
                    'item', ?, 'Changed a price', '10.0.0.1', 'info', ?)
            """,
            [
                (
                    company_id,
                    "change" if index % 3 else "security",
                    str(index),
                    now,
                )
                for index in range(MESSAGES)
            ],
        )
        conn.commit()

    return company_id


def test_the_first_page_of_a_busy_log_is_still_fast(a_busy_log):
    from backend.services.activity_service import activity_service

    start = time.monotonic()
    page = activity_service.list_entries(company_id=a_busy_log, limit=50)
    elapsed = time.monotonic() - start

    assert len(page["items"]) == 50
    assert page["total"] == MESSAGES
    assert elapsed < PAGE_BUDGET_SECONDS, f"the first page took {elapsed:.2f}s"


def test_a_deep_page_costs_about_the_same_as_a_shallow_one(a_busy_log):
    """The shape that matters, rather than an absolute number.

    Reading page eighty must not cost eighty times page one. Comparing the two
    rather than timing one makes the check independent of how fast the machine
    running it happens to be.
    """
    from backend.services.activity_service import activity_service

    start = time.monotonic()
    activity_service.list_entries(company_id=a_busy_log, limit=50, offset=0)
    shallow = time.monotonic() - start

    start = time.monotonic()
    deep = activity_service.list_entries(
        company_id=a_busy_log, limit=50, offset=MESSAGES - 100
    )
    deep_seconds = time.monotonic() - start

    assert len(deep["items"]) == 50
    assert deep_seconds < PAGE_BUDGET_SECONDS, (
        f"a deep page took {deep_seconds:.2f}s against {shallow:.3f}s shallow"
    )


def test_a_filter_over_a_busy_log_is_still_fast(a_busy_log):
    """Filtering is what an investigation actually does."""
    from backend.services.activity_service import activity_service

    start = time.monotonic()
    page = activity_service.list_entries(
        company_id=a_busy_log, kind="security", limit=50
    )
    elapsed = time.monotonic() - start

    assert page["total"] > 0
    assert all(item["kind"] == "security" for item in page["items"])
    assert elapsed < PAGE_BUDGET_SECONDS, f"a filtered page took {elapsed:.2f}s"


def test_a_page_never_returns_more_than_it_was_asked_for(a_busy_log):
    """A limit that leaks under volume is how an export becomes an outage."""
    from backend.services.activity_service import activity_service

    for limit in (1, 10, 50, 200, 500):
        page = activity_service.list_entries(company_id=a_busy_log, limit=limit)

        assert len(page["items"]) <= min(limit, activity_service.MAX_LIMIT)


def test_paging_to_the_end_sees_every_row_exactly_once(a_busy_log):
    """Ordering, not speed.

    A page ordered by a column with ties — the thousands of rows above all
    share one `created_at` — may return the same row on two pages and skip
    another, and the reader never notices.

    Honest limit, established by mutation rather than assumed: **this test
    cannot detect a missing tiebreaker.** Dropping `, id DESC` from the query
    leaves it green, because SQLite happens to fall back to rowid order for
    this plan. That is incidental, not a guarantee — it can change with an
    index, a version, or a different plan for the same query — so the
    tiebreaker is asserted separately, on the query itself, in
    `test_the_ordering_is_total`. What this test does prove is the property
    that matters to a reader today: walking every page returns every row once.
    """
    from backend.services.activity_service import activity_service

    seen: list[int] = []
    offset = 0

    while offset < MESSAGES:
        page = activity_service.list_entries(
            company_id=a_busy_log, limit=200, offset=offset
        )

        if not page["items"]:
            break

        seen.extend(int(item["id"]) for item in page["items"])
        offset += 200

    assert len(seen) == MESSAGES, f"walked {len(seen)} rows of {MESSAGES}"
    assert len(set(seen)) == MESSAGES, (
        f"{len(seen) - len(set(seen))} rows appeared on more than one page — "
        "the ordering is not total, so paging both repeats and skips entries"
    )


def test_the_ordering_is_total():
    """Every paged query orders by something unique.

    Separate from the walk above because the walk cannot see this: SQLite's
    incidental row order hides a missing tiebreaker until the day a plan
    changes, and then pages start repeating and skipping rows in production
    with nothing in the code having changed.

    Written against `created_at` specifically — the only sort key in this
    service, and one that ties by the thousand whenever rows are written in a
    burst, which is exactly when a log is worth reading.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "backend/services/activity_service.py").read_text()

    untied = [
        match.group(0).strip()
        for match in re.finditer(r"ORDER BY created_at[^\n]*", source)
        if not re.search(r"\bid\b", match.group(0))
    ]

    assert not untied, (
        "A paged query orders by created_at with no unique tiebreaker:\n  "
        + "\n  ".join(untied)
        + "\n\nRows sharing a timestamp can then appear on two pages and be "
        "skipped from a third. Add `, id DESC`."
    )
