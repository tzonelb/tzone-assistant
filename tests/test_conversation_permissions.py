"""
RBAC enforcement tests for the core conversation feature.

Before this suite, backend/api/routes/conversations.py and
manual_messages.py only checked authentication + active company membership.
They never consulted the seeded "conversations.view" / "conversations.reply"
permission codes, so ANY authenticated employee could read and reply to every
conversation regardless of the role an admin assigned them.

These tests prove the gating is now enforced and is *additive*:
  * an employee WITHOUT conversations.view is 403'd on read routes,
  * an employee WITHOUT conversations.reply is 403'd on reply/mutate routes,
  * an employee WITH the codes behaves exactly as before (regression guard),
  * owner role and super admins bypass the checks.

Run with: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COMPANY_ID = 1
CHANNEL = "messenger"
CUSTOMER_ID = "perm_test_customer"


def _create_role(conn, code, name, permission_codes):
    cursor = conn.execute(
        """
        INSERT INTO roles (company_id, name, code, description, is_system)
        VALUES (?, ?, ?, ?, 0)
        """,
        (COMPANY_ID, name, code, name),
    )
    role_id = cursor.lastrowid
    for perm_code in permission_codes:
        row = conn.execute(
            "SELECT id FROM permissions WHERE code = ?", (perm_code,)
        ).fetchone()
        assert row is not None, f"permission {perm_code} not seeded"
        conn.execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (role_id, row["id"]),
        )
    return role_id


def _create_user(conn, user_id, email, role_id, is_super_admin=0):
    conn.execute(
        """
        INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin)
        VALUES (?, ?, ?, 'active', ?)
        """,
        (user_id, email, email, is_super_admin),
    )
    if role_id is not None:
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, branch_id, status)
            VALUES (?, ?, ?, NULL, 'active')
            """,
            (COMPANY_ID, user_id, role_id),
        )


@pytest.fixture()
def env():
    """Fresh throwaway DB seeded with four distinct role/permission setups."""
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()  # seeds company 1, owner role, and all permission codes
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    # core/engine.py depends on this unconditionally now.
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        owner_role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (COMPANY_ID,),
        ).fetchone()
        assert owner_role is not None
        agent_role = _create_role(
            conn, "agent", "Agent",
            ["conversations.view", "conversations.reply"],
        )
        viewer_role = _create_role(
            conn, "viewer", "Viewer", ["conversations.view"],
        )
        restricted_role = _create_role(
            conn, "restricted", "Restricted", ["dashboard.view"],
        )

        _create_user(conn, 1, "owner@test.local", owner_role["id"])
        _create_user(conn, 2, "agent@test.local", agent_role)
        _create_user(conn, 3, "viewer@test.local", viewer_role)
        _create_user(conn, 4, "restricted@test.local", restricted_role)
        # Super admin needs no membership row.
        _create_user(conn, 5, "super@test.local", None, is_super_admin=1)
        conn.commit()

    # Permission gating is additive on top of the existing company-scoped
    # ownership gate (conversation_exists) -- a permission-successful call
    # must still find a real conversation, or it 404s before the permission
    # assertions below even run. Seed one for every customer id this suite
    # exercises.
    from core.conversation_store import save_conversation_message

    def _seed(external_user_id):
        conversation_control_service.get_or_create(
            company_id=COMPANY_ID,
            channel=CHANNEL,
            external_user_id=external_user_id,
        )
        save_conversation_message(
            channel=CHANNEL,
            user_id=external_user_id,
            direction="inbound",
            text="hello",
        )

    for cust in (CUSTOMER_ID, "owner_cust", "super_cust"):
        _seed(cust)

    yield

    db.db_path = original_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def _user(user_id, is_super_admin=False):
    return {
        "id": user_id,
        "email": f"user{user_id}@test.local",
        "active_company_id": COMPANY_ID,
        "is_super_admin": is_super_admin,
    }


OWNER = lambda: _user(1)
AGENT = lambda: _user(2)
VIEWER = lambda: _user(3)
RESTRICTED = lambda: _user(4)
SUPER = lambda: _user(5, is_super_admin=True)


def _status_of(exc: HTTPException) -> int:
    return exc.status_code


def _assert_forbidden(fn):
    with pytest.raises(HTTPException) as info:
        fn()
    assert _status_of(info.value) == 403, (
        f"expected 403, got {_status_of(info.value)}: {info.value.detail}"
    )


def _assert_not_forbidden(fn):
    """Call fn; the permission gate must pass. Any raised HTTPException must
    NOT be a 403 (a later 404/409/etc. is fine — it means the gate was
    cleared and the normal downstream logic ran)."""
    try:
        return fn()
    except HTTPException as exc:
        assert _status_of(exc) != 403, (
            f"unexpectedly forbidden by permission gate: {exc.detail}"
        )
        return None


# --------------------------------------------------------------------------
# has_permission semantics (the primitive every gate relies on)
# --------------------------------------------------------------------------

def test_has_permission_semantics(env):
    from backend.services.auth_service import auth_service

    def check(uid, code, is_super=False):
        return auth_service.has_permission(uid, COMPANY_ID, code, is_super)

    # owner role bypasses every code
    assert check(1, "conversations.view")
    assert check(1, "conversations.reply")
    # agent has both
    assert check(2, "conversations.view")
    assert check(2, "conversations.reply")
    # viewer has view but not reply
    assert check(3, "conversations.view")
    assert not check(3, "conversations.reply")
    # restricted has neither
    assert not check(4, "conversations.view")
    assert not check(4, "conversations.reply")
    # super admin bypasses via the flag
    assert check(5, "conversations.view", is_super=True)
    assert check(5, "conversations.reply", is_super=True)


# --------------------------------------------------------------------------
# Read routes require conversations.view
# --------------------------------------------------------------------------

def _call_list(user):
    from backend.api.routes import conversations
    return conversations.list_conversations(
        search="", channel="all", status="all", department="all",
        assigned_user_id=None, folder="inbox", tag="", read_status="all",
        page=1, page_size=20, current_user=user,
    )


def test_list_forbidden_without_view(env):
    _assert_forbidden(lambda: _call_list(RESTRICTED()))


def test_list_allowed_with_view(env):
    for who in (VIEWER, AGENT, OWNER, SUPER):
        result = _call_list(who())
        assert result["status"] == "ok"


def test_read_control_requires_view(env):
    from backend.api.routes import conversations
    _assert_forbidden(
        lambda: conversations.read_control(CHANNEL, CUSTOMER_ID, current_user=RESTRICTED())
    )
    for who in (VIEWER, AGENT, OWNER, SUPER):
        result = conversations.read_control(CHANNEL, CUSTOMER_ID, current_user=who())
        assert "permissions" in result


def test_read_control_permissions_reflect_reply_code(env):
    """A viewer (view but no reply) must not be offered reply/manage/take-over
    controls, so the UI never surfaces an action the backend will 403."""
    from backend.api.routes import conversations
    viewer_result = conversations.read_control(CHANNEL, CUSTOMER_ID, current_user=VIEWER())
    perms = viewer_result["permissions"]
    assert viewer_result["can_reply_permission"] is False
    assert perms["can_manage"] is False
    assert perms["can_take_over"] is False
    assert perms["can_reply"] is False

    agent_result = conversations.read_control(CHANNEL, CUSTOMER_ID, current_user=AGENT())
    assert agent_result["can_reply_permission"] is True
    # Unowned + reply permission -> take-over offered.
    assert agent_result["permissions"]["can_take_over"] is True


def test_export_requires_view(env):
    from backend.api.routes import conversations
    _assert_forbidden(
        lambda: conversations.export_conversation(
            CHANNEL, CUSTOMER_ID, scope="full", file_format="json",
            current_user=RESTRICTED(),
        )
    )
    # A viewer can export (read-only capability).
    _assert_not_forbidden(
        lambda: conversations.export_conversation(
            CHANNEL, CUSTOMER_ID, scope="full", file_format="json",
            current_user=VIEWER(),
        )
    )


# --------------------------------------------------------------------------
# Reply / mutate routes require conversations.reply
# --------------------------------------------------------------------------

def test_take_over_requires_reply(env):
    from backend.api.routes import conversations
    # viewer has view but NOT reply -> forbidden
    _assert_forbidden(
        lambda: conversations.take_over(CHANNEL, CUSTOMER_ID, current_user=VIEWER())
    )
    _assert_forbidden(
        lambda: conversations.take_over(CHANNEL, CUSTOMER_ID, current_user=RESTRICTED())
    )


def test_take_over_regression_for_reply_capable_employee(env):
    """The primary regression guard: an employee holding conversations.reply
    takes over an unowned conversation exactly as before the gating existed."""
    from backend.api.routes import conversations
    result = conversations.take_over(CHANNEL, CUSTOMER_ID, current_user=AGENT())
    assert result["status"] == "ok"
    assert result["conversation"]["assigned_user_id"] == 2
    assert result["conversation"]["handled_by_ai"] is False


def test_owner_and_super_admin_can_take_over(env):
    from backend.api.routes import conversations
    result = conversations.take_over(CHANNEL, "owner_cust", current_user=OWNER())
    assert result["conversation"]["assigned_user_id"] == 1
    result = conversations.take_over(CHANNEL, "super_cust", current_user=SUPER())
    assert result["conversation"]["assigned_user_id"] == 5


def test_release_requires_reply(env):
    from backend.api.routes import conversations
    _assert_forbidden(
        lambda: conversations.release_conversation(CHANNEL, CUSTOMER_ID, current_user=VIEWER())
    )


def test_return_to_ai_requires_reply(env):
    from backend.api.routes import conversations
    _assert_forbidden(
        lambda: conversations.return_to_ai(CHANNEL, CUSTOMER_ID, current_user=VIEWER())
    )


def test_update_control_requires_reply(env):
    from backend.api.routes import conversations
    payload = conversations.ConversationControlUpdate(priority="high")
    _assert_forbidden(
        lambda: conversations.update_control(
            CHANNEL, CUSTOMER_ID, payload=payload, current_user=VIEWER()
        )
    )


def test_add_note_requires_reply(env):
    from backend.api.routes import conversations
    payload = conversations.ConversationNoteCreate(note="hi")
    _assert_forbidden(
        lambda: conversations.add_note(
            CHANNEL, CUSTOMER_ID, payload=payload, current_user=VIEWER()
        )
    )


def test_reply_capable_employee_passes_reply_gate(env):
    """An agent taking over then updating control must clear the reply gate
    (regression guard: additive gating did not break the normal flow)."""
    from backend.api.routes import conversations
    conversations.take_over(CHANNEL, CUSTOMER_ID, current_user=AGENT())
    payload = conversations.ConversationControlUpdate(priority="high")
    result = conversations.update_control(
        CHANNEL, CUSTOMER_ID, payload=payload, current_user=AGENT()
    )
    assert result["status"] == "ok"


# --------------------------------------------------------------------------
# Manual reply route (manual_messages.py)
# --------------------------------------------------------------------------

def test_manual_reply_forbidden_without_reply_permission(env):
    from backend.api.routes import manual_messages
    payload = manual_messages.ManualReplyRequest(text="hello")
    _assert_forbidden(
        lambda: manual_messages.send_manual_conversation_reply(
            CHANNEL, CUSTOMER_ID, payload=payload, current_user=VIEWER()
        )
    )
    _assert_forbidden(
        lambda: manual_messages.send_manual_conversation_reply(
            CHANNEL, CUSTOMER_ID, payload=payload, current_user=RESTRICTED()
        )
    )


def test_manual_reply_passes_gate_for_reply_capable_employee(env):
    """A reply-capable employee clears the permission gate. It then hits the
    normal ownership/take-over check (409) because the conversation is still
    AI-handled -- proving the gate is additive, not a replacement."""
    from backend.api.routes import manual_messages
    payload = manual_messages.ManualReplyRequest(text="hello")
    _assert_not_forbidden(
        lambda: manual_messages.send_manual_conversation_reply(
            CHANNEL, CUSTOMER_ID, payload=payload, current_user=AGENT()
        )
    )


# --------------------------------------------------------------------------
# Conversation tag catalog routes
# --------------------------------------------------------------------------

def test_tag_list_requires_view_create_requires_reply(env):
    from backend.api.routes import conversation_tags
    _assert_forbidden(
        lambda: conversation_tags.list_conversation_tags(current_user=RESTRICTED())
    )
    # viewer can list
    result = conversation_tags.list_conversation_tags(current_user=VIEWER())
    assert "items" in result
    # viewer cannot create (needs reply)
    payload = conversation_tags.ConversationTagCreate(name="VIP")
    _assert_forbidden(
        lambda: conversation_tags.create_conversation_tag(payload=payload, current_user=VIEWER())
    )
    # agent can create
    result = conversation_tags.create_conversation_tag(payload=payload, current_user=AGENT())
    assert "item" in result
