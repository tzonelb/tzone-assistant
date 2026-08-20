"""How long a refusal takes must not say why it was refused.

A login endpoint answers "no" to a wrong password and "no" to an address that
belongs to nobody. If the second is measurably faster, the two noes are
different answers, and anybody can ask the form which of ten thousand addresses
have accounts here. That is a customer list — for a platform whose customers
are businesses, it is a competitor's prospect list — extracted from a public
endpoint with no credentials at all.

`auth_service._dummy_password_check` exists for this: an unknown address burns
one PBKDF2 round so it costs the same as verifying a real one. It is four lines
that look pointless, sit on the failure path where nothing visibly depends on
them, and would survive any amount of review by somebody tidying up. Deleting
them changes no test, breaks no screen, and turns the sign-in form into a
directory.

So the property is measured rather than trusted. Timing tests are noisy, so the
assertion is on a *ratio* of medians over many samples with a wide band: the
difference this catches is not subtle. Removing the equaliser makes an unknown
address roughly a hundred times faster, because it skips 310,000 hash
iterations.

**A leak that is deliberately left, and why.** The branches *after* a correct
password are not equalised, and cannot be cheaply: unsealing the workspace code
runs 600,000 KDF iterations against the password's 310,000, so a wrong code is
about three times slower than a wrong password. That tells somebody who already
holds valid credentials that the company or the code was what stopped them.
Equalising it would mean running the 600k unseal on every rejected attempt,
which hands every anonymous caller a CPU-exhaustion lever. The leak is bounded
to someone who already has a working password; the amplification would be
available to everyone. That trade is recorded in `authenticate` and asserted
below so it stays a decision rather than drifting into an accident.
"""

from __future__ import annotations

import statistics
import sys
import time

import pytest


PASSWORD = "RealPass123!"

SAMPLES = 15

# Wide on purpose. The equalised case measures within a few percent on a quiet
# machine; removing the equaliser puts it around 0.01. Anything inside this
# band is noise, anything outside it is a missing PBKDF2 round.
EQUAL_ENOUGH = (0.45, 2.2)


@pytest.fixture()
def wired(platform, alpha, monkeypatch):
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
    )

    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="real@alpha.example.com", password=PASSWORD, full_name="Real Employee"
    )

    with test_manager.control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (alpha["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (alpha["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    return auth_service


def _median_ms(call, samples=SAMPLES):
    timings = []

    for _ in range(samples):
        started = time.perf_counter()
        call()
        timings.append((time.perf_counter() - started) * 1000)

    return statistics.median(timings)


def _attempt(auth, alpha, *, email, password, code=None):
    return auth.authenticate(
        email=email,
        password=password,
        company=alpha["name"],
        workspace_code=code if code is not None else alpha["workspace_code"],
    )


def test_an_unknown_address_costs_the_same_as_a_wrong_password(wired, alpha):
    """The property that matters, and the one that is cheap to lose."""
    unknown = _median_ms(
        lambda: _attempt(
            wired, alpha, email="nobody@alpha.example.com", password=PASSWORD
        )
    )
    wrong_password = _median_ms(
        lambda: _attempt(
            wired, alpha, email="real@alpha.example.com", password="WrongPass123!"
        )
    )

    ratio = unknown / wrong_password
    low, high = EQUAL_ENOUGH

    assert low <= ratio <= high, (
        f"an unknown address answers in {unknown:.0f} ms and a wrong password "
        f"in {wrong_password:.0f} ms — a ratio of {ratio:.3f}. The sign-in form "
        "can be used to find out which addresses have accounts on this "
        "platform. See `_dummy_password_check`."
    )


def test_both_refusals_are_actually_refusals(wired, alpha):
    """What stops the test above passing because both calls fail instantly for
    some unrelated reason — a broken fixture, a renamed argument."""
    assert (
        _attempt(wired, alpha, email="nobody@alpha.example.com", password=PASSWORD)
        is None
    )
    assert (
        _attempt(wired, alpha, email="real@alpha.example.com", password="WrongPass123!")
        is None
    )
    assert (
        _attempt(wired, alpha, email="real@alpha.example.com", password=PASSWORD)
        is not None
    ), "the correct credentials do not work, so neither refusal proves anything"


def test_the_equalising_work_is_real_work(wired, alpha):
    """A refusal that returns in microseconds has not hashed anything.

    Guards the case where `_dummy_password_check` is still called but has been
    weakened — a lower iteration count, a cached result — which would keep the
    ratio near one while making both paths fast enough to grind through.
    """
    unknown = _median_ms(
        lambda: _attempt(
            wired, alpha, email="nobody@alpha.example.com", password=PASSWORD
        )
    )

    assert unknown > 20, (
        f"a refused sign-in takes {unknown:.1f} ms. Password verification is "
        "310,000 PBKDF2 iterations and cannot be that fast, so either the "
        "iteration count has been lowered or the refusal is skipping the work."
    )


def test_a_wrong_workspace_code_is_slower_and_that_is_accepted(wired, alpha):
    """The leak that was deliberately left, pinned so it stays deliberate.

    If this ever *stops* being true, somebody has either equalised it — which
    hands anonymous callers a 600k-iteration CPU lever — or stopped unsealing
    on the login path, which would mean the workspace code is no longer being
    proved. Both are worth a failing test and a conversation.
    """
    wrong_password = _median_ms(
        lambda: _attempt(
            wired, alpha, email="real@alpha.example.com", password="WrongPass123!"
        )
    )
    wrong_code = _median_ms(
        lambda: _attempt(
            wired,
            alpha,
            email="real@alpha.example.com",
            password=PASSWORD,
            code="TZ-XXXX-XXXX-XXXX",
        ),
        samples=8,
    )

    assert wrong_code > wrong_password, (
        f"a wrong workspace code ({wrong_code:.0f} ms) is no longer slower than "
        f"a wrong password ({wrong_password:.0f} ms). Either the unseal was "
        "removed from the login path — so the code is no longer proved — or "
        "the branches were equalised by running the 600k unseal on every "
        "rejected attempt, which is a CPU-exhaustion lever for anonymous "
        "callers."
    )
