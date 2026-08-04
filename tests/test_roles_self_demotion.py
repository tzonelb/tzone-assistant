"""Tests for the last-admin self-demotion protection in
backend/api/routes/roles.py -- update_user_assignment().

Before this fix, an admin-capable user (owner, or any role holding the
users.manage permission) could change their OWN role to a role without
users.manage with no check at all. If they were the only admin-capable
user in the company, this permanently locked the whole company out of
user/role/settings management with no recovery path short of direct DB
access.

Run with: python3 -m pytest tests/test_roles_self_demotion.py -v
"""
import os
import sys
import tempfile
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Mirrors the fixture in tests/test_conversation_ownership.py -- mutating
    the existing singleton's db_path is the reliable way to isolate tests
    against this codebase's module layout.
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


def test_sole_admin_cannot_remove_own_manage_users_access(fresh_db):
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


def test_sole_owner_cannot_remove_own_admin_access(fresh_db):
    """The owner role bypasses per-permission checks entirely (has_permission
    always returns True for role code 'owner'), so it must count as
    admin-capable too."""
    from backend.api.routes.roles import update_user_assignment
    from backend.api.schemas.roles import UserAssignmentRequest

    with fresh_db.connect() as conn:
        owner_role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'", (COMPANY_ID,)
        ).fetchone()
        owner_role_id = owner_role["id"]
        employee_role_id = _make_role(conn, "employee", "Employee", [])
        _assign(conn, 501, owner_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = UserAssignmentRequest(role_id=employee_role_id, branch_id=None, status="active")

    with pytest.raises(HTTPException) as exc_info:
        update_user_assignment(user_id=501, payload=payload, current_user=current_user)

    assert exc_info.value.status_code == 400


def test_self_role_change_allowed_when_another_admin_remains(fresh_db):
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


def test_self_role_change_between_admin_capable_roles_allowed(fresh_db):
    """Moving from one admin-capable role to another never loses users.manage
    access, so it should be allowed even as the sole admin."""
    from backend.api.routes.roles import update_user_assignment
    from backend.api.schemas.roles import UserAssignmentRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        senior_admin_role_id = _make_role(conn, "senior_admin", "Senior Admin", ["users.manage", "settings.manage"])
        _assign(conn, 501, admin_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = UserAssignmentRequest(role_id=senior_admin_role_id, branch_id=None, status="active")

    result = update_user_assignment(user_id=501, payload=payload, current_user=current_user)
    assert result["success"] is True

    with fresh_db.connect() as conn:
        assert _current_role_id(conn, 501) == senior_admin_role_id


def test_admin_can_still_change_other_users_role_as_sole_admin(fresh_db):
    """The guard only applies to self-changes -- an admin managing someone
    else's role must be unaffected, even while being the only admin."""
    from backend.api.routes.roles import update_user_assignment
    from backend.api.schemas.roles import UserAssignmentRequest

    with fresh_db.connect() as conn:
        admin_role_id = _make_role(conn, "admin", "Admin", ["users.manage"])
        employee_role_id = _make_role(conn, "employee", "Employee", [])
        _assign(conn, 501, admin_role_id)
        _assign(conn, 503, employee_role_id)

    current_user = {"id": 501, "is_super_admin": False, "active_company_id": COMPANY_ID}
    payload = UserAssignmentRequest(role_id=employee_role_id, branch_id=None, status="active")

    result = update_user_assignment(user_id=503, payload=payload, current_user=current_user)
    assert result["success"] is True
