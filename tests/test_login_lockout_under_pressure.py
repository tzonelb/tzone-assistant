"""The lock has to hold when the guesses arrive together, not in turn.

A lockout is a counter and a decision, and the two are separate statements.
Every test that exists for it so far makes five attempts one after another,
which is the case where a counter cannot be wrong. The case worth checking is
twenty guesses arriving in the same millisecond from twenty connections — a
credential-stuffing tool does not wait politely for a response before sending
the next request, and that is the traffic the lock exists for.

Two properties, and they pull in opposite directions, which is why both are
asserted here:

* **The lock must not be walked past.** If every request reads the failure
  count before any of them writes, all twenty pass the gate and the lock lands
  after the attacker has already had twenty free guesses.
* **The lock must not be *caused* by the crowd.** Locking is a real cost to a
  real employee, so the count must be of actual failures, not of a number
  inflated by rows written twice.
"""

from __future__ import annotations

import sys
import threading

import pytest


EMAIL = "target@alpha.test"


@pytest.fixture()
def auth(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    import backend.services.auth_service  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    assert (
        getattr(sys.modules["backend.services.auth_service"], "database_manager", None)
        is test_manager
    ), "auth_service is not talking to the test database"

    from backend.services.auth_service import auth_service

    auth_service.create_user(
        email=EMAIL,
        password="a-long-enough-password",
        full_name="Target Employee",
    )

    return auth_service


def _in_parallel(work, count):
    barrier = threading.Barrier(count)
    results: list = [None] * count
    errors: list = [None] * count

    def runner(index):
        try:
            barrier.wait(timeout=30)
            results[index] = work(index)
        except Exception as exc:  # noqa: BLE001
            errors[index] = exc

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=60)

    return results, errors


def _guess(auth, index):
    """One failed sign-in, the way the route does it: record, then decide."""
    auth.record_login_attempt(
        email=EMAIL, ip_address=f"10.0.0.{index}", succeeded=False
    )

    return auth.register_failure(email=EMAIL, ip_address=f"10.0.0.{index}")


def test_a_burst_of_guesses_still_locks_the_account(auth, platform):
    """The gate must close even when nothing arrives in order."""
    from config.settings import config

    count = max(20, config.LOGIN_MAX_ATTEMPTS * 4)

    _, errors = _in_parallel(lambda i: _guess(auth, i), count)

    assert not any(errors), f"a sign-in attempt raised: {[e for e in errors if e]}"

    lock = auth.account_lock(EMAIL)

    assert lock is not None, (
        f"{count} simultaneous failed sign-ins left the account unlocked — "
        "the counter and the decision are not seeing the same rows"
    )


def test_every_failure_is_counted_exactly_once(auth, platform):
    """The other direction.

    If concurrent writes dropped rows, the lock would need more guesses than
    it claims; if they duplicated them, an employee mistyping a password twice
    while two tabs retry could lock themselves out. The count is asserted
    exactly, not as a bound.
    """
    count = 20

    _in_parallel(lambda i: _guess(auth, i), count)

    with platform["manager"].control() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM login_attempts"
            " WHERE email = ? AND succeeded = 0",
            (auth.normalize_email(EMAIL),),
        ).fetchone()["n"]

    assert int(rows) == count, f"{count} failures were recorded as {rows}"


def test_the_lock_is_not_reported_before_the_threshold(auth, platform):
    """What stops this file passing on a service that locks unconditionally.

    One under the limit, sent all at once, must leave the account usable.
    """
    from config.settings import config

    _, errors = _in_parallel(
        lambda i: _guess(auth, i), config.LOGIN_MAX_ATTEMPTS - 1
    )

    assert not any(errors)
    assert auth.account_lock(EMAIL) is None, (
        "the account locked before its own threshold — a burst of ordinary "
        "typos would disable an employee"
    )


def test_unlocking_survives_the_burst_that_caused_it(auth, platform):
    """A lock an administrator cannot clear is a disabled employee.

    The unlock has to clear the failures as well as the flag: leaving the rows
    behind would re-lock the account on the next mistyped password, which reads
    to everyone involved as the unlock not having worked.
    """
    _in_parallel(lambda i: _guess(auth, i), 20)

    assert auth.account_lock(EMAIL) is not None

    with platform["manager"].control() as conn:
        user_id = int(
            conn.execute(
                "SELECT id FROM users WHERE email = ?",
                (auth.normalize_email(EMAIL),),
            ).fetchone()["id"]
        )

    assert auth.unlock_account(user_id=user_id) is True
    assert auth.account_lock(EMAIL) is None

    # And it stays unlocked: one more typo must not re-lock it instantly.
    _guess(auth, 99)

    assert auth.account_lock(EMAIL) is None, (
        "the account re-locked on a single attempt — unlock cleared the flag "
        "but not the failures behind it"
    )
