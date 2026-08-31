"""One company being busy must not slow down the rest of the platform.

This is the property the whole storage design exists for. Each company has its
own encrypted file, so a company writing hard should contend with nobody — and
the tests that assert isolation elsewhere assert it about *content*, which is
the other half. Nothing has asserted it about *time*.

Time matters here for a specific reason. There is one file every request
touches whoever it belongs to: the control database, which holds sessions,
users, companies and channel accounts. Anything that holds its write lock holds
up the entire platform, and this is not hypothetical — a plan-limit refusal
used to hold it for fifteen seconds, and one company hitting its limit stalled
every other company's sign-ins (see `test_audit_write_does_not_block_its_caller`).

So: one company writes as fast as it can, and another company's ordinary work
is timed while that happens.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest


NOISY_WRITES = 400

# A sign-in check is a handful of indexed reads. Anything close to a second
# means it is queueing behind somebody else's writes.
QUIET_BUDGET_SECONDS = 5.0


@pytest.fixture()
def wired(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    import backend.services.activity_service  # noqa: F401
    import backend.services.auth_service  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    for name in ("backend.services.activity_service", "backend.services.auth_service"):
        assert getattr(sys.modules[name], "database_manager", None) is test_manager

    return test_manager


def test_a_company_writing_hard_does_not_stall_another_companys_reads(
    wired, platform
):
    from backend.services.activity_service import activity_service

    alpha = platform["companies"]["alpha"]["id"]
    beta = platform["companies"]["beta"]["id"]

    stop = threading.Event()
    written = []

    def noisy():
        for index in range(NOISY_WRITES):
            if stop.is_set():
                return

            activity_service.record(
                company_id=alpha,
                action="catalogue.item_updated",
                category="catalogue",
                summary=f"change {index}",
            )
            written.append(index)

    worker = threading.Thread(target=noisy)
    worker.start()

    try:
        # Give the noisy company a head start so the measurement lands during
        # its load rather than before it.
        while len(written) < 20 and worker.is_alive():
            time.sleep(0.01)

        slowest = 0.0

        for _ in range(20):
            start = time.monotonic()
            activity_service.list_entries(company_id=beta, limit=20)
            slowest = max(slowest, time.monotonic() - start)
    finally:
        stop.set()
        worker.join(timeout=120)

    assert slowest < QUIET_BUDGET_SECONDS, (
        f"a quiet company's read took {slowest:.2f}s while another company was "
        "writing — the two are contending for something they should not share"
    )


def test_a_busy_company_does_not_stall_sign_ins(wired, platform):
    """The one shared file, and the request nobody can do without.

    A company writing to its own database must not slow down the control
    database, because a sign-in reads the control database and every person on
    the platform needs one.
    """
    from backend.services.activity_service import activity_service
    from backend.services.auth_service import auth_service

    alpha = platform["companies"]["alpha"]["id"]

    auth_service.create_user(
        email="quiet@beta.test",
        password="a-long-enough-password",
        full_name="Quiet Employee",
    )

    stop = threading.Event()
    written = []

    def noisy():
        for index in range(NOISY_WRITES):
            if stop.is_set():
                return

            activity_service.record(
                company_id=alpha,
                action="catalogue.item_updated",
                category="catalogue",
                summary=f"change {index}",
            )
            written.append(index)

    worker = threading.Thread(target=noisy)
    worker.start()

    try:
        while len(written) < 20 and worker.is_alive():
            time.sleep(0.01)

        slowest = 0.0

        for _ in range(10):
            start = time.monotonic()
            auth_service.login_gate(
                email="quiet@beta.test", ip_address="10.9.9.9"
            )
            slowest = max(slowest, time.monotonic() - start)
    finally:
        stop.set()
        worker.join(timeout=120)

    assert slowest < QUIET_BUDGET_SECONDS, (
        f"a sign-in check took {slowest:.2f}s while one company was writing to "
        "its own database"
    )


def test_the_noisy_company_actually_wrote(wired, platform):
    """What stops the two tests above passing because nothing happened.

    A `record` that silently failed would make every timing here excellent and
    every conclusion false.
    """
    from backend.services.activity_service import activity_service

    alpha = platform["companies"]["alpha"]["id"]

    for index in range(25):
        activity_service.record(
            company_id=alpha,
            action="catalogue.item_updated",
            category="catalogue",
            summary=f"change {index}",
        )

    page = activity_service.list_entries(company_id=alpha, limit=200)

    assert page["total"] >= 25, (
        f"only {page['total']} of 25 writes landed — the timing tests in this "
        "file were measuring an idle platform"
    )
