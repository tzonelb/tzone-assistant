import json

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.schemas.roles import (
    PermissionOverridesUpdateRequest,
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


def _log_activity(*, company_id: int, actor_user_id: int | None, action: str, entity_id: int | None, description: str) -> None:
    try:
        from backend.services.activity_log_service import activity_log_service
        activity_log_service.record(
            company_id=company_id, actor_user_id=actor_user_id, action=action,
            entity_type="role_permission", entity_id=entity_id, description=description,
        )
    except Exception:
        pass


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


def _require_owner_to_grant_owner(conn, *, current_user: dict, company_id: int, role) -> None:
    """Only an existing Owner (or a platform super admin) may grant the Owner
    role. `_require_access_admin` lets ANY holder of the single `users.manage`
    permission reach the assignment endpoints — without this, a delegated
    admin who was only ever granted `users.manage` could self-promote (or
    promote anyone) to Owner, which is exempt from the last-admin lockout and
    from ever having its permissions edited (see update_role) — permanent,
    un-demotable, unrestricted access exceeding what their role was ever meant
    to have."""
    if role["code"] != "owner" or current_user.get("is_super_admin"):
        return
    caller_is_owner = conn.execute(
        """
        SELECT 1 FROM company_users cu
        JOIN roles r ON r.id = cu.role_id
        WHERE cu.company_id = ? AND cu.user_id = ? AND r.code = 'owner'
        LIMIT 1
        """,
        (company_id, current_user["id"]),
    ).fetchone()
    if not caller_is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an existing Owner can grant the Owner role.",
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
            user["permission_overrides"] = auth_service.list_permission_overrides(
                company_id=company_id, user_id=user["id"],
            )

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
    _require_grantable_permissions(
        current_user=current_user, company_id=company_id, permission_codes=payload.permission_codes,
    )

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
    _log_activity(
        company_id=company_id, actor_user_id=current_user.get("id"), action="role_created",
        entity_id=role_id, description=f'Created role "{payload.name.strip()}"',
    )
    return {"success": True, "role_id": role_id}


def _admin_capable_user_exists(
    conn,
    company_id: int,
    *,
    exclude_user_id: int | None = None,
    treat_role_as_non_admin: int | None = None,
) -> bool:
    """SECURITY: last-admin lockout guard. True if at least one ACTIVE
    company member (optionally excluding one user, or treating one role
    as if it had lost its admin power) would still be able to manage
    users — via the owner role, or a role carrying users.manage. Without
    this, an admin could demote the last admin (or strip users.manage
    from the last admin-carrying role) and permanently lock the company
    out of its own user management."""
    rows = conn.execute(
        """
        SELECT cu.user_id, cu.role_id, r.code AS role_code
        FROM company_users cu
        JOIN roles r ON r.id = cu.role_id
        WHERE cu.company_id = ? AND cu.status = 'active'
        """,
        (company_id,),
    ).fetchall()

    for row in rows:
        if exclude_user_id is not None and row["user_id"] == exclude_user_id:
            continue
        if row["role_code"] == "owner":
            return True
        if (
            treat_role_as_non_admin is not None
            and row["role_id"] == treat_role_as_non_admin
        ):
            continue
        has_manage = conn.execute(
            """
            SELECT 1 FROM role_permissions rp
            JOIN permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = ? AND p.code = 'users.manage'
            LIMIT 1
            """,
            (row["role_id"],),
        ).fetchone()
        if has_manage:
            return True
    return False


def _require_grantable_permissions(
    *, current_user: dict, company_id: int, permission_codes: list[str], existing_codes: list[str] | None = None,
) -> None:
    """SECURITY: a holder of `users.manage` (a deliberately limited
    "delegated admin" permission) must never be able to mint a NEW role
    carrying permissions they don't themselves hold, then self-assign it —
    that is a full privilege-escalation bypass of the entire granular-
    permission model (identical blast radius to the Owner-role escalation
    already fixed elsewhere in this file, just routed around the
    `code == 'owner'` special case via a fresh role instead).

    Only the NEWLY-ADDED codes (permission_codes minus whatever the role
    already had, when editing) are checked against the caller — removing
    permissions from a role (including one's own) must always be allowed
    regardless of what the caller currently holds, otherwise an admin could
    get stuck unable to edit their own role at all once it holds anything
    they don't personally have (e.g. narrowing their own role down)."""
    if current_user.get("is_super_admin"):
        return
    with db.connect() as conn:
        caller_is_owner = conn.execute(
            """
            SELECT 1 FROM company_users cu
            JOIN roles r ON r.id = cu.role_id
            WHERE cu.company_id = ? AND cu.user_id = ? AND r.code = 'owner'
            LIMIT 1
            """,
            (company_id, current_user["id"]),
        ).fetchone()
    if caller_is_owner:
        return
    newly_added = set(permission_codes) - set(existing_codes or [])
    not_held = [
        code for code in newly_added
        if not auth_service.has_permission(current_user["id"], company_id, code, False)
    ]
    if not_held:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You cannot grant a role permissions you don't hold yourself: "
                + ", ".join(sorted(not_held))
            ),
        )


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
        if payload.permission_codes is not None:
            existing_codes = [
                r["code"] for r in conn.execute(
                    "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = ?",
                    (role_id,),
                ).fetchall()
            ]
            _require_grantable_permissions(
                current_user=current_user, company_id=company_id,
                permission_codes=payload.permission_codes, existing_codes=existing_codes,
            )
        # Last-admin guard: stripping users.manage from this role must not
        # leave the company with no active member able to manage users.
        if (
            payload.permission_codes is not None
            and "users.manage" not in payload.permission_codes
            and not _admin_capable_user_exists(
                conn, company_id, treat_role_as_non_admin=role_id
            )
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This change would leave the company with no "
                    "administrator able to manage users. Grant another "
                    "role users.manage (or keep it here) first."
                ),
            )
        conn.execute("""
            UPDATE roles
            SET name = COALESCE(?, name), description = COALESCE(?, description)
            WHERE id = ?
        """, (payload.name, payload.description, role_id))
        if payload.permission_codes is not None:
            _set_role_permissions(conn, role_id, payload.permission_codes)
        conn.commit()
    _log_activity(
        company_id=company_id, actor_user_id=current_user.get("id"), action="role_updated",
        entity_id=role_id, description=f'Updated role "{role["name"]}"' + (" (permissions changed)" if payload.permission_codes is not None else ""),
    )
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
        role = conn.execute(
            "SELECT id, code FROM roles WHERE id = ? AND company_id = ?", (payload.role_id, company_id)
        ).fetchone()
        if not role:
            raise HTTPException(status_code=400, detail="Selected role is invalid.")
        _require_owner_to_grant_owner(conn, current_user=current_user, company_id=company_id, role=role)

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
        role = conn.execute(
            "SELECT id, code FROM roles WHERE id = ? AND company_id = ?", (payload.role_id, company_id)
        ).fetchone()
        if not role:
            raise HTTPException(status_code=400, detail="Selected role is invalid.")
        _require_owner_to_grant_owner(conn, current_user=current_user, company_id=company_id, role=role)

        # Last-admin guard: demoting/deactivating this user must not leave
        # the company with no active member able to manage users. The
        # check excludes the user being changed, then verifies someone
        # ELSE still holds admin capability (owner role or users.manage).
        if not _admin_capable_user_exists(conn, company_id, exclude_user_id=user_id):
            new_role_is_admin = False
            if payload.status == "active":
                new_role_code = conn.execute(
                    "SELECT code FROM roles WHERE id = ?", (payload.role_id,)
                ).fetchone()
                if new_role_code and new_role_code["code"] == "owner":
                    new_role_is_admin = True
                else:
                    new_role_is_admin = (
                        conn.execute(
                            """
                            SELECT 1 FROM role_permissions rp
                            JOIN permissions p ON p.id = rp.permission_id
                            WHERE rp.role_id = ? AND p.code = 'users.manage'
                            LIMIT 1
                            """,
                            (payload.role_id,),
                        ).fetchone()
                        is not None
                    )
            if not new_role_is_admin:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "This change would leave the company with no "
                        "administrator able to manage users. Promote "
                        "another member first."
                    ),
                )
        cursor = conn.execute("""
            UPDATE company_users
            SET role_id = ?, branch_id = ?, status = ?, departments_json = ?
            WHERE company_id = ? AND user_id = ?
        """, (payload.role_id, payload.branch_id, payload.status, json.dumps(cleaned_departments), company_id, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Company user not found.")
        conn.commit()
    return {"success": True}


def _require_company_member(company_id: int, user_id: int) -> None:
    with db.connect() as conn:
        member = conn.execute(
            "SELECT 1 FROM company_users WHERE company_id = ? AND user_id = ? LIMIT 1",
            (company_id, user_id),
        ).fetchone()
    if not member:
        raise HTTPException(status_code=404, detail="Company user not found.")


@router.get("/users/{user_id}/overrides")
def get_user_permission_overrides(user_id: int, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    _require_company_member(company_id, user_id)
    return {"overrides": auth_service.list_permission_overrides(company_id=company_id, user_id=user_id)}


@router.put("/users/{user_id}/overrides")
def put_user_permission_overrides(
    user_id: int, payload: PermissionOverridesUpdateRequest, current_user: dict = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    _require_company_member(company_id, user_id)
    auth_service.set_permission_overrides(
        company_id=company_id,
        user_id=user_id,
        overrides=[item.model_dump() for item in payload.overrides],
    )
    return {"overrides": auth_service.list_permission_overrides(company_id=company_id, user_id=user_id)}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: int, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    _require_company_member(company_id, user_id)
    try:
        temporary_password = auth_service.admin_reset_password(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _log_activity(
        company_id=company_id, actor_user_id=current_user.get("id"), action="employee_password_reset",
        entity_id=user_id, description=f"Reset password for employee #{user_id}",
    )
    return {"success": True, "temporary_password": temporary_password}


@router.post("/users/{user_id}/logout")
def force_logout_user(user_id: int, current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    _require_company_member(company_id, user_id)
    revoked = auth_service.revoke_all_user_sessions(user_id)
    _log_activity(
        company_id=company_id, actor_user_id=current_user.get("id"), action="employee_force_logout",
        entity_id=user_id, description=f"Forced logout for employee #{user_id} ({revoked} session(s) revoked)",
    )
    return {"success": True, "revoked_sessions": revoked}
