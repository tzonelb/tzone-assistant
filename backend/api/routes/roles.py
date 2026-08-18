from fastapi import APIRouter, Depends, HTTPException, Request, status

from sqlcipher3 import dbapi2 as sqlcipher

from backend.api.schemas.roles import (
    BranchCreateRequest,
    BranchUpdateRequest,
    RoleCreateRequest,
    RoleUpdateRequest,
    UserAssignmentRequest,
    UserCreateRequest,
)
from backend.services import mailer
from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    get_current_user,
    require_permission,
)
from backend.services.plan_service import PlanLimitExceeded, plan_service
from config.settings import config
from database.manager import database_manager, utc_now_iso


router = APIRouter(prefix="/api/admin/access", tags=["Roles and Permissions"])


def _company_id(current_user: dict) -> int:
    return auth_service.resolve_company_id(current_user)


def _active_member_count(conn, company_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total FROM company_users
        WHERE company_id = ? AND status = 'active'
        """,
        (int(company_id),),
    ).fetchone()

    return int(row["total"]) if row else 0


def _assert_seat_available(conn, company_id: int) -> None:
    """Refuse a seat the plan does not have.

    Counted on active memberships, and checked on **both** paths that can
    produce one. Creating a user is the obvious one; the other is
    `PATCH /users/{id}` setting an existing membership back to `active`, which
    is a seat appearing without an INSERT. Guarding only the create would leave
    a plan limit that anybody could step around by disabling a member and
    re-enabling them.
    """
    try:
        plan_service.check(company_id, "max_users", _active_member_count(conn, company_id))
    except PlanLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=exc.as_detail(),
        ) from exc


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

    with database_manager.control() as conn:
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
                users.password_changed_at,
                users.must_change_password,
                -- The screen cannot offer "unlock" on the accounts that need
                -- it without knowing which those are. `user_status` is the
                -- account's platform status and says nothing about a lockout.
                users.locked_until,
                company_users.status AS membership_status,
                company_users.branch_id,
                roles.id AS role_id,
                roles.name AS role_name,
                roles.code AS role_code,
                branches.name AS branch_name
            FROM company_users
            JOIN users ON users.id = company_users.user_id
            LEFT JOIN roles ON roles.id = company_users.role_id
            LEFT JOIN branches
                   ON branches.id = company_users.branch_id
                  AND branches.company_id = company_users.company_id
            WHERE company_users.company_id = ?
            ORDER BY users.full_name, users.email
        """, (company_id,)).fetchall()]

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
    }


@router.post("/roles")
def create_role(
    payload: RoleCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    with database_manager.control() as conn:
        try:
            cursor = conn.execute("""
                INSERT INTO roles (
                    company_id, name, code, description, is_system, created_at
                )
                VALUES (?, ?, ?, ?, 0, ?)
            """, (
                company_id,
                payload.name.strip(),
                payload.code.strip().lower(),
                payload.description,
                utc_now_iso(),
            ))
        # `sqlcipher.IntegrityError`, not `sqlite3.IntegrityError` — these
        # connections come from the SQLCipher driver and its exception classes
        # are a separate hierarchy. `database/manager.py` catches
        # `sqlcipher.Error` for the same reason.
        #
        # Narrow, and it has to be. This was `except Exception` reporting
        # "A role with this code already exists", which turned every failure
        # into that one sentence — including the one that was actually
        # happening: the INSERT omitted `created_at`, which is NOT NULL with no
        # default, so creating a role raised IntegrityError every single time
        # and answered with a plausible lie about duplicate codes. Catching
        # broadly is what let a completely broken button look like a validation
        # message for as long as it did.
        except sqlcipher.IntegrityError as exc:
            if "UNIQUE" not in str(exc).upper():
                raise

            raise HTTPException(
                status_code=409,
                detail="A role with this code already exists.",
            ) from exc

        role_id = cursor.lastrowid
        _set_role_permissions(conn, role_id, payload.permission_codes)
        conn.commit()

    # A grant is a security event, mirrored to the control plane. Deciding who
    # may read a customer file or replace a channel credential is the change
    # most worth being able to review after the fact, and nothing recorded it.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.ROLE_CREATED,
        category="roles",
        kind="security",
        target_type="role",
        target_id=role_id,
        summary=f"Created the role {payload.name}",
        after={"code": payload.code, "permissions": sorted(payload.permission_codes)},
        severity="notice",
        ip_address=client_ip(request),
    )

    return {"success": True, "role_id": role_id}



def _assert_branch_belongs(conn, company_id: int, branch_id) -> None:
    """A branch id on a membership row must name a branch this company owns.

    `role_id` was checked here from the start; `branch_id` sat beside it and
    was written exactly as it arrived. Ids are global across the control
    database, so an id from another company was a valid row — and the team
    list joins the branch name back out.
    """
    if branch_id in (None, "", 0):
        return

    row = conn.execute(
        "SELECT id FROM branches WHERE id = ? AND company_id = ?",
        (int(branch_id), int(company_id)),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="Selected branch is invalid.")


def _set_role_permissions(conn, role_id: int, permission_codes: list[str]) -> None:
    """Replace a role's permissions with exactly the codes given.

    `created_at` is supplied explicitly. It is NOT NULL with no default, and
    omitting it here was worse than the same omission elsewhere in this file:
    `INSERT OR IGNORE` suppresses a NOT NULL violation just as it suppresses a
    duplicate, so every permission was silently discarded. Roles were created
    and edited successfully, reported success, and came back holding nothing.

    The OR IGNORE stays, for the duplicate it was meant for — a code listed
    twice in the request.
    """
    conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
    codes = sorted(set(permission_codes))
    if not codes:
        return
    placeholders = ",".join("?" for _ in codes)
    permission_rows = conn.execute(
        f"SELECT id FROM permissions WHERE code IN ({placeholders})", codes
    ).fetchall()
    now = utc_now_iso()
    conn.executemany(
        """
        INSERT OR IGNORE INTO role_permissions (role_id, permission_id, created_at)
        VALUES (?, ?, ?)
        """,
        [(role_id, row["id"], now) for row in permission_rows],
    )


@router.patch("/roles/{role_id}")
def update_role(
    role_id: int,
    payload: RoleUpdateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    with database_manager.control() as conn:
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

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=(
            Action.PERMISSIONS_CHANGED
            if payload.permission_codes is not None
            else Action.ROLE_UPDATED
        ),
        category="roles",
        kind="security" if payload.permission_codes is not None else "change",
        target_type="role",
        target_id=role_id,
        summary=(
            f"Changed what the {role['name']} role may do"
            if payload.permission_codes is not None
            else f"Renamed the {role['name']} role"
        ),
        after=(
            {"permissions": sorted(payload.permission_codes)}
            if payload.permission_codes is not None
            else {"name": payload.name}
        ),
        severity="notice" if payload.permission_codes is not None else "info",
        ip_address=client_ip(request),
    )

    return {"success": True}


@router.post("/users")
def create_user(
    payload: UserCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    with database_manager.control() as conn:
        role = conn.execute("SELECT id FROM roles WHERE id = ? AND company_id = ?", (payload.role_id, company_id)).fetchone()
        if not role:
            raise HTTPException(status_code=400, detail="Selected role is invalid.")
        # The same check the role gets. Without it a branch id belonging to
        # another company was stored on this company's membership row and its
        # name came back through the team list.
        _assert_branch_belongs(conn, company_id, payload.branch_id)
        # Before the account is created, not after. `create_user` writes a row
        # to the shared `users` table; refusing afterwards would leave an
        # orphaned account belonging to no company, and the same email could
        # then never be used again.
        _assert_seat_available(conn, company_id)
    try:
        user_id = auth_service.create_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            phone=payload.phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    with database_manager.control() as conn:
        conn.execute("""
            INSERT INTO company_users (
                company_id, user_id, role_id, branch_id, status, created_at
            )
            VALUES (?, ?, ?, ?, 'active', ?)
        """, (company_id, user_id, payload.role_id, payload.branch_id, utc_now_iso()))
        conn.commit()

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.USER_ADDED,
        category="roles",
        kind="security",
        target_type="user",
        target_id=user_id,
        summary=f"Added {payload.full_name or payload.email} to the team",
        after={"role_id": payload.role_id, "branch_id": payload.branch_id},
        severity="notice",
        ip_address=client_ip(request),
    )

    return {"success": True, "user_id": user_id}


@router.patch("/users/{user_id}")
def update_user_assignment(
    user_id: int,
    payload: UserAssignmentRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    if user_id == current_user["id"] and payload.status != "active":
        raise HTTPException(status_code=400, detail="You cannot disable your own membership.")
    with database_manager.control() as conn:
        role = conn.execute("SELECT id FROM roles WHERE id = ? AND company_id = ?", (payload.role_id, company_id)).fetchone()
        if not role:
            raise HTTPException(status_code=400, detail="Selected role is invalid.")
        # The same check the role gets. Without it a branch id belonging to
        # another company was stored on this company's membership row and its
        # name came back through the team list.
        _assert_branch_belongs(conn, company_id, payload.branch_id)

        existing = conn.execute(
            "SELECT status FROM company_users WHERE company_id = ? AND user_id = ? LIMIT 1",
            (company_id, user_id),
        ).fetchone()

        # Only when this actually adds a seat. Re-saving an already-active
        # member — a role change, a branch change — must not be refused for
        # occupying the seat it already occupies.
        if (
            payload.status == "active"
            and existing
            and str(existing["status"]) != "active"
        ):
            _assert_seat_available(conn, company_id)

        cursor = conn.execute("""
            UPDATE company_users
            SET role_id = ?, branch_id = ?, status = ?
            WHERE company_id = ? AND user_id = ?
        """, (payload.role_id, payload.branch_id, payload.status, company_id, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Company user not found.")
        conn.commit()

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.USER_UPDATED,
        category="roles",
        kind="security",
        target_type="user",
        target_id=user_id,
        summary=f"Changed the access of team member {user_id}",
        before={"status": dict(existing)["status"] if existing else None},
        after={
            "role_id": payload.role_id,
            "branch_id": payload.branch_id,
            "status": payload.status,
        },
        severity="notice",
        ip_address=client_ip(request),
    )

    return {"success": True}


# ----------------------------------------------------------------------
# Account recovery
# ----------------------------------------------------------------------


def _assert_member(conn, company_id: int, user_id: int) -> dict:
    """Confirm the target belongs to this company before acting on them.

    Without it, `users.manage` in one company would be a lever on any account
    on the platform — the user id comes from the URL, and `users` is a shared
    control-plane table.
    """
    row = conn.execute(
        """
        SELECT users.id, users.email, users.full_name
        FROM company_users
        JOIN users ON users.id = company_users.user_id
        WHERE company_users.company_id = ? AND company_users.user_id = ?
        LIMIT 1
        """,
        (company_id, user_id),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Company user not found.")

    return dict(row)


@router.post("/users/{user_id}/force-password-reset")
def force_password_reset(
    user_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Send this employee a single-use link to set a new password.

    This is the unlock path. A locked account does not have to wait out its
    timer — setting a new password clears the lock — which is what makes a full
    account lock safe to have: without it, five requests against a known address
    would be a free "disable this employee" button.

    The administrator never learns the password. They cause a link to be sent;
    the employee chooses what to set. Every existing session of that employee is
    ended immediately, before the link is even used, because the reason for a
    forced reset is usually that somebody else may be holding the account.
    """
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    # Refuse before doing anything if the mail cannot go out. Reporting success
    # for a message nobody will receive leaves two people believing the account
    # is recoverable when it is not.
    try:
        mailer.assert_configured()
    except mailer.MailerNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    with database_manager.control() as conn:
        target = _assert_member(conn, company_id, user_id)

    token = auth_service.create_password_reset(
        user_id=user_id,
        created_by_user_id=int(current_user["id"]),
        ip_address=client_ip(request),
    )

    auth_service.revoke_all_user_sessions(user_id)
    auth_service.unlock_account(user_id=user_id)

    link = f"{config.APP_PUBLIC_URL.rstrip('/')}/reset-password/{token}"
    minutes = config.PASSWORD_RESET_TTL_MINUTES

    result = mailer.send(
        to=target["email"],
        subject="Set a new password for your T-ZONE account",
        body=(
            f"Hello {target['full_name'] or ''},\n\n"
            "An administrator at your company asked us to help you set a new "
            "password. Open this link to choose one:\n\n"
            f"  {link}\n\n"
            f"The link works once and expires in {minutes} minutes.\n\n"
            "If you did not expect this, tell your administrator — somebody "
            "asked for it on your behalf.\n"
        ),
    )

    if not result.delivered:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The reset link could not be sent. {result.reason}",
        )

    # After the link is actually sent, not before. A forced reset that failed
    # to deliver ends every one of that employee's sessions and gives them no
    # way back, and recording it as done would hide exactly that.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.USER_PASSWORD_RESET,
        category="roles",
        kind="security",
        target_type="user",
        target_id=user_id,
        summary=f"Forced a password reset for {target['email']}",
        severity="warning",
        ip_address=client_ip(request),
    )

    return {
        "success": True,
        "message": f"A reset link was sent to {target['email']}.",
    }


@router.post("/users/{user_id}/unlock")
def unlock_user(
    user_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Clear a lockout without touching the password.

    For the ordinary case: an employee mistyped their password five times and
    remembers it perfectly well. Forcing a reset on them would be theatre.
    """
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    with database_manager.control() as conn:
        _assert_member(conn, company_id, user_id)

    auth_service.unlock_account(user_id=user_id)

    # An account is locked because five sign-ins failed, which is either a
    # forgetful employee or somebody guessing at their password. Who reopened
    # it, and when, is the other half of that record.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.USER_UNLOCKED,
        category="roles",
        kind="security",
        target_type="user",
        target_id=user_id,
        summary=f"Unlocked team member {user_id}",
        severity="notice",
        ip_address=client_ip(request),
    )

    return {"success": True, "message": "Account unlocked."}


# ---------------------------------------------------------------------------
# Branches
#
# `branches` was read in four places and written in none. Two screens already
# render the list — the branch selector on every team member, and the branch
# field when connecting a channel — and both were permanently empty, because
# no endpoint, service or CLI command could create a row.
#
# A company with one location needs none of this, which is why the field is
# optional everywhere it appears. A company with three shops needs to be able
# to say which one an employee works at and which one a page belongs to, and
# until now it could not.
# ---------------------------------------------------------------------------


def _branch_row(conn, company_id: int, branch_id: int):
    """The branch, or a 404 — matched with the company, never on the id alone.

    Ids are global in the control database, so an id from another company is a
    real row. Fetching on the id and checking afterwards would still have read
    it; this cannot.
    """
    row = conn.execute(
        """
        SELECT id, name, code, address, phone, status
        FROM branches
        WHERE id = ? AND company_id = ?
        LIMIT 1
        """,
        (int(branch_id), int(company_id)),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Branch not found.")

    return row


@router.get("/branches")
def list_branches(current_user: dict = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    with database_manager.control() as conn:
        rows = conn.execute(
            """
            SELECT id, name, code, address, phone, status
            FROM branches
            WHERE company_id = ?
            ORDER BY name
            """,
            (company_id,),
        ).fetchall()

    return {"branches": [dict(row) for row in rows]}


@router.post("/branches")
def create_branch(
    payload: BranchCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    now = utc_now_iso()

    with database_manager.control() as conn:
        # Within this company only. Two companies naming a branch "Main" is
        # ordinary; the same company naming two branches "Main" is a mistake
        # nobody could untangle from a dropdown afterwards.
        clash = conn.execute(
            "SELECT id FROM branches WHERE company_id = ? AND LOWER(name) = ?",
            (company_id, payload.name.strip().lower()),
        ).fetchone()

        if clash:
            raise HTTPException(
                status_code=409, detail="A branch with that name already exists."
            )

        cursor = conn.execute(
            """
            INSERT INTO branches (
                company_id, name, code, address, phone, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                company_id,
                payload.name.strip(),
                (payload.code or "").strip() or None,
                (payload.address or "").strip() or None,
                (payload.phone or "").strip() or None,
                now,
                now,
            ),
        )
        branch_id = int(cursor.lastrowid)
        conn.commit()

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.BRANCH_CREATED,
        category="roles",
        target_type="branch",
        target_id=branch_id,
        summary=f"Added the branch {payload.name.strip()}",
        after={"name": payload.name.strip(), "code": payload.code},
        ip_address=client_ip(request),
    )

    return {"success": True, "branch_id": branch_id}


@router.patch("/branches/{branch_id}")
def update_branch(
    branch_id: int,
    payload: BranchUpdateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)
    values = payload.model_dump(exclude_unset=True)

    with database_manager.control() as conn:
        before = dict(_branch_row(conn, company_id, branch_id))

        if not values:
            return {"success": True, "branch": before}

        assignments = ", ".join(f"{column} = ?" for column in values)
        conn.execute(
            f"""
            UPDATE branches
            SET {assignments}, updated_at = ?
            WHERE id = ? AND company_id = ?
            """,
            [*values.values(), utc_now_iso(), int(branch_id), company_id],
        )
        conn.commit()

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.BRANCH_UPDATED,
        category="roles",
        target_type="branch",
        target_id=branch_id,
        summary=f"Edited the branch {before['name']}",
        before={key: before.get(key) for key in values},
        after=values,
        ip_address=client_ip(request),
    )

    return {"success": True}


@router.delete("/branches/{branch_id}")
def delete_branch(
    branch_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Retire a branch, releasing whoever was pointed at it.

    Both tables that point at a branch declare
    `FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL`, and
    every control connection opens with `PRAGMA foreign_keys = ON`, so the
    database does the releasing. Repeating it here in SQL would be two
    mechanisms for one rule, and the redundant one is the one that rots.

    What that leaves worth testing is the pragma itself. With foreign keys off
    — a connection opened somewhere else, a future refactor of `control()` —
    this silently stops working and a deleted branch's id lives on in
    `company_users` and `channel_accounts`, ready to be handed to a different
    branch later. `tests/test_branches.py` asserts the release for both tables
    for that reason.
    """
    company_id = _company_id(current_user)
    _require_access_admin(current_user, company_id)

    with database_manager.control() as conn:
        before = dict(_branch_row(conn, company_id, branch_id))

        conn.execute(
            "DELETE FROM branches WHERE id = ? AND company_id = ?",
            (int(branch_id), company_id),
        )
        conn.commit()

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.BRANCH_DELETED,
        category="roles",
        target_type="branch",
        target_id=branch_id,
        summary=f"Removed the branch {before['name']}",
        before=before,
        severity="notice",
        ip_address=client_ip(request),
    )

    return {"success": True}
