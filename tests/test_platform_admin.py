"""Tests for the SUPER ADMIN control plane.

The property this module exists to defend is one sentence: a platform
administrator manages companies but cannot read their customer data. Everything
below is a way that sentence has been broken in real systems — a console token
that also opened the customer API, a "statistics" screen that grew a preview of
recent messages, a suspension that only greyed out a row in a list.

Every test runs against real, freshly provisioned, encrypted databases. The
tenant boundary only exists at that layer, so mocking it would prove nothing.
"""

from __future__ import annotations

import pytest

from backend.security import keyring
from backend.security.keyring import InvalidWorkspaceCode


PLATFORM_PASSWORD = "PlatformAdminPass1"
EMPLOYEE_PASSWORD = "EmployeePass12345"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the platform service and both routers at the test databases."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep below: a module that has not been imported yet
    # holds no reference to rebind, and would later import the real singleton.
    # The customer routers are imported here too — the cross-token tests call
    # them, and a module first imported *after* the sweep would bind this test's
    # temporary manager permanently and corrupt every test file that runs
    # afterwards.
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.dashboard  # noqa: F401
    import backend.api.routes.platform  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.platform_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    # Modules that did `from database.manager import database_manager` hold
    # their own reference and must be rebound too, or the test silently runs
    # against the process-wide singleton and proves nothing.
    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.platform_service" in rebound
    assert "backend.services.auth_service" in rebound
    assert "backend.api.routes.dashboard" in rebound

    from backend.services.platform_service import platform_service

    return platform_service


@pytest.fixture()
def client(service):
    """The platform router and the customer routers on one app.

    Both are mounted so a token can be offered to the wrong one, which is the
    only way to prove the scopes are actually separate.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, dashboard, platform as platform_routes

    app = FastAPI()
    app.include_router(platform_routes.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)

    return TestClient(app)


def _make_user(
    email: str,
    password: str,
    *,
    full_name: str = "Test Person",
    is_super_admin: bool = False,
) -> int:
    from backend.services.auth_service import auth_service

    return auth_service.create_user(
        email=email,
        password=password,
        full_name=full_name,
        is_super_admin=is_super_admin,
    )


def _make_admin(email: str = "root@platform.example.com") -> int:
    return _make_user(
        email, PLATFORM_PASSWORD, full_name="Platform Root", is_super_admin=True
    )


def _employ(platform, company, user_id: int) -> None:
    """Make a user an active employee of a company, with no role attached."""
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        conn.execute(
            """
            INSERT INTO company_users (
                company_id, user_id, role_id, status, created_at
            )
            VALUES (?, ?, NULL, 'active', ?)
            """,
            (company["id"], user_id, utc_now_iso()),
        )
        conn.commit()


def _platform_token(client, email: str = "root@platform.example.com") -> str:
    response = client.post(
        "/api/platform/auth/login",
        json={"email": email, "password": PLATFORM_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _company_token(client, company, email: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": EMPLOYEE_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _open_with_workspace_code(manager, company_id: int, workspace_code: str):
    """Open a tenant file using only the workspace code, as an employee does.

    Goes through the code-sealed copy of the key rather than the master-sealed
    one, so this proves the code itself unlocks the data — not merely that the
    server could open the file anyway.
    """
    from sqlcipher3 import dbapi2 as sqlcipher

    with manager.control() as conn:
        row = conn.execute(
            """
            SELECT key_sealed_code, code_salt
            FROM company_databases
            WHERE company_id = ?
            """,
            (int(company_id),),
        ).fetchone()

    company_key = keyring.unwrap_with_code(
        row["key_sealed_code"],
        workspace_code,
        bytes.fromhex(row["code_salt"]),
        int(company_id),
    )

    connection = sqlcipher.connect(str(manager.tenant_path(company_id)))
    connection.row_factory = sqlcipher.Row
    connection.execute(
        f'PRAGMA key = "{keyring.sqlcipher_key_literal(company_key)}"'
    )
    return connection


# ----------------------------------------------------------------------
# The scope boundary
# ----------------------------------------------------------------------


def test_a_platform_token_is_refused_by_a_customer_endpoint(
    client, platform, alpha
):
    """Defect: one token type for both consoles.

    If the platform token also opened the customer API, the operator would be a
    single request away from every company's conversations and the per-company
    encryption would only be protecting customers from a stolen disk. The token
    is minted with no company at all and must be refused by anything that reads
    company data.
    """
    _make_admin()
    token = _platform_token(client)

    assert client.get("/api/platform/companies", headers=_bearer(token)).status_code == 200

    refused = client.get("/api/dashboard/summary", headers=_bearer(token))

    assert refused.status_code == 403
    assert "platform" in refused.json()["detail"].lower()


def test_a_company_token_is_refused_by_a_platform_endpoint(client, platform, alpha):
    """Defect: a company owner reaching the control plane.

    A company session — even a super admin's company session — must not be able
    to suspend companies, rotate codes or read the platform audit. Otherwise the
    console's guard is the `is_super_admin` flag alone, and every company login
    by that person is also a platform login.
    """
    admin_id = _make_admin()
    _employ(platform, alpha, admin_id)

    # The same human, signed into a company instead of the console, and a super
    # admin at that: the scope is what decides, not the person.
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "root@platform.example.com",
            "password": PLATFORM_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    company_token = response.json()["access_token"]

    for path in ("/api/platform/companies", "/api/platform/health", "/api/platform/audit"):
        refused = client.get(path, headers=_bearer(company_token))
        assert refused.status_code == 403, path

    assert (
        client.post(
            f"/api/platform/companies/{alpha['id']}/status",
            json={"status": "suspended"},
            headers=_bearer(company_token),
        ).status_code
        == 403
    )


def test_a_non_super_admin_cannot_obtain_a_platform_session(client, platform, alpha):
    """Defect: the console authenticating anyone with a valid password.

    A company employee's password is a company credential. It must not mint a
    platform session, and the refusal must be indistinguishable from a wrong
    password, or the login page becomes a directory of who the platform
    administrators are.
    """
    _make_admin()
    _make_user("employee@alpha.example.com", EMPLOYEE_PASSWORD)

    refused = client.post(
        "/api/platform/auth/login",
        json={"email": "employee@alpha.example.com", "password": EMPLOYEE_PASSWORD},
    )
    wrong_password = client.post(
        "/api/platform/auth/login",
        json={"email": "employee@alpha.example.com", "password": "totally-wrong-1"},
    )
    unknown_email = client.post(
        "/api/platform/auth/login",
        json={"email": "nobody@alpha.example.com", "password": EMPLOYEE_PASSWORD},
    )

    assert refused.status_code == 401
    assert refused.json()["detail"] == wrong_password.json()["detail"]
    assert refused.json()["detail"] == unknown_email.json()["detail"]
    assert "administrator" not in refused.json()["detail"].lower()


def test_every_platform_route_requires_a_token_except_login(client):
    """Defect: a route added later without the dependency.

    The router is walked rather than a hand-written list, so a new endpoint that
    forgets `Depends(get_platform_admin)` fails this test on the day it is
    written instead of the day it is exploited.
    """
    from backend.api.routes import platform as platform_routes

    unguarded = []

    for route in platform_routes.router.routes:
        if route.path == "/api/platform/auth/login":
            continue

        dependency_names = {
            dependant.call.__name__
            for dependant in route.dependant.dependencies
            if getattr(dependant, "call", None) is not None
        }

        if "get_platform_admin" not in dependency_names:
            unguarded.append(route.path)

    assert unguarded == []

    # And the guard is real over the wire, not only in the signature.
    assert client.get("/api/platform/companies").status_code in (401, 403)
    assert client.get("/api/platform/health").status_code in (401, 403)


# ----------------------------------------------------------------------
# The tenant boundary
# ----------------------------------------------------------------------


def test_the_platform_api_never_returns_customer_row_data(
    client, service, platform, alpha
):
    """Defect: statistics growing a preview of what customers wrote.

    `company_statistics` is the only place in the whole service that opens a
    tenant database, and it is allowed to return counts and a file size. A name,
    a phone number, a subject line or "just the most recent message" would walk
    around the encryption the platform sells. This asserts on the shape rather
    than on a blocklist of words: anything that is not a number fails.
    """
    from database.manager import utc_now_iso

    secret = "Hala, my number is 03-123456 and my order is late"

    now = utc_now_iso()

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, phone,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (alpha["id"], "Rita Haddad", "03123456", now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO conversations (
                company_id, channel, external_user_id, status,
                created_at, updated_at
            )
            VALUES (?, 'messenger', 'EXT-1', 'ai_handling', ?, ?)
            """,
            (alpha["id"], now, now),
        )
        conn.execute(
            """
            INSERT INTO messages (
                company_id, conversation_id, channel, external_user_id,
                direction, body, created_at
            )
            VALUES (?, 1, 'messenger', 'EXT-1', 'inbound', ?, ?)
            """,
            (alpha["id"], secret, now),
        )
        conn.commit()

    statistics = service.company_statistics(alpha["id"])

    assert statistics["customers"] == 1
    assert statistics["conversations"] == 1
    assert statistics["messages"] == 1
    assert statistics["database_bytes"] > 0

    for key, value in statistics.items():
        assert isinstance(value, int) and not isinstance(value, bool), key

    # The same must hold of what the API actually serves.
    _make_admin()
    token = _platform_token(client)

    detail = client.get(
        f"/api/platform/companies/{alpha['id']}", headers=_bearer(token)
    )
    assert detail.status_code == 200

    body = detail.json()
    assert all(isinstance(value, int) for value in body["statistics"].values())
    assert secret not in detail.text
    assert "Rita Haddad" not in detail.text
    assert "03123456" not in detail.text

    listing = client.get("/api/platform/companies", headers=_bearer(token))
    assert secret not in listing.text
    assert "Rita Haddad" not in listing.text


def test_the_service_opens_a_tenant_database_in_exactly_one_place(service):
    """Defect: a second tenant-opening code path added quietly.

    The rule is not "be careful", it is "there is one function". Reading the
    source keeps that checkable: if `database_manager.tenant(` appears anywhere
    other than inside `company_statistics`, the boundary now has two doors and
    only one of them has been reviewed.
    """
    import inspect

    import backend.services.platform_service as module

    source_lines = inspect.getsource(module).splitlines()
    hits = [
        line.strip()
        for line in source_lines
        if "database_manager.tenant(" in line and not line.strip().startswith("#")
    ]

    assert len(hits) == 1, hits

    statistics_source = inspect.getsource(module.PlatformService.company_statistics)
    assert "database_manager.tenant(" in statistics_source


# ----------------------------------------------------------------------
# Provisioning
# ----------------------------------------------------------------------


def test_creating_a_company_provisions_a_database_the_code_unlocks(
    client, service, platform
):
    """Defect: a console that creates a row and calls it a company.

    Onboarding is only real if the new company has an encrypted database and the
    code handed to its owner actually opens it. This unwraps the key with the
    returned code and reads the schema through it, so a code that were merely
    stored as a label would fail here.
    """
    _make_admin()
    token = _platform_token(client)

    response = client.post(
        "/api/platform/companies",
        json={
            "name": "Gamma Retail",
            "slug": "gamma",
            "workspace": "Gamma Group",
            "owner_email": "owner@gamma.example.com",
            "owner_name": "Gamma Owner",
            "owner_password": EMPLOYEE_PASSWORD,
            "plan_code": "starter",
        },
        headers=_bearer(token),
    )

    assert response.status_code == 201, response.text
    created = response.json()

    company_id = created["company_id"]
    workspace_code = created["workspace_code"]

    assert workspace_code
    assert platform["manager"].tenant_path(company_id).exists()

    connection = _open_with_workspace_code(
        platform["manager"], company_id, workspace_code
    )
    try:
        assert (
            int(connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
            == 0
        )
        assert (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM company_settings"
                ).fetchone()[0]
            )
            > 0
        )
    finally:
        connection.close()

    # Roles, the owner and the plan came with it, or the company cannot be used.
    with platform["manager"].control() as conn:
        roles = {
            row["code"]
            for row in conn.execute(
                "SELECT code FROM roles WHERE company_id = ?", (company_id,)
            ).fetchall()
        }
        owner = conn.execute(
            """
            SELECT users.email
            FROM company_users
            JOIN users ON users.id = company_users.user_id
            JOIN roles ON roles.id = company_users.role_id
            WHERE company_users.company_id = ? AND roles.code = 'owner'
            """,
            (company_id,),
        ).fetchone()

    assert {"owner", "manager", "agent", "viewer"} <= roles
    assert owner["email"] == "owner@gamma.example.com"

    listed = client.get("/api/platform/companies", headers=_bearer(token)).json()
    entry = next(item for item in listed["items"] if item["id"] == company_id)
    assert entry["owner_email"] == "owner@gamma.example.com"
    assert entry["database_exists"] is True
    assert entry["plan_code"] == "starter"

    # The code is shown once. It is not recoverable from any later read.
    detail = client.get(
        f"/api/platform/companies/{company_id}", headers=_bearer(token)
    )
    assert workspace_code not in detail.text


def test_a_failed_creation_leaves_no_orphan_row_and_no_orphan_file(
    client, service, platform
):
    """Defect: a half-created company.

    A company row with no database serves nothing but holds its slug hostage; a
    database file with no company row is unreachable ciphertext nobody will ever
    delete. Either half surviving a failure means the operator's retry fails too
    and the platform accumulates rubble it cannot identify.
    """
    _make_admin()
    token = _platform_token(client)

    def explode(self, conn, company_id):
        raise RuntimeError("role seeding failed")

    # Patched and restored by hand rather than with `monkeypatch`: this test
    # shares that fixture's instance with the `service` fixture, so undoing it
    # mid-test would also unbind the temporary database manager and point the
    # retry below at the real platform.
    original_seed = type(service)._seed_company_roles
    type(service)._seed_company_roles = explode

    try:
        failed = client.post(
            "/api/platform/companies",
            json={
                "name": "Delta Foods",
                "slug": "delta",
                "workspace": "Delta Group",
                "owner_email": "owner@delta.example.com",
                "owner_name": "Delta Owner",
                "owner_password": EMPLOYEE_PASSWORD,
            },
            headers=_bearer(token),
        )
    finally:
        type(service)._seed_company_roles = original_seed

    assert failed.status_code >= 400

    with platform["manager"].control() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS total FROM companies WHERE slug = 'delta'"
            ).fetchone()["total"]
            == 0
        )
        orphan_databases = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM company_databases
            LEFT JOIN companies ON companies.id = company_databases.company_id
            WHERE companies.id IS NULL
            """
        ).fetchone()["total"]

    assert orphan_databases == 0

    tenant_dir = platform["manager"].tenant_path(1).parent
    registered = {
        platform["manager"].tenant_path(company["id"]).name
        for company in platform["companies"].values()
    }
    leftovers = [
        path.name
        for path in tenant_dir.iterdir()
        if path.name.split("-")[0] not in registered
    ]

    assert leftovers == [], leftovers

    # And the retry the operator makes next actually works.
    retried = client.post(
        "/api/platform/companies",
        json={
            "name": "Delta Foods",
            "slug": "delta",
            "workspace": "Delta Group",
            "owner_email": "owner@delta.example.com",
            "owner_name": "Delta Owner",
            "owner_password": EMPLOYEE_PASSWORD,
        },
        headers=_bearer(token),
    )

    assert retried.status_code == 201, retried.text


# ----------------------------------------------------------------------
# Suspension and codes
# ----------------------------------------------------------------------


def test_suspending_a_company_stops_its_employees_signing_in(
    client, service, platform, alpha, beta
):
    """Defect: a suspension that only greys out a row in the console.

    Non-payment and abuse handling are worth nothing if the company keeps
    working. Sign-in must fail for that company's employees, the tokens they
    already hold must stop working, and no other company may be affected.
    """
    admin_id = _make_admin()
    _make_user("employee@alpha.example.com", EMPLOYEE_PASSWORD)
    _make_user("employee@beta.example.com", EMPLOYEE_PASSWORD)

    with platform["manager"].control() as conn:
        alpha_employee = conn.execute(
            "SELECT id FROM users WHERE email = 'employee@alpha.example.com'"
        ).fetchone()["id"]
        beta_employee = conn.execute(
            "SELECT id FROM users WHERE email = 'employee@beta.example.com'"
        ).fetchone()["id"]

    _employ(platform, alpha, alpha_employee)
    _employ(platform, beta, beta_employee)

    live_token = _company_token(client, alpha, "employee@alpha.example.com")
    assert client.get("/api/auth/me", headers=_bearer(live_token)).status_code == 200

    token = _platform_token(client)
    suspended = client.post(
        f"/api/platform/companies/{alpha['id']}/status",
        json={"status": "suspended", "reason": "non-payment"},
        headers=_bearer(token),
    )
    assert suspended.status_code == 200, suspended.text

    refused = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": EMPLOYEE_PASSWORD,
        },
    )
    assert refused.status_code == 401

    # The service the login endpoint calls agrees, so this is not a route quirk.
    from backend.services.auth_service import auth_service

    assert (
        auth_service.authenticate(
            workspace_code=alpha["workspace_code"],
            company=alpha["name"],
            email="employee@alpha.example.com",
            password=EMPLOYEE_PASSWORD,
        )
        is None
    )

    # The session issued before the suspension is dead too.
    assert client.get("/api/auth/me", headers=_bearer(live_token)).status_code == 401

    # The other company on the same server is untouched.
    assert _company_token(client, beta, "employee@beta.example.com")

    # And reactivation puts it back, or suspension would be a one-way door.
    reactivated = client.post(
        f"/api/platform/companies/{alpha['id']}/status",
        json={"status": "active"},
        headers=_bearer(token),
    )
    assert reactivated.status_code == 200
    assert _company_token(client, alpha, "employee@alpha.example.com")


def test_rotating_the_code_invalidates_the_old_one_and_keeps_the_data(
    client, service, platform, alpha
):
    """Defect: a rotation that re-keys nothing, or one that loses the data.

    A leaked workspace code has to be revocable, so the old one must stop
    opening the company. The other half matters just as much: the company's
    database is not re-encrypted, so everything written before the rotation must
    still be readable through the new code.
    """
    from database.manager import utc_now_iso

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items (
                company_id, title, content_ar, status, created_at, updated_at
            )
            VALUES (?, 'Opening hours', 'من 9 لـ 6', 'active', ?, ?)
            """,
            (alpha["id"], utc_now_iso(), utc_now_iso()),
        )
        conn.commit()

    old_code = alpha["workspace_code"]

    _make_admin()
    token = _platform_token(client)

    rotated = client.post(
        f"/api/platform/companies/{alpha['id']}/workspace-code/rotate",
        headers=_bearer(token),
    )
    assert rotated.status_code == 200, rotated.text

    new_code = rotated.json()["workspace_code"]
    assert new_code != old_code

    assert platform["manager"].verify_workspace_code(alpha["id"], old_code) is False
    assert platform["manager"].verify_workspace_code(alpha["id"], new_code) is True

    with pytest.raises(InvalidWorkspaceCode):
        _open_with_workspace_code(platform["manager"], alpha["id"], old_code)

    connection = _open_with_workspace_code(platform["manager"], alpha["id"], new_code)
    try:
        row = connection.execute(
            "SELECT title, content_ar FROM knowledge_items"
        ).fetchone()
        assert row["title"] == "Opening hours"
        assert row["content_ar"] == "من 9 لـ 6"
    finally:
        connection.close()

    # An employee signing in with the old code is refused; the new one works.
    _make_user("employee@alpha.example.com", EMPLOYEE_PASSWORD)
    with platform["manager"].control() as conn:
        employee_id = conn.execute(
            "SELECT id FROM users WHERE email = 'employee@alpha.example.com'"
        ).fetchone()["id"]
    _employ(platform, alpha, employee_id)

    stale = client.post(
        "/api/auth/login",
        json={
            "workspace_code": old_code,
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": EMPLOYEE_PASSWORD,
        },
    )
    assert stale.status_code == 401

    fresh = client.post(
        "/api/auth/login",
        json={
            "workspace_code": new_code,
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": EMPLOYEE_PASSWORD,
        },
    )
    assert fresh.status_code == 200


# ----------------------------------------------------------------------
# Platform administrators
# ----------------------------------------------------------------------


def test_revoking_the_last_platform_admin_is_refused(client, service, platform):
    """Defect: locking every human out of the console.

    Platform rights can only be granted from this console, so revoking the final
    administrator — usually by revoking your own — leaves nobody who can grant
    them back, and recovery means shell access and the CLI. Refusing while one
    remains is the only cheap moment to catch it.
    """
    admin_id = _make_admin()
    token = _platform_token(client)

    refused = client.post(
        f"/api/platform/admins/{admin_id}/revoke", headers=_bearer(token)
    )

    assert refused.status_code == 409
    assert "last platform administrator" in refused.json()["detail"].lower()

    # Still an administrator, still able to work.
    assert client.get("/api/platform/companies", headers=_bearer(token)).status_code == 200

    # With a second administrator in place the same call is allowed.
    second_id = _make_user("second@platform.example.com", PLATFORM_PASSWORD)
    granted = client.post(
        f"/api/platform/admins/{second_id}/grant", headers=_bearer(token)
    )
    assert granted.status_code == 200

    allowed = client.post(
        f"/api/platform/admins/{admin_id}/revoke", headers=_bearer(token)
    )
    assert allowed.status_code == 200

    # Revocation takes effect on the next request rather than at token expiry:
    # the session is killed outright (401) and, were it not, `get_platform_admin`
    # re-checks the flag and would refuse it (403).
    assert client.get(
        "/api/platform/companies", headers=_bearer(token)
    ).status_code in (401, 403)

    # And the survivor cannot now revoke themselves either.
    survivor_token = _platform_token(client, "second@platform.example.com")
    assert (
        client.post(
            f"/api/platform/admins/{second_id}/revoke",
            headers=_bearer(survivor_token),
        ).status_code
        == 409
    )


# ----------------------------------------------------------------------
# Platform configuration
# ----------------------------------------------------------------------


def test_unknown_module_keys_are_rejected(client, service, platform, alpha):
    """Defect: storing a typo as if it were a decision.

    `"conversatoins": false` disables nothing, reads back to the console looking
    like a setting that was applied, and is only ever discovered by the company
    still using the module the operator believes they switched off.
    """
    from backend.services.platform_service import PLATFORM_MODULES, PlatformError

    _make_admin()
    token = _platform_token(client)

    rejected = client.put(
        f"/api/platform/companies/{alpha['id']}/config",
        json={"modules": {"conversations": False, "conversatoins": False}},
        headers=_bearer(token),
    )

    assert rejected.status_code == 400
    assert "conversatoins" in rejected.json()["detail"]

    # Nothing was written: a rejected request must not half-apply.
    config = client.get(
        f"/api/platform/companies/{alpha['id']}/config", headers=_bearer(token)
    ).json()
    assert config["modules"]["conversations"] is True

    with pytest.raises(PlatformError):
        service.update_platform_config(alpha["id"], modules={"nope": True})

    with pytest.raises(PlatformError):
        service.update_platform_config(alpha["id"], layout={"make_it_pretty": True})

    with pytest.raises(PlatformError):
        service.update_platform_config(alpha["id"], branding={"colour": "#fff"})

    with pytest.raises(PlatformError):
        service.update_platform_config(
            alpha["id"], branding={"primary_color": "not-a-colour"}
        )

    # A real edit against real keys goes through and comes back.
    accepted = client.put(
        f"/api/platform/companies/{alpha['id']}/config",
        json={
            "modules": {"scheduler": False},
            "branding": {"brand_name": "Alpha Care", "primary_color": "#1B2A4A"},
            "layout": {"dense_tables": True},
        },
        headers=_bearer(token),
    )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["modules"]["scheduler"] is False
    assert body["modules"]["conversations"] is True
    assert body["branding"]["brand_name"] == "Alpha Care"
    assert body["layout"]["dense_tables"] is True
    assert set(body["modules"]) == set(PLATFORM_MODULES)


# ----------------------------------------------------------------------
# Health and audit
# ----------------------------------------------------------------------


def test_health_reports_a_company_whose_database_cannot_be_opened(
    client, service, platform, alpha, beta
):
    """Defect: a console that reports healthy while a company is unserved.

    A missing or unrestorable database file is invisible from the control plane
    — the company row still looks perfect. Health has to actually try each one,
    and it does that through the single tenant-opening method rather than adding
    a second door.
    """
    _make_admin()
    token = _platform_token(client)

    healthy = client.get("/api/platform/health", headers=_bearer(token)).json()
    assert healthy["companies"] == 2
    assert healthy["provisioned_databases"] == 2
    assert healthy["unreadable_databases"] == []
    assert healthy["healthy"] is True

    for suffix in ("", "-wal", "-shm"):
        path = platform["manager"].tenant_path(beta["id"])
        candidate = type(path)(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()

    broken = client.get("/api/platform/health", headers=_bearer(token)).json()

    assert [item["company_id"] for item in broken["unreadable_databases"]] == [
        beta["id"]
    ]
    assert broken["healthy"] is False
    assert broken["readable_databases"] == 1

    # The healthy company is still fully usable from the console.
    assert (
        client.get(
            f"/api/platform/companies/{alpha['id']}", headers=_bearer(token)
        ).status_code
        == 200
    )


def test_every_mutating_operation_writes_an_audit_row(
    client, service, platform, alpha
):
    """Defect: platform actions nobody can reconstruct afterwards.

    These operations create companies, cut off logins and change encryption
    codes. Without an audit row naming the actor there is no answer to "who
    suspended this customer, and when" — which is exactly the question asked
    after it happens.
    """
    admin_id = _make_admin()
    token = _platform_token(client)

    client.post(
        "/api/platform/companies",
        json={
            "name": "Epsilon Ltd",
            "slug": "epsilon",
            "workspace": "Epsilon Group",
            "owner_email": "owner@epsilon.example.com",
            "owner_name": "Epsilon Owner",
            "owner_password": EMPLOYEE_PASSWORD,
        },
        headers=_bearer(token),
    )
    client.post(
        f"/api/platform/companies/{alpha['id']}/status",
        json={"status": "suspended"},
        headers=_bearer(token),
    )
    client.post(
        f"/api/platform/companies/{alpha['id']}/workspace-code/rotate",
        headers=_bearer(token),
    )
    client.post(
        f"/api/platform/companies/{alpha['id']}/plan",
        json={"plan_code": "business"},
        headers=_bearer(token),
    )
    client.put(
        f"/api/platform/companies/{alpha['id']}/config",
        json={"modules": {"catalogue": False}},
        headers=_bearer(token),
    )

    audit = client.get(
        "/api/platform/audit?limit=100", headers=_bearer(token)
    ).json()
    actions = {item["action"] for item in audit["items"]}

    assert {
        "company.created",
        "company.suspended",
        "company.workspace_code_rotated",
        "company.plan_assigned",
        "company.platform_config_updated",
    } <= actions

    assert all(
        item["actor_user_id"] == admin_id
        for item in audit["items"]
        if item["action"] != "platform.signed_in"
    )

    # The audit table is shared across companies, so it must carry no content
    # from inside one.
    assert "content" not in audit["items"][0]
