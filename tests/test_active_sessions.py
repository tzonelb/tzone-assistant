""""Where am I signed in" — list live sessions, and revoke them safely.

The two properties that matter: a person sees their own live sessions and never
the token behind them, and one person can never revoke another's session even
by guessing an id. Both are asserted here at the service level, where the
tenant/account boundary lives.
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module
from database.manager import utc_now_iso


@pytest.fixture()
def bound(platform, monkeypatch):
    original = manager_module.database_manager
    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
    import backend.services.auth_service as auth_module
    monkeypatch.setattr(auth_module, "database_manager", test_manager)
    return test_manager


def _user(email):
    from backend.services.auth_service import auth_service

    return auth_service.create_user(
        email=email, password="a-strong-password-123", full_name="Test"
    )


def test_sessions_are_listed_without_the_token(bound):
    from backend.services.auth_service import auth_service

    uid = _user("s1@example.com")
    a = auth_service.create_session(uid, ip_address="1.1.1.1", user_agent="Chrome")
    auth_service.create_session(uid, ip_address="2.2.2.2", user_agent="Safari")

    listed = auth_service.list_user_sessions(uid, current_token=a["access_token"])

    assert len(listed) == 2
    assert all("token_hash" not in s and "access_token" not in s for s in listed)
    # Exactly the session whose token we passed is flagged current.
    assert sum(1 for s in listed if s["current"]) == 1
    current = next(s for s in listed if s["current"])
    assert current["ip_address"] == "1.1.1.1"


def test_revoking_one_session_removes_it(bound):
    from backend.services.auth_service import auth_service

    uid = _user("s2@example.com")
    keep = auth_service.create_session(uid)
    drop = auth_service.create_session(uid)

    listed = auth_service.list_user_sessions(uid, current_token=keep["access_token"])
    drop_id = next(s["id"] for s in listed if not s["current"])

    assert auth_service.revoke_session(user_id=uid, session_id=drop_id) is True
    remaining = auth_service.list_user_sessions(uid, current_token=keep["access_token"])
    assert len(remaining) == 1
    assert remaining[0]["current"] is True
    # The revoked token no longer authenticates.
    assert auth_service.get_user_from_token(drop["access_token"]) is None


def test_one_user_cannot_revoke_anothers_session(bound):
    from backend.services.auth_service import auth_service

    victim = _user("victim@example.com")
    attacker = _user("attacker@example.com")
    victim_session = auth_service.create_session(victim)

    victim_list = auth_service.list_user_sessions(
        victim, current_token=victim_session["access_token"]
    )
    victim_session_id = victim_list[0]["id"]

    # The attacker names the victim's session id, but it is not theirs.
    assert (
        auth_service.revoke_session(user_id=attacker, session_id=victim_session_id)
        is False
    )
    # The victim's session is untouched.
    assert auth_service.get_user_from_token(victim_session["access_token"]) is not None


def test_revoke_others_keeps_the_current_one(bound):
    from backend.services.auth_service import auth_service

    uid = _user("s3@example.com")
    current = auth_service.create_session(uid)
    auth_service.create_session(uid)
    auth_service.create_session(uid)

    count = auth_service.revoke_other_sessions(
        user_id=uid, current_token=current["access_token"]
    )
    assert count == 2

    remaining = auth_service.list_user_sessions(uid, current_token=current["access_token"])
    assert len(remaining) == 1
    assert remaining[0]["current"] is True


def _raw_last_used_at(manager, session_id: int) -> str:
    with manager.control() as conn:
        row = conn.execute(
            "SELECT last_used_at FROM auth_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row["last_used_at"]


def test_last_used_at_is_not_rewritten_on_every_call(bound):
    """`get_current_user` runs on every protected request -- through
    `require_permission` on literally every route that needs one. Writing
    `last_used_at` on every single call made that the write-heaviest thing on
    the control database, contending with every other writer on the platform
    for no reason a settings screen ("last active 2 minutes ago") needs at
    request granularity. Reproduced live: under a ~330-worker load test,
    dozens of worker threads piled up waiting on exactly this UPDATE, and once
    the pool that serves it filled, `/health/` -- a route with no database
    call at all -- stopped answering too, because it needs a thread from the
    same pool.

    A session created seconds ago calling this repeatedly must not move
    `last_used_at` again until roughly a minute has passed.
    """
    from backend.services.auth_service import auth_service

    uid = _user("throttle@example.com")
    session = auth_service.create_session(uid)
    token = session["access_token"]

    listed = auth_service.list_user_sessions(uid, current_token=token)
    session_id = listed[0]["id"]

    first = _raw_last_used_at(bound, session_id)

    for _ in range(5):
        assert auth_service.get_user_from_token(token) is not None

    assert _raw_last_used_at(bound, session_id) == first, (
        "last_used_at moved on a call seconds after the session was created "
        "-- the throttle in get_user_from_token is not holding."
    )


def test_last_used_at_does_move_once_it_is_stale(bound):
    """The other half of the throttle: a session really does get its
    last-active time refreshed, just not on every single request."""
    from datetime import timedelta

    from backend.services.auth_service import auth_service, utc_now

    uid = _user("throttle-stale@example.com")
    session = auth_service.create_session(uid)
    token = session["access_token"]

    listed = auth_service.list_user_sessions(uid, current_token=token)
    session_id = listed[0]["id"]

    stale = (utc_now() - timedelta(minutes=5)).isoformat()
    with bound.control() as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_used_at = ? WHERE id = ?",
            (stale, session_id),
        )
        conn.commit()

    assert auth_service.get_user_from_token(token) is not None

    refreshed = _raw_last_used_at(bound, session_id)
    assert refreshed != stale, (
        "last_used_at never moved even though it was five minutes stale"
    )
