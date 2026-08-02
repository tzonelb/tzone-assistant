"""
Real test proving a manually-set business status ("resolved", "pending",
etc.) survives AI/human handoff transitions instead of being silently
clobbered back to "ai_handling"/"human_handling". Before this fix, an
agent marking a conversation "resolved" and then taking it over again
(or the AI picking it back up) would lose that status with no warning,
making the conversation unfindable under a "Resolved" filter.

Run with: python3 -m pytest tests/test_conversation_status_preserved.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
CHANNEL = "telegram"
CUSTOMER_ID = "status-preserve-test"
EMPLOYEE_ID = 101


@pytest.fixture()
def env():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) VALUES (?, 'emp@test.local', 'Employee', 'active')",
            (EMPLOYEE_ID,),
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name) VALUES (1, 'Test Co')")
        conn.commit()

    yield conversation_control_service

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


def test_resolved_status_survives_take_over(env):
    svc = env
    svc.get_or_create(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    svc.update_state(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        actor_user_id=EMPLOYEE_ID, status="resolved",
    )
    state = svc.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert state["status"] == "resolved"

    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=EMPLOYEE_ID,
    )
    state = svc.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert state["status"] == "resolved"
    assert state["handled_by_ai"] == 0  # the actual handling state still changed correctly


def test_resolved_status_survives_return_to_ai(env):
    svc = env
    svc.get_or_create(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=EMPLOYEE_ID,
    )
    svc.update_state(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        actor_user_id=EMPLOYEE_ID, status="pending",
    )

    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=True, actor_user_id=EMPLOYEE_ID,
    )
    state = svc.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert state["status"] == "pending"
    assert state["handled_by_ai"] == 1


def test_new_conversation_still_gets_a_real_status_by_default(env):
    """The fix must not break the normal case: a brand-new conversation
    with no manual status yet still ends up ai_handling/human_handling
    as before."""
    svc = env
    svc.get_or_create(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    svc.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=EMPLOYEE_ID,
    )
    state = svc.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert state["status"] == "human_handling"
