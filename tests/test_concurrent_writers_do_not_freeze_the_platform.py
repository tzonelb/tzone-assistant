"""A burst of writers contending for one row must not freeze every request.

Reproduced live against the running app, not imagined: open a handful of the
inbox's live-events streams, then fire thirty concurrent writes at the same
conversation row (two employees is realistic; thirty is a burst, a script, or
a retry storm). With the busy-timeout this platform shipped with — fifteen
seconds — that burst didn't just make those thirty writers slow. It made
`GET /login`, a request that touches no database at all, hang for minutes,
because Starlette serves it from the same process-wide thread pool every
blocking database call also uses, and thirty threads each waiting up to
fifteen seconds for the same SQLite write lock can exhaust it. The single
uvicorn worker this platform runs (deploy/tzone-api.service, deliberately —
see its own comment) means there is no second worker to pick up the slack
while that happens.

`BUSY_TIMEOUT_MS` is what bounds a blocked writer's worst case. This test
does not re-run the live reproduction (that needs a live server and real
concurrency at the socket level, not just threads in one process) — it pins
the property that made the live fix work: a burst of writers all contending
for the *same* row finishes within a small, predictable multiple of the
timeout, not an unbounded wait. If this constant ever creeps back up toward
what shipped before, this test is the tripwire.
"""

from __future__ import annotations

import threading
import time

from database.manager import BUSY_TIMEOUT_MS


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


def test_automatic_wal_checkpointing_is_disabled_on_every_connection(platform):
    """A second, worse freeze than the one this file's own name describes.

    Reproduced live against a real single-worker server: roughly 160
    concurrent writers plus held-open SSE connections against these two
    files did not just contend for BUSY_TIMEOUT_MS's write lock -- it wedged
    the process *permanently*. py-spy showed the event loop itself parked
    forever inside `connection.close()` (reached through
    `get_current_user` -> `get_user_from_token` -> `control()`), with every
    other thread spinning on the GIL futex and making no progress at all.
    Only `kill -9` recovered it; no timeout on this platform could have,
    because the thing that would have to fire the timeout -- the event loop
    -- was the thing that was stuck.

    With `wal_autocheckpoint` left at SQLite's default (1000 pages), a
    connection opened this often against a file this contended can trigger
    an automatic checkpoint on commit or close, competing with every other
    short-lived connection doing the same thing at the same moment. Turning
    it off here does not skip checkpointing -- see
    `backend.workers._checkpoint_all_databases`, which does it explicitly, on
    a clock, one connection at a time. Restarting the server after this fix
    and repeating the exact live load it froze on -- write burst, SSE holds,
    and concurrent CPU-heavy PDF/CSV exports together -- produced zero
    timeouts on `/health/` and a clean, empty connection table afterward.
    """
    with platform["manager"].control() as conn:
        value = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]

    assert value == 0, (
        f"PRAGMA wal_autocheckpoint is {value}, not 0. A connection opened "
        "under load may try to run an automatic checkpoint on commit or "
        "close -- see database/manager.py's `_open` for the live freeze this "
        "was reproduced against."
    )


def test_the_busy_timeout_is_bounded_low_enough_to_matter():
    """The regression this whole file exists to prevent, in one line.

    Fifteen seconds is what a real incident cost this platform once already
    (tests/test_audit_write_does_not_block_its_caller.py) on a single stalled
    write with no concurrency at all. A burst of concurrent writers multiplies
    that cost across every thread it occupies -- see the timing test below for
    the multiplied version of this same assertion.
    """
    assert BUSY_TIMEOUT_MS <= 5_000, (
        f"BUSY_TIMEOUT_MS is {BUSY_TIMEOUT_MS}ms. Every thread blocked on a "
        "contended write holds a slot in the one thread pool every other "
        "request -- on any company, for anything -- also depends on, on a "
        "platform that runs a single worker by design. A long timeout here "
        "is a long platform-wide freeze under a write burst, not just a slow "
        "write."
    )


def test_a_burst_of_writers_to_one_row_clears_in_bounded_time(platform):
    """Thirty threads, one row, same customer -- the exact shape reproduced
    live. Each writer opens its own connection the way a real request would;
    none of them coordinate. What matters is the *last* one finishes soon
    after the first, not eventually.
    """
    manager = platform["manager"]
    company_id = _alpha(platform)

    with manager.tenant(company_id) as conn:
        conn.execute(
            "INSERT INTO customers ("
            "   company_id, display_name, first_seen_at, last_seen_at,"
            "   created_at, updated_at"
            ") VALUES (?, 'Contended Customer', datetime('now'), datetime('now'),"
            "          datetime('now'), datetime('now'))",
            (company_id,),
        )
        customer_id = conn.execute(
            "SELECT id FROM customers WHERE company_id = ? ORDER BY id DESC LIMIT 1",
            (company_id,),
        ).fetchone()["id"]
        conn.commit()

    WRITERS = 30
    errors: list[BaseException] = []
    finished_at: list[float] = []
    start = time.monotonic()

    def _write(n: int) -> None:
        try:
            with manager.tenant(company_id) as conn:
                conn.execute(
                    "UPDATE customers SET notes = ? WHERE id = ? AND company_id = ?",
                    (f"touched by writer {n}", customer_id, company_id),
                )
                conn.commit()
        except BaseException as exc:  # noqa: BLE001
            # A writer that loses the race and fails fast is the traded-off
            # cost of this fix, not something this test forbids -- see the
            # module docstring. What it forbids is the *pool* never clearing.
            errors.append(exc)
        finally:
            finished_at.append(time.monotonic())

    threads = [threading.Thread(target=_write, args=(n,)) for n in range(WRITERS)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads), (
        "a writer thread never finished -- the burst did not clear"
    )

    total = max(finished_at) - start
    budget = (BUSY_TIMEOUT_MS / 1000) * 3

    assert total < budget, (
        f"{WRITERS} concurrent writers to one row took {total:.2f}s to all "
        f"clear (budget {budget:.2f}s at the current {BUSY_TIMEOUT_MS}ms "
        "timeout). That is the platform-freeze this file exists to catch."
    )

    with manager.tenant(company_id) as conn:
        row = conn.execute(
            "SELECT notes FROM customers WHERE id = ? AND company_id = ?",
            (customer_id, company_id),
        ).fetchone()

    assert row["notes"] is not None, "not one of the thirty writers ever won"
