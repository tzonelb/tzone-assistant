import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture()
def db_with_user():
    from database.database import db
    from backend.services.auth_service import auth_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()

    password_hash = auth_service.hash_password("CorrectHorse123!")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (email, full_name, status, is_super_admin, password_hash) "
            "VALUES ('locktest@test.local', 'Lock Test', 'active', 0, ?)",
            (password_hash,),
        )
        user_id = conn.execute("SELECT id FROM users WHERE email = 'locktest@test.local'").fetchone()["id"]
        conn.execute("INSERT INTO company_users (company_id, user_id, status) VALUES (1, ?, 'active')", (user_id,))
        conn.commit()

    yield auth_service, user_id

    db.db_path = original_db_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_correct_password_logs_in(db_with_user):
    auth_service, _ = db_with_user
    user = auth_service.authenticate(email="locktest@test.local", password="CorrectHorse123!", company="tzone-lb")
    assert user is not None


def test_wrong_password_fails_without_locking_immediately(db_with_user):
    auth_service, _ = db_with_user
    assert auth_service.authenticate(email="locktest@test.local", password="wrong", company="tzone-lb") is None
    # Still under the threshold — correct password should still work.
    assert auth_service.authenticate(email="locktest@test.local", password="CorrectHorse123!", company="tzone-lb") is not None


def test_account_locks_after_max_failed_attempts(db_with_user):
    auth_service, _ = db_with_user
    for _ in range(auth_service.MAX_FAILED_LOGIN_ATTEMPTS):
        auth_service.authenticate(email="locktest@test.local", password="wrong", company="tzone-lb")

    # Even the CORRECT password is rejected while locked.
    assert auth_service.authenticate(email="locktest@test.local", password="CorrectHorse123!", company="tzone-lb") is None


def test_successful_login_resets_failed_attempts(db_with_user):
    from database.database import db
    auth_service, user_id = db_with_user

    auth_service.authenticate(email="locktest@test.local", password="wrong", company="tzone-lb")
    auth_service.authenticate(email="locktest@test.local", password="wrong", company="tzone-lb")
    auth_service.authenticate(email="locktest@test.local", password="CorrectHorse123!", company="tzone-lb")

    with db.connect() as conn:
        row = conn.execute("SELECT failed_login_attempts FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row["failed_login_attempts"] == 0


def test_lockout_clears_after_expiry(db_with_user):
    from database.database import db
    from datetime import datetime, timedelta, timezone
    auth_service, user_id = db_with_user

    for _ in range(auth_service.MAX_FAILED_LOGIN_ATTEMPTS):
        auth_service.authenticate(email="locktest@test.local", password="wrong", company="tzone-lb")

    # Simulate the lockout window having already passed.
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE users SET locked_until = ? WHERE id = ?", (past, user_id))
        conn.commit()

    assert auth_service.authenticate(email="locktest@test.local", password="CorrectHorse123!", company="tzone-lb") is not None
