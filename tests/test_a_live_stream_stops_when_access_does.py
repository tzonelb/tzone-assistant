"""Revoking access has to stop the data that is already flowing.

Every other route is checked once, answers, and is gone. The two live-event
endpoints hold the connection open and push the company's inbox or its team
chat every time either changes — for as long as the browser stays on the
screen. A dependency runs at connection time, so all three guards were answered
once, for a screen that then stayed open for hours.

Measured before the fix, on a stream already proven to be pushing: suspending
the company and revoking every one of the employee's sessions each left it
pushing the next change anyway. `set_company_status` does revoke sessions, and
it made no difference, because nothing in the loop looked at the session again.

`stream_access.may_continue` is now asked once per pass. It fails closed, which
is the opposite of the gates it consults — deliberately: refusing here costs
one already-open stream that the browser reopens immediately, and reopening
runs the real dependencies.
"""

from __future__ import annotations

import pytest

from database.manager import utc_now_iso

# Imported at module scope, before any fixture patches `database_manager`. A
# module first imported during an active monkeypatch keeps that test's manager
# for the life of the process; see the note in
# `tests/test_a_departed_colleague_keeps_their_name.py`.
from backend.services.auth_service import auth_service  # noqa: E402
from backend.services.company_gate import company_gate  # noqa: E402
from backend.services.stream_access import may_continue  # noqa: E402
from backend.services.subscription_gate import subscription_gate  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_gates():
    for gate in (company_gate, subscription_gate):
        gate.invalidate()
    yield
    for gate in (company_gate, subscription_gate):
        gate.invalidate()


@pytest.fixture()
def signed_in(platform, alpha, monkeypatch):
    """A real employee with a real token, wired to this test's manager."""
    import sys

    from database.manager import DatabaseManager

    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    user_id = auth_service.create_user(
        email="watcher@alpha.example.com",
        password="WatcherPass123!",
        full_name="Watcher",
    )

    with test_manager.control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner' LIMIT 1",
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

    session = auth_service.create_session(user_id=user_id, company_id=alpha["id"])
    raw = session["access_token"] if isinstance(session, dict) else session

    user = auth_service.get_user_from_token(raw)
    assert user, "the fixture could not sign anybody in"
    user["_raw_token"] = raw

    return {"user": user, "user_id": user_id, "manager": test_manager}


def test_a_live_session_may_continue(signed_in):
    """The control. Every test below asserts a refusal, which proves nothing
    unless an untouched session is allowed."""
    assert may_continue(signed_in["user"]) is True


def test_it_stops_when_the_session_is_revoked(signed_in):
    auth_service.revoke_all_user_sessions(signed_in["user_id"])

    assert may_continue(signed_in["user"]) is False, (
        "an open stream kept its entitlement after every session for that "
        "employee was revoked"
    )


def test_it_stops_when_the_company_is_suspended(signed_in, platform, alpha):
    with signed_in["manager"].control() as conn:
        conn.execute(
            "UPDATE companies SET status = 'suspended' WHERE id = ?", (alpha["id"],)
        )
        conn.commit()

    company_gate.invalidate(alpha["id"])

    assert may_continue(signed_in["user"]) is False, (
        "an open dashboard kept streaming the company's inbox after an "
        "operator suspended it"
    )


def test_it_stops_when_the_membership_is_disabled(signed_in, platform, alpha):
    """A departed employee's open tab is the case this is really for."""
    with signed_in["manager"].control() as conn:
        conn.execute(
            "UPDATE company_users SET status = 'disabled' WHERE user_id = ?",
            (signed_in["user_id"],),
        )
        conn.commit()

    assert may_continue(signed_in["user"]) is False


def test_it_stops_when_the_subscription_has_lapsed(signed_in, platform, alpha):
    from datetime import datetime, timedelta, timezone

    now = utc_now_iso()
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    with signed_in["manager"].control() as conn:
        plan = conn.execute("SELECT id FROM plans LIMIT 1").fetchone()
        assert plan, "the platform has no plans to subscribe to"

        conn.execute(
            "UPDATE subscriptions SET status = 'replaced' WHERE company_id = ?",
            (alpha["id"],),
        )
        conn.execute(
            """
            INSERT INTO subscriptions (
                company_id, plan_id, status, starts_at, expires_at,
                grace_period_until, auto_renew, created_at, updated_at
            )
            VALUES (?, ?, 'active', ?, ?, NULL, 0, ?, ?)
            """,
            (alpha["id"], int(plan["id"]), now, expired, now, now),
        )
        conn.commit()

    subscription_gate.invalidate(alpha["id"])

    assert may_continue(signed_in["user"]) is False, (
        "every screen answers 402 once the bill lapses, but an already-open "
        "stream kept delivering the inbox"
    )


def test_a_caller_with_no_token_is_refused(signed_in):
    """Nothing to re-check is not a reason to wave a running stream through."""
    without = dict(signed_in["user"])
    without.pop("_raw_token", None)

    assert may_continue(without) is False


def test_it_fails_closed_when_the_check_itself_breaks(signed_in, monkeypatch):
    """The opposite choice from the gates, and the module says why."""
    def explode(*args, **kwargs):
        raise RuntimeError("the control plane is unreachable")

    monkeypatch.setattr(auth_service, "get_user_from_token", explode)

    assert may_continue(signed_in["user"]) is False


@pytest.mark.parametrize(
    "module_name, attribute",
    [
        ("backend.api.routes.conversations", "live_conversation_events"),
        ("backend.api.routes.team_chat", "live_team_chat_events"),
    ],
)
def test_both_live_streams_really_close(
    module_name, attribute, signed_in, platform, alpha
):
    """Behaviour is worth nothing if a stream never asks.

    Driven by pulling the endpoint's async generator directly. A test client
    that has to hold an infinite SSE response open is the wrong instrument --
    the first attempt at this could not keep its own control stream alive,
    which made every "nothing arrived" result meaningless.

    An earlier version of this test read the endpoint's source for the string
    `may_continue`. It passed against a deliberately broken
    `if False and ... may_continue(...)`, which is exactly the mutation it
    existed to catch, so it is now driven instead of read.
    """
    import asyncio
    import importlib

    module = importlib.import_module(module_name)
    endpoint = getattr(module, attribute)

    # No waiting between passes; one `__anext__` is one pass of the loop.
    monkey_poll = getattr(module, "LIVE_POLL_SECONDS", None)
    assert monkey_poll is not None, f"{module_name} has no LIVE_POLL_SECONDS"
    module.LIVE_POLL_SECONDS = 0

    # Called directly rather than through a router, so any parameter whose
    # default is a `Query(...)` marker has to be supplied by hand — otherwise
    # the marker object itself arrives where an int is expected.
    import inspect

    extra = {}
    for name, parameter in inspect.signature(endpoint).parameters.items():
        if name in ("current_user",):
            continue
        extra[name] = None

    async def drive():
        response = await endpoint(current_user=signed_in["user"], **extra)
        stream = response.body_iterator

        # One pass while everything is fine, to prove the stream runs at all.
        first = await asyncio.wait_for(stream.__anext__(), timeout=20)

        auth_service.revoke_all_user_sessions(signed_in["user_id"])

        frames = []
        for _ in range(3):
            try:
                frames.append(await asyncio.wait_for(stream.__anext__(), timeout=20))
            except StopAsyncIteration:
                break

        await stream.aclose()

        return first, frames

    try:
        first, frames = asyncio.run(drive())
    finally:
        module.LIVE_POLL_SECONDS = monkey_poll

    assert first is not None, "the stream produced nothing at all"

    joined = "".join(frames)

    assert "access_ended" in joined or not frames, (
        f"{attribute} kept streaming after every session was revoked: "
        f"{joined[:200]!r}"
    )
    assert "conversations_updated" not in joined, (
        f"{attribute} pushed inbox data after access was revoked"
    )
    assert "team_chat_updated" not in joined, (
        f"{attribute} pushed team chat data after access was revoked"
    )
