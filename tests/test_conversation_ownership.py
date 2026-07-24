"""
Real automated tests for the conversation ownership / handover logic
(the core described as "Patch 9.1" in docs/PATCH_9_STATUS.md).

No test suite existed in the repo for this logic before this file —
docs/PATCH_9_STATUS.md claimed tests were "reported as passed" on a
separate artifact that does not exist anywhere in this repository's
git history. This file verifies the actual committed code directly.

Run with: DATABASE_PATH=/tmp/tzone_test.db python3 -m pytest tests/ -v
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    (Reimporting modules per-test via sys.modules deletion looked cleaner
    but breaks under pytest's assertion-rewrite import hook — it left a
    stale schema behind. Mutating the existing singleton's db_path is the
    reliable way to isolate tests against this codebase's module layout.)
    """
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    # assigned_user_id is a real FK to users(id); seed the three employee
    # ids used across these tests (regular employee x2, admin-ish x1).
    with db.connect() as conn:
        for uid, email in ((101, "emp1@test.local"), (202, "emp2@test.local"), (999, "admin@test.local")):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, full_name, status) VALUES (?, ?, ?, 'active')",
                (uid, email, email),
            )
        conn.commit()

    yield conversation_control_service

    db.db_path = original_path

    # On Windows, sqlite3 connections opened via `with db.connect() as conn:`
    # aren't fully closed just by exiting the `with` block (that only
    # commits/rolls back) — the file can stay locked briefly, which makes
    # os.remove() raise PermissionError even though every test assertion
    # already passed. Cleanup failing here is not a correctness problem,
    # so don't let it fail the run — just try a bit harder, then give up
    # quietly and leave the temp file for the OS to clean up later.
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


COMPANY_ID = 1
CHANNEL = "messenger"
CUSTOMER_ID = "test_customer_1"


def test_first_takeover_succeeds(fresh_db):
    svc = fresh_db
    result = svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    assert result["assigned_user_id"] == 101
    assert result["handled_by_ai"] is False


def test_second_employee_takeover_raises_conflict(fresh_db):
    """This is the actual bug Patch 9.1 claims to fix: two employees
    grabbing the same conversation must not silently overwrite."""
    svc = fresh_db
    from backend.services.conversation_control_service import ConversationOwnershipConflict

    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    with pytest.raises(ConversationOwnershipConflict) as exc_info:
        svc.set_ai_mode(
            company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
            handled_by_ai=False, actor_user_id=202,
        )
    assert exc_info.value.owner_user_id == 101


def test_owner_can_release_then_second_employee_can_take_over(fresh_db):
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    svc.release(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
                actor_user_id=101, force=False)
    result = svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=202,
    )
    assert result["assigned_user_id"] == 202


def test_non_owner_cannot_release(fresh_db):
    svc = fresh_db
    from backend.services.conversation_control_service import ConversationOwnershipConflict

    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    with pytest.raises(ConversationOwnershipConflict):
        svc.release(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
                    actor_user_id=202, force=False)


def test_admin_can_force_release_another_employees_conversation(fresh_db):
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    result = svc.release(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
                          actor_user_id=999, force=True)
    assert result["assigned_user_id"] is None


def test_return_to_ai_after_takeover(fresh_db):
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    result = svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=True, actor_user_id=101,
    )
    assert result["handled_by_ai"] is True


def test_release_starts_a_timeout_instead_of_returning_to_ai_immediately(fresh_db):
    """Business rule: Release means 'open to any employee for N minutes',
    not an immediate hand-back to AI. It should NOT clear
    takeover_expires_at anymore — it should (re)start it, so the existing
    background expiry worker can return the conversation to AI later if
    nobody claims it."""
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    result = svc.release(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
                          actor_user_id=101, force=False)
    assert result["assigned_user_id"] is None
    assert result["handled_by_ai"] is False
    assert result["takeover_expires_at"] is not None


def test_released_conversation_auto_returns_to_ai_after_timeout(fresh_db):
    """The actual end-to-end behavior requested: nobody claims a released
    conversation within the timeout window -> it goes back to AI on its
    own, via the same worker that already expires abandoned takeovers."""
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    svc.release(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
                actor_user_id=101, force=False)

    # Simulate the 5-minute window having already elapsed.
    from database.database import db
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE conversations SET takeover_expires_at = ? "
            "WHERE company_id = ?",
            (past, COMPANY_ID),
        )
        conn.commit()

    expired = svc.expire_overdue_takeovers()
    assert expired == 1

    final_state = svc.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert final_state["handled_by_ai"] is True
    assert final_state["assigned_user_id"] is None


def test_second_employee_can_still_claim_a_released_conversation_before_timeout(fresh_db):
    """Releasing must not block other employees from taking it over — the
    timeout is a fallback for when nobody does, not an exclusive lock."""
    svc = fresh_db
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )
    svc.release(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
                actor_user_id=101, force=False)

    result = svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=202,
    )
    assert result["assigned_user_id"] == 202
