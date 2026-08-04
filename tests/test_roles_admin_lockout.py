"""Tests for the last-admin self-lockout protection in
backend/api/routes/roles.py.

Two endpoints can strip administrator access (users.manage, or the
built-in owner role) from the caller's own role:

  * PATCH /api/admin/access/users/{user_id} -- re-assigning yourself to a
    non-admin role (update_user_assignment).
  * PATCH /api/admin/access/roles/{role_id} -- editing the permission_codes
    of a role you currently hold (update_role).

If either action would leave the company with zero admin-capable users,
the whole company is permanently locked out of user/role/settings
administration with no in-app recovery path. Both endpoints must reject
that, and both must do the check + write inside a single BEGIN IMMEDIATE
transaction so two admins racing to demote themselves concurrently cannot
both pass the check and both succeed.

Run with: python3 -m pytest tests/test_roles_admin_lockout.py -v
"""
import os
import sys
import tempfile
import threading
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Mirrors the fixture in tests/test_conversation_ownership.py -- mutating
    the existing singleton's db_path is the reliable way to isolate tests
    against this codebase's module layout. core/engine imports depend on
    company_settings_service.ensure_schema() having run, so call it here.
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

    with db.connect() as conn:
        for uid, email in (
            (501, "admin1@test.local"),
            (502, "admin2@test.local"),
            (503, "employee@test.local"),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, full_name, status) VALUES (?, ?, ?, 'active')",
                (uid, email, email),
            )
        conn.commit()

    yield db

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


COMPANY_ID = 1


def _make_role(conn, code, name, permission_codes=()):
    cursor = conn.execute(
        "INSERT INTO roles (company_id, name, code, description, is_system) VALUES (?, ?, ?, ?, 0)",
        (COMPANY_ID, name, code, ""),
    )
    role_id = cursor.lastrowid
    for code_name in permission_codes:
        permission = conn.execute(
            "SELECT id FROM permissions WHERE code = ?", (code_name,)
        ).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (role_id, permission["id"]),
        )
    conn.commit()
    return role_id


def _assign(conn, user_id, role_id):
    conn.execute(
        """
        INSERT INTO company_users (company_id, user_id, role_id, status)
        VALUES (?, ?, ?, 'active')
        ON CONFLICT(company_id, user_id) DO UPDATE SET role_id = excluded.role_id
        """,
        (COMPANY_ID, user_id, role_id),
    )
    conn.commit()


def _current_role_id(conn, user_id):
    row = conn.execute(
        "SELECT role_id FROM company_users WHERE company_id = ? AND user_id = ?",
        (COMPANY_ID, user_id),
    ).fetchone()
    return row["role_id"] if row else None


def _role_has_manage_users(conn, role_id):
    row = conn.execute(
        """
        SELECT 1 FROM role_permissions
        JOIN permissions ON permissions.id = role_permissions.permission_id
        WHERE role_permissions.role_id = ? AND permissions.code = 'users.manage'
        LIMIT 1
        """,
        (role_id,),
    ).fetchone()
    return row is not None


def _any_admin_capable_user(conn):
    """True if the company still has at least one active admin-capable user
    (owner role or a role holding users.manage). This is the invariant every
    guard exists to preserve."""
    row = conn.execute(
        """
        SELECT 1
        FROM company_users
        JOIN roles ON roles.id = company_users.role_id
        WHERE company_users.company_id = ?
          AND company_users.status = 'active'
          AND (
                roles.code = 'owner'
                OR EXISTS (
                    SELECT 1 FROM role_permissions
                    JOIN permissions ON permissions.id = role_permissions.permission_id
                    WHERE role_permissions.role_id = roles.id
                      AND permissions.code = 'users.manage'
                )
          )
        LIMIT 1
        """,
        (COMPANY_ID,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Finding 1: role-permission edit self-lockout guard
# ---------------------------------------------------------------------------

def test_sole_admin_cannot_strip_manage_users_from_own_role(fresh_db):
    """(a) Editing your own role's permissions to remove admin-capability is
    rejected when you are the last admin."""
    from backend.api.routes.roles import update_role
    from backend.api.schemas.roles import RoleUpdateRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage", "conversations.view"])
        _assign(conn, 501, admin_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    # Remove users.manage, keep an unrelated permission.
    payload = RoleUpdateRequest(permission_codes=["conversations.view"])

    with pytest.raises(HTTPException) as exc_info:
        update_role(role_id=admin_role_id, payload=payload, current_user=current_user)
    assert exc_info.value.status_code == 400

    # Rejected AND rolled back: the role still holds users.manage.
    with fresh_db.connect() as conn:
        assert _role_has_manage_users(conn, admin_role_id)
        assert _any_admin_capable_user(conn)


def test_sole_admin_cannot_empty_own_role_permissions(fresh_db):
    """Passing an empty permission_codes list is the most direct lockout and
    must be rejected for the sole admin too."""
    from backend.api.routes.roles import update_role
    from backend.api.schemas.roles import RoleUpdateRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        _assign(conn, 501, admin_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = RoleUpdateRequest(permission_codes=[])

    with pytest.raises(HTTPException) as exc_info:
        update_role(role_id=admin_role_id, payload=payload, current_user=current_user)
    assert exc_info.value.status_code == 400

    with fresh_db.connect() as conn:
        assert _role_has_manage_users(conn, admin_role_id)


def test_strip_own_role_allowed_when_another_admin_user_exists(fresh_db):
    """(b) Succeeds when another admin-capable user (on a different admin
    role) exists."""
    from backend.api.routes.roles import update_role
    from backend.api.schemas.roles import RoleUpdateRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        owner_role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'", (COMPANY_ID,)
        ).fetchone()
        _assign(conn, 501, admin_role_id)
        _assign(conn, 502, owner_role["id"])  # a different admin-capable user remains

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = RoleUpdateRequest(permission_codes=[])

    result = update_role(role_id=admin_role_id, payload=payload, current_user=current_user)
    assert result["success"] is True

    with fresh_db.connect() as conn:
        assert not _role_has_manage_users(conn, admin_role_id)  # edit applied
        assert _any_admin_capable_user(conn)  # owner still admin


def test_strip_a_role_the_caller_does_not_hold_is_allowed(fresh_db):
    """The guard only fires when the CALLER currently holds the edited role.
    An admin editing some other, unheld role must be unaffected even as the
    sole admin -- that role isn't what keeps them admin."""
    from backend.api.routes.roles import update_role
    from backend.api.schemas.roles import RoleUpdateRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        other_role_id = _make_role(conn, "support_lead", "Support Lead", ["users.manage"])
        _assign(conn, 501, admin_role_id)  # caller keeps their own admin role

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = RoleUpdateRequest(permission_codes=[])

    # Editing other_role_id (which the caller does not hold) is fine: the
    # caller stays admin via admin_role_id.
    result = update_role(role_id=other_role_id, payload=payload, current_user=current_user)
    assert result["success"] is True

    with fresh_db.connect() as conn:
        assert _role_has_manage_users(conn, admin_role_id)
        assert _any_admin_capable_user(conn)


def test_rename_own_admin_role_without_touching_permissions_is_allowed(fresh_db):
    """Editing only name/description (permission_codes=None) never removes
    admin access, so it must be allowed for the sole admin."""
    from backend.api.routes.roles import update_role
    from backend.api.schemas.roles import RoleUpdateRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        _assign(conn, 501, admin_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = RoleUpdateRequest(name="Administrator", description="renamed")

    result = update_role(role_id=admin_role_id, payload=payload, current_user=current_user)
    assert result["success"] is True

    with fresh_db.connect() as conn:
        assert _role_has_manage_users(conn, admin_role_id)


# ---------------------------------------------------------------------------
# Finding 1 companion: the pre-existing user-reassignment guard (implemented
# here too, sharing the same helper) must still hold.
# ---------------------------------------------------------------------------

def test_sole_admin_cannot_reassign_self_to_non_admin_role(fresh_db):
    from backend.api.routes.roles import update_user_assignment
    from backend.api.schemas.roles import UserAssignmentRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        employee_role_id = _make_role(conn, "employee", "Employee", [])
        _assign(conn, 501, admin_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = UserAssignmentRequest(role_id=employee_role_id, branch_id=None, status="active")

    with pytest.raises(HTTPException) as exc_info:
        update_user_assignment(user_id=501, payload=payload, current_user=current_user)
    assert exc_info.value.status_code == 400

    with fresh_db.connect() as conn:
        assert _current_role_id(conn, 501) == admin_role_id


def test_self_reassignment_allowed_when_another_admin_remains(fresh_db):
    from backend.api.routes.roles import update_user_assignment
    from backend.api.schemas.roles import UserAssignmentRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        employee_role_id = _make_role(conn, "employee", "Employee", [])
        _assign(conn, 501, admin_role_id)
        _assign(conn, 502, admin_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = UserAssignmentRequest(role_id=employee_role_id, branch_id=None, status="active")

    result = update_user_assignment(user_id=501, payload=payload, current_user=current_user)
    assert result["success"] is True
    with fresh_db.connect() as conn:
        assert _current_role_id(conn, 501) == employee_role_id
        assert _any_admin_capable_user(conn)


# ---------------------------------------------------------------------------
# Finding 2: the check + write is one BEGIN IMMEDIATE transaction, so the
# race cannot leave zero admins.
# ---------------------------------------------------------------------------

def test_sequential_race_window_cannot_leave_zero_admins(fresh_db):
    """(c) Simulated race window (deterministic).

    Two distinct admin roles, one admin each -- the only two admins in the
    company. Both admins try to strip users.manage from their own role. With
    a naive check-then-act, each one's check sees the *other* admin and
    passes, so both succeed and the company is left with zero admins.

    Because the guard re-reads the admin count inside the same BEGIN
    IMMEDIATE transaction that performs the write, the second call observes
    the first call's committed effect: the first strip succeeds, the second
    is rejected. At least one admin always survives.
    """
    from backend.api.routes.roles import update_role
    from backend.api.schemas.roles import RoleUpdateRequest

    with fresh_db.connect() as conn:
        role_a = _make_role(conn, "admin_a", "Admin A", ["users.manage"])
        role_b = _make_role(conn, "admin_b", "Admin B", ["users.manage"])
        _assign(conn, 501, role_a)
        _assign(conn, 502, role_b)

    user_a = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    user_b = {"id": 502, "is_super_admin": False, "active_company_id": COMPANY_ID}
    empty = RoleUpdateRequest(permission_codes=[])

    # First demotion succeeds (the other admin still exists).
    result_a = update_role(role_id=role_a, payload=empty, current_user=user_a)
    assert result_a["success"] is True

    # Second demotion must now be rejected -- it re-reads the committed state
    # and sees no other admin remains.
    with pytest.raises(HTTPException) as exc_info:
        update_role(role_id=role_b, payload=empty, current_user=user_b)
    assert exc_info.value.status_code == 400

    with fresh_db.connect() as conn:
        assert _any_admin_capable_user(conn)
        assert _role_has_manage_users(conn, role_b)  # second edit rolled back


def test_threaded_concurrent_self_demotion_cannot_leave_zero_admins(fresh_db):
    """(c) Genuine concurrency.

    Two threads simultaneously strip users.manage from their own (distinct)
    admin role. BEGIN IMMEDIATE serializes them: exactly one succeeds, the
    other is rejected with HTTP 400, and an admin always remains. The
    invariant (an admin survives) must hold regardless of which thread wins.
    """
    from backend.api.routes.roles import update_role
    from backend.api.schemas.roles import RoleUpdateRequest

    with fresh_db.connect() as conn:
        role_a = _make_role(conn, "admin_a", "Admin A", ["users.manage"])
        role_b = _make_role(conn, "admin_b", "Admin B", ["users.manage"])
        _assign(conn, 501, role_a)
        _assign(conn, 502, role_b)

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def demote(key, role_id, user):
        payload = RoleUpdateRequest(permission_codes=[])
        barrier.wait()
        try:
            update_role(role_id=role_id, payload=payload, current_user=user)
            results[key] = "ok"
        except HTTPException as exc:
            results[key] = exc.status_code
        except Exception as exc:  # pragma: no cover - surfaced in assert below
            results[key] = repr(exc)

    user_a = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    user_b = {"id": 502, "is_super_admin": False, "active_company_id": COMPANY_ID}
    t1 = threading.Thread(target=demote, args=("a", role_a, user_a))
    t2 = threading.Thread(target=demote, args=("b", role_b, user_b))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    outcomes = sorted(str(v) for v in results.values())
    # Exactly one success, one rejection (400). No unexpected exceptions.
    assert outcomes == ["400", "ok"], f"unexpected outcomes: {results}"

    with fresh_db.connect() as conn:
        assert _any_admin_capable_user(conn)
