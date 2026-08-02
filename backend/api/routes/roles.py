import json

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.schemas.roles import (
    RoleCreateRequest,
    RoleUpdateRequest,
    UserAssignmentRequest,
    UserCreateRequest,
)
from backend.services.auth_service import auth_service, get_current_user
from backend.services.department_service import department_service
from backend.services.platform_admin_service import platform_admin_service
from database.database import db


router = APIRouter(prefix="/api/admin/access", tags=["Roles and Permissions"])


def _company_id(current_user: dict) -> int:
    return auth_service.resolve_company_id(current_user)


def _require_access_admin(current_user: dict, company_id: int) -> None:
    if current_user.get("is_super_admin"):
        return
    if auth_service.has_permission(
        current_user["id"], company_id, "users.manage", False
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only an administrator can manage users, roles and permissions.",
    )


@router.get("/overview")
def overview(current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    with db.connect() as conn:
        permissions = [dict(row) for row in conn.execute("""
            SELECT id, code, name, description
            FROM permissions
            ORDER BY code
        """).fetchall()]
        role_rows = conn.execute("""
            SELECT id, name, code, description, is_system
            FROM roles
            WHERE company_id = ?
            ORDER BY is_system DESC, name
        """, (company_id,)).fetchall()
        roles = []
        for role in role_rows:
            item = dict(role)
            item["permission_codes"] = [row["code"] for row in conn.execute("""
                SELECT permissions.code
                FROM role_permissions
                JOIN permissions ON permissions.id = role_permissions.permission_id
                WHERE role_permissions.role_id = ?
                ORDER BY permissions.code
            """, (role["id"],)).fetchall()]
            roles.append(item)

        users = [dict(row) for row in conn.execute("""
            SELECT
                users.id,
                users.full_name,
                users.email,
                users.phone,
                users.status AS user_status,
                users.last_login_at,
                company_users.status AS membership_status,
                company_users.branch_id,
                company_users.departments_json,
                roles.id AS role_id,
                roles.name AS role_name,
                roles.code AS role_code,
                branches.name AS branch_name
            FROM company_users
            JOIN users ON users.id = company_users.user_id
            LEFT JOIN roles ON roles.id = company_users.role_id
            LEFT JOIN branches ON branches.id = company_users.branch_id
            WHERE company_users.company_id = ?
            ORDER BY users.full_name, users.email
        """, (company_id,)).fetchall()]
        for user in users:
            user["departments"] = json.loads(user.pop("departments_json") or "[]")

        branches = [dict(row) for row in conn.execute("""
            SELECT id, name, code
            FROM branches
            WHERE company_id = ? AND status = 'active'
            ORDER BY name
        """, (company_id,)).fetchall()]

        company = conn.execute("""
            SELECT id, name, slug
            FROM companies
            WHERE id = ?
        """, (company_id,)).fetchone()

    return {
        "company": dict(company) if company else None,
        "permissions": permissions,
        "roles": roles,
        "users": users,
        "branches": branches,
        "departments": department_service.list_for_company(company_id=company_id),
    }


@router.post("/roles")
def create_role(payload: RoleCreateRequest, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    with db.connect() as conn:
        try:
            cursor = conn.execute("""
                INSERT INTO roles (company_id, name, code, description, is_system)
                VALUES (?, ?, ?, ?, 0)
            """, (company_id, payload.name.strip(), payload.code.strip().lower(), payload.description))
        except Exception as exc:
            raise HTTPException(status_code=409, detail="A role with this code already exists.") from exc

        role_id = cursor.lastrowid
        _set_role_permissions(conn, role_id, payload.permission_codes)
        conn.commit()
    return {"success": True, "role_id": role_id}


def _set_role_permissions(conn, role_id: int, permission_codes: list[str]) -> None:
    conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
    codes = sorted(set(permission_codes))
    if not codes:
        return
    placeholders = ",".join("?" for _ in codes)
    permission_rows = conn.execute(
        f"SELECT id FROM permissions WHERE code IN ({placeholders})", codes
    ).fetchall()
    conn.executemany(
        "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
        [(role_id, row["id"]) for row in permission_rows],
    )


@router.patch("/roles/{role_id}")
def update_role(role_id: int, payload: RoleUpdateRequest, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    with db.connect() as conn:
        role = conn.execute("SELECT * FROM roles WHERE id = ? AND company_id = ?", (role_id, company_id)).fetchone()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found.")
        if role["code"] == "owner" and payload.permission_codes is not None:
            raise HTTPException(status_code=400, detail="The Owner role always has full access.")
        conn.execute("""
            UPDATE roles
            SET name = COALESCE(?, name), description = COALESCE(?, description)
            WHERE id = ?
        """, (payload.name, payload.description, role_id))
        if payload.permission_codes is not None:
            _set_role_permissions(conn, role_id, payload.permission_codes)
        conn.commit()
    return {"success": True}


@router.post("/users")
def create_user(payload: UserCreateRequest, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    limits = platform_admin_service.get_active_subscription_limits(company_id=company_id)
    if limits is not None:
        with db.connect() as conn:
            active_users = conn.execute(
                "SELECT COUNT(*) AS total FROM company_users WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchone()["total"]
        if active_users >= limits["max_users"]:
            raise HTTPException(
                status_code=400,
                detail=f"This company's plan ({limits['name']}) allows up to "
                       f"{limits['max_users']} users. Contact your platform administrator to upgrade.",
            )

    with db.connect() as conn:
        role = conn.execute("SELECT id FROM roles WHERE id = ? AND company_id = ?", (payload.role_id, company_id)).fetchone()
        if not role:
            raise HTTPException(status_code=400, detail="Selected role is invalid.")

    try:
        cleaned_departments = department_service.clean_selection(company_id=company_id, departments=payload.departments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        user_id = auth_service.create_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            phone=payload.phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    with db.connect() as conn:
        conn.execute("""
            INSERT INTO company_users (company_id, user_id, role_id, branch_id, status, departments_json)
            VALUES (?, ?, ?, ?, 'active', ?)
        """, (company_id, user_id, payload.role_id, payload.branch_id, json.dumps(cleaned_departments)))
        conn.commit()
    return {"success": True, "user_id": user_id}


@router.patch("/users/{user_id}")
def update_user_assignment(user_id: int, payload: UserAssignmentRequest, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    if user_id == current_user["id"] and payload.status != "active":
        raise HTTPException(status_code=400, detail="You cannot disable your own membership.")

    try:
        cleaned_departments = department_service.clean_selection(company_id=company_id, departments=payload.departments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with db.connect() as conn:
        role = conn.execute("SELECT id FROM roles WHERE id = ? AND company_id = ?", (payload.role_id, company_id)).fetchone()
        if not role:
            raise HTTPException(status_code=400, detail="Selected role is invalid.")
        cursor = conn.execute("""
            UPDATE company_users
            SET role_id = ?, branch_id = ?, status = ?, departments_json = ?
            WHERE company_id = ? AND user_id = ?
        """, (payload.role_id, payload.branch_id, payload.status, json.dumps(cleaned_departments), company_id, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Company user not found.")
        conn.commit()
    return {"success": True}
