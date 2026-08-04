"""
Regression test for a confirmed cross-tenant data leak in
GET /api/conversations/{channel}/{user_id} (read_conversation) and
GET /api/conversations/{channel}/{user_id}/export (export_conversation).

core/conversation_store.py stores/reads raw conversation transcripts from
flat files at data/conversations/{channel}/{user_id}.jsonl with ZERO
company_id concept. Before this fix, both endpoints called
core.conversation_store.get_conversation(channel, user_id, limit)
directly, with no check that the resolved conversation actually belongs
to the caller's company. Any authenticated employee of ANY company could
read another company's full customer conversation transcript just by
knowing (or guessing) a channel + external_user_id pair -- e.g. a phone
number.

The fix adds a company-scoped ownership gate,
ConversationControlService.conversation_exists(company_id, channel,
external_user_id), backed by the properly tenant-scoped `conversations`
DB table, and both endpoints now call it *before* touching the raw
file-backed transcript, returning 404 (not 403, to avoid confirming/
denying another company's data) when the row doesn't belong to the
caller's resolved company.

Run with: python3 -m pytest tests/test_conversation_cross_tenant_isolation.py -v
"""
import json
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
    """Isolate both the SQLite DB (conversations table / company_users /
    etc.) and the flat-file conversation transcript store used by
    core.conversation_store, so this test never touches real data or
    config files.

    Follows the same db_path-swap approach as tests/test_conversation_ownership.py
    (reimporting modules via sys.modules deletion breaks pytest's
    assertion-rewrite import hook).
    """
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    import core.conversation_store as conversation_store

    tmp_db_path = tempfile.mktemp(suffix=".db")
    tmp_conversations_dir = Path(tempfile.mkdtemp(prefix="tzone_conv_store_"))

    original_db_path = db.db_path
    original_base_dir = conversation_store.BASE_DIR

    db.db_path = Path(tmp_db_path)
    conversation_store.BASE_DIR = tmp_conversations_dir

    db.create_tables()  # seeds default workspace(id=1)/company(id=1)
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        # Company 1 ("T-ZONE") is seeded by db.create_tables(). Add a
        # second, unrelated tenant sharing the same workspace row.
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

        for uid, email in (
            (101, "emp_company_a@test.local"),
            (202, "emp_company_b@test.local"),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, full_name, status) "
                "VALUES (?, ?, ?, 'active')",
                (uid, email, email),
            )

        # This suite tests tenant isolation, not permission tiers, so both
        # employees get the "owner" role (bypasses conversations.view/reply
        # checks, same as every other employee-facing permission gate) --
        # company 1's owner role is already seeded by db.create_tables();
        # company 2 needs its own.
        owner_role_a = conn.execute(
            "SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'"
        ).fetchone()["id"]
        owner_role_b = conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (2, 'Owner', 'owner', 'Full access to the company', 1)
            """
        ).lastrowid

        # Employee 101 belongs only to company 1 ("Company A").
        conn.execute(
            "INSERT INTO company_users (company_id, user_id, role_id, status) "
            "VALUES (1, 101, ?, 'active')",
            (owner_role_a,),
        )
        # Employee 202 belongs only to company 2 ("Company B").
        conn.execute(
            "INSERT INTO company_users (company_id, user_id, role_id, status) "
            "VALUES (2, 202, ?, 'active')",
            (owner_role_b,),
        )

        conn.commit()

    yield conversation_control_service

    db.db_path = original_db_path
    conversation_store.BASE_DIR = original_base_dir

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

# Company B's real customer -- an employee of Company A who knows/guesses
# this phone number should NOT be able to read this transcript.
CHANNEL = "whatsapp"
COMPANY_B_CUSTOMER_ID = "+15551234567"
COMPANY_B_SECRET_TEXT = "My credit card number is 4111-1111-1111-1111"

# Company A's own, legitimate customer.
COMPANY_A_CUSTOMER_ID = "+15559876543"
COMPANY_A_OWN_TEXT = "Hi, I'd like to check my order status."


def _seed_conversation(svc, company_id, channel, external_user_id, text):
    """Create both halves of a real conversation: the company-scoped DB
    row (via the same get_or_create() path production code uses) and the
    raw, NOT company-scoped flat-file transcript that
    core.conversation_store actually serves reads from."""
    from core.conversation_store import save_conversation_message

    svc.get_or_create(
        company_id=company_id,
        channel=channel,
        external_user_id=external_user_id,
    )
    save_conversation_message(
        channel=channel,
        user_id=external_user_id,
        direction="inbound",
        text=text,
    )


def test_conversation_exists_is_true_only_for_owning_company(fresh_db):
    svc = fresh_db
    _seed_conversation(svc, COMPANY_B, CHANNEL, COMPANY_B_CUSTOMER_ID, COMPANY_B_SECRET_TEXT)

    assert svc.conversation_exists(
        company_id=COMPANY_B, channel=CHANNEL, external_user_id=COMPANY_B_CUSTOMER_ID
    ) is True
    assert svc.conversation_exists(
        company_id=COMPANY_A, channel=CHANNEL, external_user_id=COMPANY_B_CUSTOMER_ID
    ) is False


def test_conversation_exists_does_not_auto_create_a_row(fresh_db):
    """The whole point of using a dedicated read-only check instead of
    get_state()/get_or_create() is that those auto-vivify a row on first
    lookup, which would make any ownership check based on them always
    return True. Assert conversation_exists never does that."""
    svc = fresh_db

    assert svc.conversation_exists(
        company_id=COMPANY_A, channel=CHANNEL, external_user_id="never-seen-before"
    ) is False

    from database.database import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM conversations "
            "WHERE company_id = ? AND channel = ? AND external_user_id = ?",
            (COMPANY_A, CHANNEL, "never-seen-before"),
        ).fetchone()
    assert row["n"] == 0


def test_employee_cannot_read_another_companys_conversation_via_endpoint(fresh_db):
    """The actual exploit path: Company A's employee calls
    GET /api/conversations/{channel}/{user_id} for a customer that
    belongs to Company B. Must be rejected with 404 and must never reach
    the raw file read (core.conversation_store.get_conversation)."""
    svc = fresh_db
    _seed_conversation(svc, COMPANY_B, CHANNEL, COMPANY_B_CUSTOMER_ID, COMPANY_B_SECRET_TEXT)

    # Sanity check: the vulnerable file-backed store has zero tenant
    # awareness and genuinely contains Company B's secret data at this
    # channel/user_id -- proving this is a real leak, not a hypothetical.
    from core.conversation_store import get_conversation as raw_get_conversation
    raw_messages = raw_get_conversation(CHANNEL, COMPANY_B_CUSTOMER_ID, 50)
    assert any(COMPANY_B_SECRET_TEXT in m.get("text", "") for m in raw_messages)

    from backend.api.routes import conversations as conversations_route

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.read_conversation(
            channel=CHANNEL,
            user_id=COMPANY_B_CUSTOMER_ID,
            limit=50,
            current_user=EMPLOYEE_A,
        )

    assert exc_info.value.status_code == 404


def test_employee_can_read_their_own_companys_conversation_via_endpoint(fresh_db):
    """Positive control: the fix must not break legitimate same-company
    access -- Company A's employee reading Company A's own customer."""
    svc = fresh_db
    _seed_conversation(svc, COMPANY_A, CHANNEL, COMPANY_A_CUSTOMER_ID, COMPANY_A_OWN_TEXT)

    from backend.api.routes import conversations as conversations_route

    result = conversations_route.read_conversation(
        channel=CHANNEL,
        user_id=COMPANY_A_CUSTOMER_ID,
        limit=50,
        current_user=EMPLOYEE_A,
    )

    assert result["status"] == "ok"
    assert result["channel"] == CHANNEL
    assert result["user_id"] == COMPANY_A_CUSTOMER_ID
    assert any(COMPANY_A_OWN_TEXT in m.get("text", "") for m in result["messages"])


def test_employee_of_company_b_can_read_their_own_conversation_not_company_a(fresh_db):
    """Symmetric check: Company B's employee can read Company B's
    customer, and is equally blocked from Company A's."""
    svc = fresh_db
    _seed_conversation(svc, COMPANY_A, CHANNEL, COMPANY_A_CUSTOMER_ID, COMPANY_A_OWN_TEXT)
    _seed_conversation(svc, COMPANY_B, CHANNEL, COMPANY_B_CUSTOMER_ID, COMPANY_B_SECRET_TEXT)

    from backend.api.routes import conversations as conversations_route

    result = conversations_route.read_conversation(
        channel=CHANNEL,
        user_id=COMPANY_B_CUSTOMER_ID,
        limit=50,
        current_user=EMPLOYEE_B,
    )
    assert result["status"] == "ok"
    assert any(COMPANY_B_SECRET_TEXT in m.get("text", "") for m in result["messages"])

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.read_conversation(
            channel=CHANNEL,
            user_id=COMPANY_A_CUSTOMER_ID,
            limit=50,
            current_user=EMPLOYEE_B,
        )
    assert exc_info.value.status_code == 404


def test_read_conversation_404_does_not_leak_which_company_owns_it(fresh_db):
    """Both the "no such conversation anywhere" case and the "exists,
    but for another company" case must return an identical 404, so an
    attacker cannot distinguish them by probing channel/user_id pairs."""
    svc = fresh_db
    _seed_conversation(svc, COMPANY_B, CHANNEL, COMPANY_B_CUSTOMER_ID, COMPANY_B_SECRET_TEXT)

    from backend.api.routes import conversations as conversations_route

    with pytest.raises(HTTPException) as exc_info_owned_by_other:
        conversations_route.read_conversation(
            channel=CHANNEL,
            user_id=COMPANY_B_CUSTOMER_ID,
            limit=50,
            current_user=EMPLOYEE_A,
        )

    with pytest.raises(HTTPException) as exc_info_nonexistent:
        conversations_route.read_conversation(
            channel=CHANNEL,
            user_id="totally-made-up-number",
            limit=50,
            current_user=EMPLOYEE_A,
        )

    assert exc_info_owned_by_other.value.status_code == 404
    assert exc_info_nonexistent.value.status_code == 404
    assert exc_info_owned_by_other.value.detail == exc_info_nonexistent.value.detail


def test_export_conversation_blocks_cross_tenant_read(fresh_db):
    """Same gate must apply to the export endpoint (json format), which
    also reads the raw file-backed transcript."""
    svc = fresh_db
    _seed_conversation(svc, COMPANY_B, CHANNEL, COMPANY_B_CUSTOMER_ID, COMPANY_B_SECRET_TEXT)

    from backend.api.routes import conversations as conversations_route

    with pytest.raises(HTTPException) as exc_info:
        conversations_route.export_conversation(
            channel=CHANNEL,
            user_id=COMPANY_B_CUSTOMER_ID,
            scope="full",
            file_format="json",
            current_user=EMPLOYEE_A,
        )

    assert exc_info.value.status_code == 404


def test_export_conversation_allows_same_company_read(fresh_db):
    svc = fresh_db
    _seed_conversation(svc, COMPANY_A, CHANNEL, COMPANY_A_CUSTOMER_ID, COMPANY_A_OWN_TEXT)

    from backend.api.routes import conversations as conversations_route

    response = conversations_route.export_conversation(
        channel=CHANNEL,
        user_id=COMPANY_A_CUSTOMER_ID,
        scope="full",
        file_format="json",
        current_user=EMPLOYEE_A,
    )

    payload = json.loads(response.body)
    assert any(
        COMPANY_A_OWN_TEXT in (m.get("text") or "")
        for m in payload["messages"]
    )
