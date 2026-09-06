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
