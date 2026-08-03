"""
Regression tests for the auto-vivification bypass of the cross-tenant
conversation read gate.

BACKGROUND: the original bug was that GET /api/conversations/{channel}/{user_id}
let any employee of any company read any other company's conversation
transcript (core/conversation_store.py has no company concept -- files are
keyed only by channel+user_id). The first fix attempt added
ConversationControlService.conversation_exists(company_id, channel,
external_user_id), a read-only company-scoped gate checked before returning
any transcript data.

That gate was then found to be trivially bypassable: ConversationControlService
.get_state()/.get_or_create() AUTO-VIVIFY (silently INSERT) a new
`conversations` row scoped to the CALLER's own company_id on first lookup,
and get_state() was called as an ordinary side effect by many other
employee-facing endpoints (read_control, return-to-ai, take-over, release,
manual-reply, and -- discovered during this repair round -- the
list_conversations / live SSE feed too). An attacker (any authenticated
employee) could call ANY of those endpoints against a victim company's known
channel+external_user_id FIRST to manufacture a same-shape "ownership" row
for their own company, which then made conversation_exists() incorrectly
return True, and the transcript leaked anyway.

THE FIX: ConversationControlService.get_state()/get_or_create() now accept
create_if_missing (default True, preserving every existing caller's
behavior). Every employee-initiated HTTP route that must not be able to
manufacture ownership of a conversation it doesn't already have now gates on
a read-only existence check (or passes create_if_missing=False) BEFORE any
auto-create side effect can run, and returns 404 if the row doesn't already
belong to the caller's company.

The three tests below are adapted directly from the reviewer's reproduced,
working proof-of-concept bypass scripts (which called the route functions
in-process against a real temp SQLite DB + temp flat-file conversation
store, exactly as done here) -- one per distinct attack endpoint:
  * poc_bypass_read_control.py    -> GET  .../control
  * poc_bypass_return_to_ai.py    -> POST .../return-to-ai
  * poc_bypass_test.py            -> POST .../reply (manual reply)

Additional endpoints (take-over, release) and the list_conversations /
live-events auto-vivification-on-every-poll issue found during this repair
round's full audit are covered by the tests that follow.

Run with: python3 -m pytest tests/test_conversation_auto_vivify_bypass.py -v
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Same isolation approach as test_conversation_cross_tenant_isolation.py:
    swap db.db_path and core.conversation_store.BASE_DIR to temp locations
    for the duration of the test, and seed a second tenant."""
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    import core.conversation_store as conversation_store
    from backend.api.routes import conversations as conversations_route

    tmp_db_path = tempfile.mktemp(suffix=".db")
    tmp_conversations_dir = Path(tempfile.mkdtemp(prefix="tzone_conv_store_"))

    original_db_path = db.db_path
    original_base_dir = conversation_store.BASE_DIR
    # NOTE: backend/api/routes/conversations.py's list_conversations /
    # live_conversation_events read from a SEPARATE hardcoded
    # CONVERSATIONS_DIR module constant (not core.conversation_store.BASE_DIR),
    # so it must be patched too or these tests would read the real repo's
    # data/conversations directory instead of this test's isolated one.
    original_conversations_route_dir = conversations_route.CONVERSATIONS_DIR

    db.db_path = Path(tmp_db_path)
    conversation_store.BASE_DIR = tmp_conversations_dir
    conversations_route.CONVERSATIONS_DIR = tmp_conversations_dir

    db.create_tables()  # seeds default workspace(id=1)/company(id=1)
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO companies (
                id, workspace_id, name, slug, country, currency,
                timezone, default_language, status
            )
            VALUES (2, 1, 'Rival Co', 'rival-co', 'Lebanon', 'USD',
                    'Asia/Beirut', 'ar', 'active')
            """
        )
        for uid, email in ((101, "empA@test.local"), (202, "empB@test.local")):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, full_name, status) "
                "VALUES (?, ?, ?, 'active')",
                (uid, email, email),
            )
        conn.execute(
            "INSERT INTO company_users (company_id, user_id, status) VALUES (1, 101, 'active')"
        )
        conn.execute(
            "INSERT INTO company_users (company_id, user_id, status) VALUES (2, 202, 'active')"
        )
        conn.commit()

    yield conversation_control_service

    db.db_path = original_db_path
    conversation_store.BASE_DIR = original_base_dir
    conversations_route.CONVERSATIONS_DIR = original_conversations_route_dir

    shutil.rmtree(tmp_conversations_dir, ignore_errors=True)

    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


COMPANY_A = 1
COMPANY_B = 2
EMPLOYEE_A = {"id": 101, "is_super_admin": False, "active_company_id": COMPANY_A}
EMPLOYEE_B = {"id": 202, "is_super_admin": False, "active_company_id": COMPANY_B}


def _seed_victim_conversation(svc, channel: str, victim_id: str, secret_text: str) -> None:
    """Seed a real Company B conversation: a company-scoped DB row (via the
    same get_or_create() path production code uses for a genuine inbound
    message) plus the raw, NOT company-scoped flat-file transcript."""
    from core.conversation_store import save_conversation_message

    svc.get_or_create(company_id=COMPANY_B, channel=channel, external_user_id=victim_id)
    save_conversation_message(channel=channel, user_id=victim_id, direction="inbound", text=secret_text)


# ---------------------------------------------------------------------------
# PoC 1: GET /{channel}/{user_id}/control  (poc_bypass_read_control.py)
# ---------------------------------------------------------------------------

def test_read_control_no_longer_auto_vivifies_ownership(fresh_db):
    svc = fresh_db
    channel = "telegram"
    victim_id = "999888777"
    secret_text = "Company B secret: bank routing number 021000021"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import conversations as conversations_route

    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False

    # Attack: Company A's employee calls read_control for Company B's victim.
    with pytest.raises(HTTPException) as exc_info:
        conversations_route.read_control(channel=channel, user_id=victim_id, current_user=EMPLOYEE_A)
    assert exc_info.value.status_code == 404

    # The side effect that used to happen here (auto-vivify a Company A row)
    # must no longer occur.
    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False

    # And the downstream transcript read must still be blocked.
    with pytest.raises(HTTPException) as exc_info2:
        conversations_route.read_conversation(channel=channel, user_id=victim_id, limit=50, current_user=EMPLOYEE_A)
    assert exc_info2.value.status_code == 404


# ---------------------------------------------------------------------------
# PoC 2: POST /{channel}/{user_id}/return-to-ai  (poc_bypass_return_to_ai.py)
# ---------------------------------------------------------------------------

def test_return_to_ai_no_longer_auto_vivifies_ownership(fresh_db):
    svc = fresh_db
    channel = "whatsapp"
    victim_id = "+15551234567"
    secret_text = "Company B secret: prescription for Ozempic"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import conversations as conversations_route

    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.return_to_ai(channel=channel, user_id=victim_id, current_user=EMPLOYEE_A)
    assert exc_info.value.status_code == 404

    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False

    with pytest.raises(HTTPException) as exc_info2:
        conversations_route.read_conversation(channel=channel, user_id=victim_id, limit=50, current_user=EMPLOYEE_A)
    assert exc_info2.value.status_code == 404


# ---------------------------------------------------------------------------
# PoC 3: POST /{channel}/{user_id}/reply  (poc_bypass_test.py, manual reply)
# ---------------------------------------------------------------------------

def test_manual_reply_no_longer_auto_vivifies_ownership(fresh_db):
    svc = fresh_db
    channel = "messenger"
    victim_id = "PSID_OF_COMPANY_B_CUSTOMER"
    secret_text = "Company B secret: my SSN is 123-45-6789"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import manual_messages
    from backend.api.routes import conversations as conversations_route

    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False

    with pytest.raises(HTTPException) as exc_info:
        manual_messages.send_manual_conversation_reply(
            channel=channel,
            user_id=victim_id,
            payload=manual_messages.ManualReplyRequest(text="hi there"),
            current_user=EMPLOYEE_A,
        )
    # Must now be rejected as "not found", not the old 409 the auto-created,
    # AI-handled row used to produce.
    assert exc_info.value.status_code == 404

    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False

    with pytest.raises(HTTPException) as exc_info2:
        conversations_route.read_conversation(channel=channel, user_id=victim_id, limit=50, current_user=EMPLOYEE_A)
    assert exc_info2.value.status_code == 404


# ---------------------------------------------------------------------------
# Additional endpoints found during the full audit of this repair round.
# ---------------------------------------------------------------------------

def test_take_over_no_longer_auto_vivifies_ownership(fresh_db):
    svc = fresh_db
    channel = "messenger"
    victim_id = "victim_takeover"
    secret_text = "Company B secret: takeover probe"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import conversations as conversations_route

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.take_over(channel=channel, user_id=victim_id, current_user=EMPLOYEE_A)
    assert exc_info.value.status_code == 404

    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False


def test_release_no_longer_auto_vivifies_ownership(fresh_db):
    svc = fresh_db
    channel = "messenger"
    victim_id = "victim_release"
    secret_text = "Company B secret: release probe"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import conversations as conversations_route

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.release_conversation(channel=channel, user_id=victim_id, current_user=EMPLOYEE_A)
    assert exc_info.value.status_code == 404

    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False


def test_update_control_no_longer_auto_vivifies_ownership(fresh_db):
    svc = fresh_db
    channel = "messenger"
    victim_id = "victim_update_control"
    secret_text = "Company B secret: update-control probe"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import conversations as conversations_route

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.update_control(
            channel=channel,
            user_id=victim_id,
            payload=conversations_route.ConversationControlUpdate(priority="high"),
            current_user=EMPLOYEE_A,
        )
    assert exc_info.value.status_code == 404
    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False


def test_add_note_no_longer_auto_vivifies_ownership(fresh_db):
    svc = fresh_db
    channel = "messenger"
    victim_id = "victim_add_note"
    secret_text = "Company B secret: add-note probe"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import conversations as conversations_route

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.add_note(
            channel=channel,
            user_id=victim_id,
            payload=conversations_route.ConversationNoteCreate(note="probe"),
            current_user=EMPLOYEE_A,
        )
    assert exc_info.value.status_code == 404
    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False


def test_list_conversations_does_not_leak_or_auto_vivify_other_companies(fresh_db):
    """Discovered during this repair round's full audit: list_conversations
    (and the live SSE feed) iterate the GLOBAL flat-file conversation store
    and, for every file found, called get_state(company_id=<caller's own
    company>, ...) with no existence gate at all -- auto-vivifying a
    Company-A-owned row for every OTHER company's customer on every single
    inbox page load, and (independently of auto-vivification) returning
    those other companies' customer_name / last_message / tags into
    Company A's own list. Neither should happen now."""
    svc = fresh_db
    channel = "messenger"
    victim_id = "victim_list"
    secret_text = "Company B secret: appears in a list row"
    _seed_victim_conversation(svc, channel, victim_id, secret_text)

    from backend.api.routes import conversations as conversations_route

    result = conversations_route.list_conversations(
        search="",
        channel="all",
        status="all",
        department="all",
        assigned_user_id=None,
        folder="all",
        tag="",
        read_status="all",
        page=1,
        page_size=20,
        current_user=EMPLOYEE_A,
    )

    ids_seen = {item["id"] for item in result["items"]}
    assert f"{channel}:{victim_id}" not in ids_seen
    for item in result["items"]:
        assert secret_text not in (item.get("last_message") or "")

    # And the auto-vivification side effect must not have happened either.
    assert svc.conversation_exists(company_id=COMPANY_A, channel=channel, external_user_id=victim_id) is False


# ---------------------------------------------------------------------------
# Positive controls: the fix must not break legitimate same-company use.
# ---------------------------------------------------------------------------

def test_legitimate_same_company_read_control_still_works(fresh_db):
    svc = fresh_db
    channel = "messenger"
    customer_id = "own_customer_1"
    text = "Hi, I need help with my order."
    svc.get_or_create(company_id=COMPANY_A, channel=channel, external_user_id=customer_id)
    from core.conversation_store import save_conversation_message
    save_conversation_message(channel=channel, user_id=customer_id, direction="inbound", text=text)

    from backend.api.routes import conversations as conversations_route

    result = conversations_route.read_control(channel=channel, user_id=customer_id, current_user=EMPLOYEE_A)
    assert result["conversation"]["company_id"] == COMPANY_A


def test_legitimate_same_company_take_over_and_reply_still_works(fresh_db):
    svc = fresh_db
    channel = "messenger"
    customer_id = "own_customer_2"
    svc.get_or_create(company_id=COMPANY_A, channel=channel, external_user_id=customer_id)
    from core.conversation_store import save_conversation_message
    save_conversation_message(channel=channel, user_id=customer_id, direction="inbound", text="hello")

    from backend.api.routes import conversations as conversations_route

    result = conversations_route.take_over(channel=channel, user_id=customer_id, current_user=EMPLOYEE_A)
    assert result["conversation"]["assigned_user_id"] == 101
    assert result["conversation"]["handled_by_ai"] is False


def test_legitimate_same_company_list_conversations_still_shows_own_customer(fresh_db):
    svc = fresh_db
    channel = "messenger"
    customer_id = "own_customer_3"
    text = "My own company's customer message"
    svc.get_or_create(company_id=COMPANY_A, channel=channel, external_user_id=customer_id)
    from core.conversation_store import save_conversation_message
    save_conversation_message(channel=channel, user_id=customer_id, direction="inbound", text=text)

    from backend.api.routes import conversations as conversations_route

    result = conversations_route.list_conversations(
        search="",
        channel="all",
        status="all",
        department="all",
        assigned_user_id=None,
        folder="all",
        tag="",
        read_status="all",
        page=1,
        page_size=20,
        current_user=EMPLOYEE_A,
    )
    ids_seen = {item["id"]: item for item in result["items"]}
    assert f"{channel}:{customer_id}" in ids_seen
    assert ids_seen[f"{channel}:{customer_id}"]["last_message"] == text
