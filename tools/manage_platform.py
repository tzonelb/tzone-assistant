#!/usr/bin/env python3
"""Operator CLI for the T-ZONE platform.

Run it from the project root:

    python -m tools.manage_platform <command> [options]

Every command here talks to encrypted databases, so every command needs
``TZONE_MASTER_KEY`` in the environment — except ``generate-master-key``,
which is how you get one in the first place.

Design notes for whoever maintains this:

* Imports of ``database.manager`` and ``backend.services.auth_service`` are
  deliberately lazy. ``generate-master-key`` must keep working on a machine
  where the database driver is not installed yet, and a broken application
  import must not stop an operator from reading ``--help``.
* Nothing here ever prints a traceback at an operator. Failures are raised as
  :class:`OperatorError` and rendered as a single readable line.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


# ----------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------

RULE = "=" * 68


class OperatorError(RuntimeError):
    """A failure that has a message an operator can act on."""


def out(text: str = "") -> None:
    print(text)


def err(text: str = "") -> None:
    print(text, file=sys.stderr)


def banner(title: str) -> None:
    out(RULE)
    out(title)
    out(RULE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    """Fold a display name into a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    slug = slug.strip("-")

    if not slug:
        raise OperatorError(
            f"Cannot build a slug from {value!r}. Use letters or digits."
        )

    return slug


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)

    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} GB"


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]

    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def render(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells))

    out(render(headers))
    out("  ".join("-" * width for width in widths))

    for row in rows:
        out(render(row))


# ----------------------------------------------------------------------
# Lazy imports of application modules
# ----------------------------------------------------------------------


def load_keyring():
    try:
        from backend.security import keyring
    except ImportError as exc:  # pragma: no cover - environment problem
        raise OperatorError(
            "Could not import backend.security.keyring "
            f"({exc}). Run this from the project root with the project's "
            "virtualenv active, and install requirements.txt."
        ) from exc

    return keyring


def load_manager():
    """Import the database layer, translating import failures into advice."""
    try:
        from database.manager import (  # noqa: F401
            CONTROL_FILENAME,
            TENANT_DIRNAME,
            DatabaseError,
            UnknownCompany,
            database_manager,
        )
    except ImportError as exc:
        raise OperatorError(
            "Could not import database.manager "
            f"({exc}). SQLCipher comes from the `sqlcipher3-binary` wheel; "
            "install it with `pip install sqlcipher3-binary` and make sure "
            "requirements.txt is installed."
        ) from exc

    import database.manager as manager_module

    return manager_module


def load_auth_service():
    """Import the auth service, which owns password hashing.

    Password hashing lives in one place on purpose: if this CLI grew its own
    hasher, a password set here would stop verifying the day the application's
    parameters changed.
    """
    try:
        from backend.services.auth_service import auth_service
    except Exception as exc:  # noqa: BLE001 - any import-time failure is fatal here
        raise OperatorError(
            "Could not import backend.services.auth_service "
            f"({type(exc).__name__}: {exc}). Password hashing lives there, so "
            "user creation cannot continue until that import works."
        ) from exc

    if not hasattr(auth_service, "hash_password"):
        raise OperatorError(
            "backend.services.auth_service.auth_service has no hash_password(). "
            "This CLI must not invent its own password hashing."
        )

    return auth_service


def require_master_key(keyring) -> bytes:
    from backend.security.keyring import KeyringError

    try:
        return keyring.load_master_key()
    except KeyringError as exc:
        raise OperatorError(str(exc)) from exc


# ----------------------------------------------------------------------
# generate-master-key
# ----------------------------------------------------------------------


def cmd_generate_master_key(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    master_key = keyring.generate_master_key()

    banner("NEW SERVER MASTER KEY")
    out()
    out(f"{keyring.MASTER_KEY_ENV}={master_key}")
    out()
    out("Where this goes")
    out("---------------")
    out("  1. Put the line above in the server's environment file, the one")
    out("     the systemd unit loads with EnvironmentFile (by default")
    out("     /etc/tzone/tzone.env), or in the project's .env for local work.")
    out("  2. Restrict the file: chown root:tzone and chmod 640.")
    out("  3. Restart the API so the new value is read:")
    out("       sudo systemctl restart tzone-api")
    out()
    out("!! READ THIS BEFORE YOU CLOSE THIS TERMINAL !!")
    out("---------------------------------------------")
    out("  This key unwraps the encryption key of EVERY company database.")
    out("  There is no recovery path, no backdoor and no support override.")
    out("  If you lose this key, every company database on this platform")
    out("  becomes permanently unreadable — backups included, because the")
    out("  backups are encrypted with the very keys this master key unwraps.")
    out()
    out("  Store a copy somewhere that is NOT this server and NOT the backup")
    out("  target: a password manager, a sealed envelope, an offline vault.")
    out()
    out("  Never generate a new key for a platform that already has data.")
    out("  A new master key cannot unwrap keys sealed under the old one.")
    out()

    return 0


# ----------------------------------------------------------------------
# create-company
# ----------------------------------------------------------------------


def _find_or_create_workspace(conn, *, name: str, owner_email: str) -> tuple[int, bool]:
    slug = slugify(name)

    row = conn.execute(
        "SELECT id FROM workspaces WHERE slug = ? LIMIT 1",
        (slug,),
    ).fetchone()

    if row:
        return int(row["id"]), False

    now = utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO workspaces (name, slug, status, owner_email, created_at, updated_at)
        VALUES (?, ?, 'active', ?, ?, ?)
        """,
        (name, slug, owner_email, now, now),
    )

    return int(cursor.lastrowid), True


def _seed_company_roles(conn, company_id: int) -> dict[str, int]:
    """Create this company's roles and wire their permissions.

    Returns a mapping of role code to role id so the caller can assign one.
    """
    from database.schema_control import DEFAULT_ROLES

    now = utc_now_iso()

    permission_ids = {
        row["code"]: int(row["id"])
        for row in conn.execute("SELECT id, code FROM permissions").fetchall()
    }

    role_ids: dict[str, int] = {}

    for name, code, description, permission_codes in DEFAULT_ROLES:
        conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(company_id, code) DO UPDATE SET
                name = excluded.name,
                description = excluded.description
            """,
            (company_id, name, code, description, now),
        )

        row = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = ? LIMIT 1",
            (company_id, code),
        ).fetchone()

        role_id = int(row["id"])
        role_ids[code] = role_id

        missing = [
            permission_code
            for permission_code in permission_codes
            if permission_code not in permission_ids
        ]

        if missing:
            raise OperatorError(
                "Role "
                f"{code!r} references permissions that are not registered: "
                f"{', '.join(missing)}. The control database seeds permissions "
                "from DEFAULT_PERMISSIONS; it looks out of date."
            )

        conn.executemany(
            """
            INSERT OR IGNORE INTO role_permissions (role_id, permission_id, created_at)
            VALUES (?, ?, ?)
            """,
            [
                (role_id, permission_ids[permission_code], now)
                for permission_code in permission_codes
            ],
        )

    return role_ids


def _create_or_reuse_user(
    conn,
    *,
    email: str,
    full_name: str,
    password_hash: str,
    is_super_admin: bool,
) -> tuple[int, bool]:
    normalized = email.strip().lower()

    if "@" not in normalized:
        raise OperatorError(f"{email!r} is not a valid email address.")

    row = conn.execute(
        "SELECT id FROM users WHERE email = ? LIMIT 1",
        (normalized,),
    ).fetchone()

    if row:
        return int(row["id"]), False

    now = utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO users (
            email, password_hash, full_name, status,
            is_super_admin, created_at, updated_at
        )
        VALUES (?, ?, ?, 'active', ?, ?, ?)
        """,
        (normalized, password_hash, full_name, 1 if is_super_admin else 0, now, now),
    )

    return int(cursor.lastrowid), True


def cmd_create_company(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    auth_service = load_auth_service()

    company_slug = slugify(args.slug)
    owner_email = args.owner_email.strip().lower()

    try:
        password_hash = auth_service.hash_password(args.owner_password)
    except ValueError as exc:
        raise OperatorError(f"Owner password rejected: {exc}") from exc

    workspace_code = keyring.generate_workspace_code()

    # Step 1: control-plane rows that must exist before the tenant file.
    with database_manager.control() as conn:
        workspace_id, workspace_created = _find_or_create_workspace(
            conn,
            name=args.workspace,
            owner_email=owner_email,
        )

        existing = conn.execute(
            "SELECT id FROM companies WHERE workspace_id = ? AND slug = ? LIMIT 1",
            (workspace_id, company_slug),
        ).fetchone()

        if existing:
            raise OperatorError(
                f"Workspace {args.workspace!r} already has a company with slug "
                f"{company_slug!r} (id {int(existing['id'])}). Pick another slug."
            )

        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO companies (
                workspace_id, name, slug, country, currency, timezone,
                default_language, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                workspace_id,
                args.name,
                company_slug,
                args.country,
                args.currency,
                args.timezone,
                args.language,
                now,
                now,
            ),
        )
        company_id = int(cursor.lastrowid)
        conn.commit()

    # Step 2: the encrypted database itself. If anything below fails the
    # half-created company row is removed, so a retry is not blocked by
    # leftovers from the failed attempt.
    try:
        database_path = database_manager.provision_company(
            company_id=company_id,
            workspace_code=workspace_code,
        )

        with database_manager.control() as conn:
            role_ids = _seed_company_roles(conn, company_id)

            owner_role_id = role_ids.get("owner")
            if owner_role_id is None:
                raise OperatorError(
                    "DEFAULT_ROLES does not define an 'owner' role, so the "
                    "first user cannot be given ownership."
                )

            user_id, user_created = _create_or_reuse_user(
                conn,
                email=owner_email,
                full_name=args.owner_name,
                password_hash=password_hash,
                is_super_admin=False,
            )

            conn.execute(
                """
                INSERT INTO company_users (
                    company_id, user_id, role_id, status, created_at
                )
                VALUES (?, ?, ?, 'active', ?)
                ON CONFLICT(company_id, user_id) DO UPDATE SET
                    role_id = excluded.role_id,
                    status = 'active'
                """,
                (company_id, user_id, owner_role_id, utc_now_iso()),
            )

            conn.execute(
                """
                INSERT INTO audit_log (
                    workspace_id, company_id, actor_user_id, action,
                    target_type, target_id, created_at
                )
                VALUES (?, ?, NULL, 'company.created', 'company', ?, ?)
                """,
                (workspace_id, company_id, str(company_id), utc_now_iso()),
            )

            conn.commit()
    except Exception:
        _rollback_company(database_manager, company_id)
        raise

    banner("COMPANY CREATED")
    out()
    out(f"  Workspace   : {args.workspace} (id {workspace_id})"
        + ("  [created]" if workspace_created else "  [existing]"))
    out(f"  Company     : {args.name} (id {company_id}, slug {company_slug})")
    out(f"  Database    : {database_path}")
    out(f"  Roles       : {', '.join(sorted(role_ids))}")
    out(f"  Owner       : {args.owner_name} <{owner_email}> (user id {user_id})"
        + ("" if user_created else "  [existing user, now owner here]"))
    out()
    out(RULE)
    out("  WORKSPACE CODE FOR THIS COMPANY")
    out()
    out(f"      {workspace_code}")
    out()
    out(RULE)
    out()
    out("  This code is printed once and is never stored in readable form.")
    out("  It seals a second copy of this company's database key, so the")
    out("  platform genuinely cannot show it to you again — not from the")
    out("  database, not from a backup, not from support.")
    out()
    out("  Give it to the company owner now, through a channel you trust.")
    out("  If it is lost, issue a new one:")
    out()
    out(f"      python -m tools.manage_platform rotate-workspace-code "
        f"--company-id {company_id}")
    out()

    return 0


def _rollback_company(database_manager, company_id: int) -> None:
    """Undo a half-finished create-company so the operator can retry."""
    try:
        path = database_manager.tenant_path(company_id)

        with database_manager.control() as conn:
            conn.execute(
                "DELETE FROM company_databases WHERE company_id = ?",
                (company_id,),
            )
            conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
            conn.commit()

        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - cleanup must never mask the real error
        err(
            f"  (cleanup of company {company_id} did not fully succeed: {exc}. "
            "Check `list-companies` before retrying.)"
        )


# ----------------------------------------------------------------------
# list-companies
# ----------------------------------------------------------------------


def cmd_list_companies(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    with database_manager.control() as conn:
        rows = conn.execute(
            """
            SELECT
                companies.id AS id,
                companies.name AS name,
                companies.slug AS slug,
                companies.status AS status,
                workspaces.name AS workspace
            FROM companies
            LEFT JOIN workspaces ON workspaces.id = companies.workspace_id
            ORDER BY companies.id
            """
        ).fetchall()

    if not rows:
        out("No companies yet. Create one with:")
        out("  python -m tools.manage_platform create-company --help")
        return 0

    table_rows: list[list[str]] = []

    for row in rows:
        path = database_manager.tenant_path(int(row["id"]))
        exists = path.exists()

        table_rows.append(
            [
                str(row["id"]),
                str(row["name"]),
                str(row["slug"]),
                str(row["workspace"] or "-"),
                str(row["status"]),
                "yes" if exists else "MISSING",
                human_size(path.stat().st_size) if exists else "-",
            ]
        )

    print_table(
        ["ID", "NAME", "SLUG", "WORKSPACE", "STATUS", "DB FILE", "SIZE"],
        table_rows,
    )

    missing = [row[0] for row in table_rows if row[5] == "MISSING"]

    if missing:
        out()
        out(
            "Warning: no database file for company id(s) "
            f"{', '.join(missing)}. That company cannot serve requests. "
            "Restore it from a backup."
        )

    return 0


# ----------------------------------------------------------------------
# rotate-workspace-code
# ----------------------------------------------------------------------


def cmd_rotate_workspace_code(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    company_id = int(args.company_id)

    with database_manager.control() as conn:
        row = conn.execute(
            "SELECT name FROM companies WHERE id = ? LIMIT 1",
            (company_id,),
        ).fetchone()

    if not row:
        raise OperatorError(
            f"No company with id {company_id}. Run `list-companies` to see them."
        )

    new_code = keyring.generate_workspace_code()

    try:
        database_manager.rotate_workspace_code(company_id, new_code)
    except manager.UnknownCompany as exc:
        raise OperatorError(str(exc)) from exc

    banner("WORKSPACE CODE ROTATED")
    out()
    out(f"  Company : {row['name']} (id {company_id})")
    out()
    out(RULE)
    out("  NEW WORKSPACE CODE")
    out()
    out(f"      {new_code}")
    out()
    out(RULE)
    out()
    out("  The previous code stopped working the moment this command ran.")
    out("  Everyone at this company must be given the new one.")
    out("  The company's data was not re-encrypted and no downtime is needed.")
    out("  This code is shown once and cannot be recovered later.")
    out()

    return 0


# ----------------------------------------------------------------------
# create-super-admin
# ----------------------------------------------------------------------


def cmd_create_super_admin(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    auth_service = load_auth_service()

    try:
        password_hash = auth_service.hash_password(args.password)
    except ValueError as exc:
        raise OperatorError(f"Password rejected: {exc}") from exc

    email = args.email.strip().lower()

    with database_manager.control() as conn:
        user_id, created = _create_or_reuse_user(
            conn,
            email=email,
            full_name=args.name,
            password_hash=password_hash,
            is_super_admin=True,
        )

        if not created:
            conn.execute(
                """
                UPDATE users
                SET is_super_admin = 1,
                    password_hash = ?,
                    full_name = ?,
                    status = 'active',
                    updated_at = ?
                WHERE id = ?
                """,
                (password_hash, args.name, utc_now_iso(), user_id),
            )

        conn.execute(
            """
            INSERT INTO audit_log (
                workspace_id, company_id, actor_user_id, action,
                target_type, target_id, created_at
            )
            VALUES (NULL, NULL, NULL, 'super_admin.created', 'user', ?, ?)
            """,
            (str(user_id), utc_now_iso()),
        )

        conn.commit()

    banner("SUPER ADMIN " + ("CREATED" if created else "UPDATED"))
    out()
    out(f"  User id : {user_id}")
    out(f"  Email   : {email}")
    out(f"  Name    : {args.name}")
    out()

    if not created:
        out("  This email already existed. It was promoted to super admin and")
        out("  its password was reset to the one you just supplied.")
        out()

    out("  A super admin can reach every company on this platform.")
    out("  Create as few of these as the work actually requires.")
    out()

    return 0


# ----------------------------------------------------------------------
# import-knowledge
# ----------------------------------------------------------------------


LEGACY_KNOWLEDGE_FILES = (
    Path("config") / "knowledge_base.json",
    Path("config") / "training_knowledge.json",
)

IMPORT_CATEGORY_NAME = "Imported knowledge"

# Kept in step with backend/api/schemas/knowledge.py, so an imported row can be
# edited through the API afterwards instead of failing its validators.
MAX_TITLE = 200
MAX_KEYWORDS = 1000
MAX_CONTENT = 8000


def load_knowledge_service():
    try:
        from backend.services.knowledge_service import knowledge_service
    except Exception as exc:  # noqa: BLE001 - any import-time failure is fatal here
        raise OperatorError(
            "Could not import backend.services.knowledge_service "
            f"({type(exc).__name__}: {exc}). The import writes through that "
            "service so the rules the API enforces also apply here."
        ) from exc

    return knowledge_service


def _clean_text(value, limit: int) -> str | None:
    text = str(value or "").strip()

    if not text:
        return None

    return text[:limit]


def _read_legacy_file(path: Path) -> list[dict]:
    """Read one legacy knowledge file, refusing anything not shaped like one."""
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OperatorError(f"Cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OperatorError(f"{path} is not valid JSON: {exc}") from exc

    items = raw.get("items") if isinstance(raw, dict) else raw

    if not isinstance(items, list):
        raise OperatorError(
            f"{path} does not contain an 'items' list. A legacy knowledge file "
            'looks like {"items": [ ... ]}.'
        )

    return [item for item in items if isinstance(item, dict)]


def _legacy_to_item(raw: dict) -> tuple[str, dict] | None:
    """Map one legacy entry onto the knowledge_items columns.

    The legacy entry carries a question and an answer per language plus a line
    of usage instructions; the table has one title, one body per language and
    one free-text hint field. The question becomes the title, the answers become
    the content, and the Arabic phrasing and the instructions are kept together
    as the item's hints, so nothing in the file is dropped on the way in.
    """
    external_id = _clean_text(raw.get("id"), 120)

    question_en = _clean_text(raw.get("question_en"), MAX_TITLE)
    question_ar = _clean_text(raw.get("question_ar"), MAX_TITLE)
    title = question_en or question_ar or external_id

    content_ar = _clean_text(raw.get("answer_ar"), MAX_CONTENT)
    content_en = _clean_text(raw.get("answer_en"), MAX_CONTENT)

    if not external_id or not title or not (content_ar or content_en):
        return None

    hints = [
        hint
        for hint in (
            question_ar if question_ar and question_ar != title else None,
            _clean_text(raw.get("instructions"), MAX_KEYWORDS),
        )
        if hint
    ]

    return external_id, {
        "title": title,
        "content_ar": content_ar,
        "content_en": content_en,
        "department": _clean_text(raw.get("department"), 60),
        "keywords": _clean_text(" | ".join(hints), MAX_KEYWORDS),
        "status": "active",
    }


def cmd_import_knowledge(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    knowledge_service = load_knowledge_service()

    company_id = int(args.company_id)

    with database_manager.control() as conn:
        row = conn.execute(
            "SELECT name FROM companies WHERE id = ? LIMIT 1",
            (company_id,),
        ).fetchone()

    if not row:
        raise OperatorError(
            f"No company with id {company_id}. Run `list-companies` to see them."
        )

    if not database_manager.tenant_path(company_id).exists():
        raise OperatorError(
            f"Company {company_id} has no database file at "
            f"{database_manager.tenant_path(company_id)}. Restore it from a "
            "backup before importing anything into it."
        )

    paths = [Path(name) for name in (args.file or [])] or list(LEGACY_KNOWLEDGE_FILES)

    missing = [path for path in paths if not path.exists()]
    present = [path for path in paths if path.exists()]

    if not present:
        raise OperatorError(
            "None of these files exist: "
            f"{', '.join(str(path) for path in paths)}. Run this from the "
            "project root, or name the files with --file."
        )

    # Later files win, which matches how the assistant used to read them: the
    # training file was loaded after the base file and refined the same ids.
    collected: dict[str, dict] = {}
    unusable = 0
    read_counts: list[tuple[Path, int]] = []

    for path in present:
        entries = _read_legacy_file(path)
        read_counts.append((path, len(entries)))

        for entry in entries:
            mapped = _legacy_to_item(entry)

            if mapped is None:
                unusable += 1
                continue

            external_id, values = mapped
            collected[external_id] = values

    if not collected:
        raise OperatorError(
            "No usable entries were found. Every entry needs an 'id', a "
            "question and at least one answer."
        )

    try:
        category = knowledge_service.ensure_category(
            company_id=company_id,
            name=IMPORT_CATEGORY_NAME,
        )
    except manager.UnknownCompany as exc:
        raise OperatorError(str(exc)) from exc
    except ValueError as exc:
        raise OperatorError(f"Could not prepare the import category: {exc}") from exc

    created = 0
    updated = 0
    failed: list[str] = []

    for external_id, values in collected.items():
        try:
            _, was_created = knowledge_service.upsert_by_external_id(
                company_id=company_id,
                external_id=external_id,
                data={**values, "category_id": int(category["id"])},
            )
        except ValueError as exc:
            failed.append(f"{external_id}: {exc}")
            continue

        if was_created:
            created += 1
        else:
            updated += 1

    banner("KNOWLEDGE IMPORTED")
    out()
    out(f"  Company   : {row['name']} (id {company_id})")
    out(f"  Category  : {category['name']} (id {int(category['id'])})")
    out()

    for path, count in read_counts:
        out(f"  Read      : {path} ({count} entries)")

    for path in missing:
        out(f"  Skipped   : {path} (not found)")

    out()
    out(f"  Created   : {created}")
    out(f"  Updated   : {updated}")

    if unusable:
        out(f"  Unusable  : {unusable} (no id, no question, or no answer)")

    if failed:
        out(f"  Rejected  : {len(failed)}")
        for line in failed:
            out(f"      {line}")

    out()
    out("  These items belong to this company alone and are stored inside its")
    out("  encrypted database. The assistant reads them on the next message;")
    out("  no restart is needed. Review them at /knowledge before relying on")
    out("  them — the legacy files were written for one specific business.")
    out()

    return 1 if failed else 0


# ----------------------------------------------------------------------
# import-departments
# ----------------------------------------------------------------------


LEGACY_DEPARTMENTS_FILE = Path("config") / "business_modules.json"

MAX_DEPARTMENT_CODE = 60
MAX_DEPARTMENT_NAME = 120
MAX_DEPARTMENT_BUTTON = 60


def load_department_service():
    try:
        from backend.services.business_department_service import (
            business_department_service,
        )
    except Exception as exc:  # noqa: BLE001 - any import-time failure is fatal here
        raise OperatorError(
            "Could not import backend.services.business_department_service "
            f"({type(exc).__name__}: {exc}). The import writes through that "
            "service so the rules the API enforces also apply here."
        ) from exc

    return business_department_service


def _read_departments_file(path: Path) -> list[dict]:
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OperatorError(f"Cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OperatorError(f"{path} is not valid JSON: {exc}") from exc

    modules = raw.get("modules") if isinstance(raw, dict) else raw

    if not isinstance(modules, list):
        raise OperatorError(
            f"{path} does not contain a 'modules' list. A business modules "
            'file looks like {"modules": [ ... ]}.'
        )

    return [module for module in modules if isinstance(module, dict)]


def _legacy_to_department(raw: dict, position: int) -> tuple[str, dict] | None:
    """Map one legacy module onto the business_departments columns."""
    code = _clean_text(raw.get("id") or raw.get("code"), MAX_DEPARTMENT_CODE)

    name_ar = _clean_text(raw.get("name_ar"), MAX_DEPARTMENT_NAME)
    name_en = _clean_text(raw.get("name_en"), MAX_DEPARTMENT_NAME)

    if not code or not (name_ar or name_en):
        return None

    return code, {
        "name_ar": name_ar,
        "name_en": name_en,
        "button_ar": _clean_text(raw.get("button_ar"), MAX_DEPARTMENT_BUTTON),
        "button_en": _clean_text(raw.get("button_en"), MAX_DEPARTMENT_BUTTON),
        "enabled": bool(raw.get("enabled", True)),
        "sort_order": position,
    }


def cmd_import_departments(args: argparse.Namespace) -> int:
    """Load the legacy shared menu into one named company.

    Deliberately one company at a time, and never at provisioning: seeding this
    file into every company is precisely the defect being repaired — it is one
    business's menu, and every other business on the platform was serving it to
    its own customers.
    """
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    department_service = load_department_service()

    company_id = int(args.company_id)

    with database_manager.control() as conn:
        row = conn.execute(
            "SELECT name FROM companies WHERE id = ? LIMIT 1",
            (company_id,),
        ).fetchone()

    if not row:
        raise OperatorError(
            f"No company with id {company_id}. Run `list-companies` to see them."
        )

    if not database_manager.tenant_path(company_id).exists():
        raise OperatorError(
            f"Company {company_id} has no database file at "
            f"{database_manager.tenant_path(company_id)}. Restore it from a "
            "backup before importing anything into it."
        )

    path = Path(args.file) if args.file else LEGACY_DEPARTMENTS_FILE

    if not path.exists():
        raise OperatorError(
            f"{path} does not exist. Run this from the project root, or name "
            "the file with --file."
        )

    entries = _read_departments_file(path)

    collected: dict[str, dict] = {}
    unusable = 0

    for position, entry in enumerate(entries):
        mapped = _legacy_to_department(entry, position)

        if mapped is None:
            unusable += 1
            continue

        code, values = mapped
        collected[code] = values

    if not collected:
        raise OperatorError(
            "No usable departments were found. Every entry needs an 'id' and "
            "an Arabic or English name."
        )

    created = 0
    updated = 0
    failed: list[str] = []

    for code, values in collected.items():
        try:
            _, was_created = department_service.upsert_by_code(
                company_id=company_id,
                code=code,
                data=values,
            )
        except manager.UnknownCompany as exc:
            raise OperatorError(str(exc)) from exc
        except ValueError as exc:
            failed.append(f"{code}: {exc}")
            continue

        if was_created:
            created += 1
        else:
            updated += 1

    banner("DEPARTMENTS IMPORTED")
    out()
    out(f"  Company   : {row['name']} (id {company_id})")
    out(f"  Read      : {path} ({len(entries)} entries)")
    out()
    out(f"  Created   : {created}")
    out(f"  Updated   : {updated}")

    if unusable:
        out(f"  Unusable  : {unusable} (no id, or no name in either language)")

    if failed:
        out(f"  Rejected  : {len(failed)}")
        for line in failed:
            out(f"      {line}")

    out()
    out("  These sections belong to this company alone and are stored inside")
    out("  its encrypted database. Every other company keeps the menu it")
    out("  defines for itself, and a company that defines none is shown no")
    out("  menu at all rather than this one. Safe to re-run: entries are")
    out("  matched on their code and refreshed rather than duplicated.")
    out()

    return 1 if failed else 0


# ----------------------------------------------------------------------
# backup
# ----------------------------------------------------------------------


def _copy_database(source: Path, destination_dir: Path) -> int:
    """Copy a database file plus its WAL sidecars. Returns bytes copied."""
    copied = 0

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(source) + suffix)

        if not candidate.exists():
            continue

        shutil.copy2(candidate, destination_dir / candidate.name)
        copied += candidate.stat().st_size

    return copied


def cmd_backup(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    from config.settings import config

    data_dir = Path(config.DATA_DIR)
    control_path = data_dir / manager.CONTROL_FILENAME

    if not control_path.exists():
        raise OperatorError(
            f"No control database at {control_path}. There is nothing to back up."
        )

    output_root = Path(args.output).expanduser()

    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OperatorError(f"Cannot create {output_root}: {exc}") from exc

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = output_root / f"tzone-backup-{stamp}"

    try:
        destination.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise OperatorError(f"Cannot create {destination}: {exc}") from exc

    # Checkpointing folds the write-ahead log back into the main file, so a
    # copy taken right after is a complete database rather than one missing
    # the most recent commits.
    try:
        with database_manager.control() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:  # noqa: BLE001
        raise OperatorError(
            f"Could not open the control database to checkpoint it: {exc}"
        ) from exc

    total_bytes = _copy_database(control_path, destination)
    copied_files = [manager.CONTROL_FILENAME]

    with database_manager.control() as conn:
        company_rows = conn.execute(
            """
            SELECT companies.id AS id, companies.name AS name
            FROM companies
            JOIN company_databases ON company_databases.company_id = companies.id
            ORDER BY companies.id
            """
        ).fetchall()

    skipped: list[str] = []

    for row in company_rows:
        company_id = int(row["id"])
        path = database_manager.tenant_path(company_id)

        if not path.exists():
            skipped.append(f"{company_id} ({row['name']})")
            continue

        try:
            with database_manager.tenant(company_id) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{company_id} ({row['name']}): {exc}")
            continue

        total_bytes += _copy_database(path, destination)
        copied_files.append(path.name)

    banner("BACKUP COMPLETE")
    out()
    out(f"  Destination : {destination}")
    out(f"  Files       : {len(copied_files)}")
    out(f"  Size        : {human_size(total_bytes)}")
    out()

    for name in copied_files:
        out(f"    {name}")

    out()

    if skipped:
        out("  NOT backed up:")
        for item in skipped:
            out(f"    company {item}")
        out()

    out(RULE)
    out("  ABOUT THIS BACKUP")
    out(RULE)
    out()
    out("  These files are encrypted exactly as they were on disk, so copying")
    out("  them is safe: anyone who steals the backup gets ciphertext.")
    out()
    out("  The other half of that sentence matters just as much:")
    out()
    out("    WITHOUT THE MASTER KEY THIS BACKUP IS WORTHLESS.")
    out()
    out("  Restoring means copying these files back AND supplying the same")
    out("  TZONE_MASTER_KEY they were created under. A backup with no key is")
    out("  not a backup, it is a folder of noise.")
    out()
    out("  Store the master key somewhere SEPARATE from these files. Putting")
    out("  the key next to the backup gives an attacker both halves at once,")
    out("  and losing that one location loses everything at once.")
    out()

    return 0


# ----------------------------------------------------------------------
# check
# ----------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    """Preflight. Run this after every deploy; exits non-zero on any failure."""
    failures: list[str] = []
    checks: list[tuple[str, bool, str]] = []

    def record(label: str, ok: bool, detail: str) -> None:
        checks.append((label, ok, detail))
        if not ok:
            failures.append(label)

    banner("PLATFORM PREFLIGHT CHECK")
    out()

    # 1. Master key -----------------------------------------------------
    keyring = None
    try:
        keyring = load_keyring()
        keyring.load_master_key()
        record("master key", True, f"{keyring.MASTER_KEY_ENV} present and valid")
    except Exception as exc:  # noqa: BLE001
        record("master key", False, str(exc))

    # 2. Data directory -------------------------------------------------
    data_dir: Path | None = None
    try:
        from config.settings import config

        data_dir = Path(config.DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)

        probe = data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

        record("data directory", True, f"{data_dir} is writable")
    except Exception as exc:  # noqa: BLE001
        record("data directory", False, f"{data_dir or 'DATA_DIR'}: {exc}")

    # 3. Control database ------------------------------------------------
    manager = None
    company_rows: list = []

    if not failures:
        try:
            manager = load_manager()

            with manager.database_manager.control() as conn:
                company_rows = conn.execute(
                    """
                    SELECT companies.id AS id, companies.name AS name
                    FROM companies
                    JOIN company_databases
                        ON company_databases.company_id = companies.id
                    ORDER BY companies.id
                    """
                ).fetchall()

            record(
                "control database",
                True,
                f"opens and decrypts, {len(company_rows)} company database(s) registered",
            )
        except Exception as exc:  # noqa: BLE001
            record(
                "control database",
                False,
                f"{exc} — this is what a WRONG TZONE_MASTER_KEY looks like: "
                "the file cannot be decrypted, so it does not look like a "
                "database at all.",
            )
    else:
        record("control database", False, "skipped, an earlier check failed")

    # 4. Every registered company database -------------------------------
    if manager is not None and "control database" not in failures:
        if not company_rows:
            record("company databases", True, "none registered yet")
        else:
            broken: list[str] = []

            for row in company_rows:
                company_id = int(row["id"])
                try:
                    with manager.database_manager.tenant(company_id) as conn:
                        conn.execute("SELECT count(*) FROM conversations").fetchone()
                except Exception as exc:  # noqa: BLE001
                    broken.append(f"company {company_id} ({row['name']}): {exc}")

            if broken:
                record("company databases", False, "; ".join(broken))
            else:
                record(
                    "company databases",
                    True,
                    f"all {len(company_rows)} open and decrypt",
                )
    elif "company databases" not in {label for label, _, _ in checks}:
        record("company databases", False, "skipped, the control database failed")

    for label, ok, detail in checks:
        out(f"  [{'PASS' if ok else 'FAIL'}]  {label:<20} {detail}")

    out()

    if failures:
        out(RULE)
        out(f"  PREFLIGHT FAILED: {', '.join(failures)}")
        out(RULE)
        out()
        out("  Do not put this deployment in front of customers until every")
        out("  line above says PASS. The most common causes are a missing or")
        out("  wrong TZONE_MASTER_KEY, and a data directory the service user")
        out("  cannot write to.")
        out()
        return 1

    out(RULE)
    out("  PREFLIGHT PASSED")
    out(RULE)
    out()

    return 0


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------



def _find_user(database_manager, email: str) -> dict:
    """Look a user up by address, or fail with advice."""
    normalized = email.strip().lower()

    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, email, full_name, status, is_super_admin, locked_until
            FROM users
            WHERE LOWER(email) = ?
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()

    if not row:
        raise OperatorError(
            f"No account with the address {normalized}. "
            "Check the spelling, or run `list-companies` to see who exists."
        )

    return dict(row)


def cmd_unlock_user(args: argparse.Namespace) -> int:
    """Clear a lockout from the server.

    The way back in when nobody inside the product can help: a platform
    administrator has nobody above them, and a company whose only owner is
    locked out has nobody with `users.manage` left to act.
    """
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager
    auth_service = load_auth_service()

    user = _find_user(database_manager, args.email)

    if not user["locked_until"]:
        out()
        out(f"  {user['email']} is not locked. Nothing to do.")
        out()
        return 0

    auth_service.unlock_account(user_id=int(user["id"]))

    banner("ACCOUNT UNLOCKED")
    out()
    out(f"  Account : {user['email']} (id {user['id']})")
    out()
    out("  The lock and the failed attempts behind it were both cleared.")
    out("  The password was not changed.")
    out()

    return 0



def cmd_mint_activation_code(args: argparse.Namespace) -> int:
    """Mint one activation code from the server.

    The console mints these too, but a platform administrator has nobody above
    them: on a fresh install, before the first super admin can even sign in,
    this is the only way to produce the code that turns the first
    demonstration into a real workspace. The plaintext prints once here and is
    stored only as a hash -- copy it now.
    """
    keyring = load_keyring()
    require_master_key(keyring)

    load_manager()

    from backend.services.activation_service import activation_service

    minted = activation_service.mint(
        plan_id=args.plan_id,
        note=(args.note or None),
        created_by_user_id=None,
        expires_at=(args.expires_at or None),
    )

    banner("ACTIVATION CODE MINTED")
    out()
    out(f"  Code    : {minted['code']}")
    out(f"  Plan    : {minted['plan_id'] if minted['plan_id'] else '(none — plan chosen at redemption)'}")
    out(f"  Expires : {minted['expires_at'] if minted['expires_at'] else '(never)'}")
    out()
    out("  This is the only time the code is shown. Only its hash was stored,")
    out("  so if you lose it you must mint another.")
    out()

    return 0


def cmd_add_branch(args: argparse.Namespace) -> int:
    """Create a branch for one company from the server.

    The API is the ordinary way; this exists for the same reason the other
    commands here do. A company being set up before anybody has a password, or
    an operator moving a customer's locations across in bulk, has no session to
    call the API with.
    """
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    with database_manager.control() as conn:
        company = conn.execute(
            "SELECT id, name FROM companies WHERE id = ? LIMIT 1",
            (int(args.company_id),),
        ).fetchone()

        if not company:
            raise OperatorError(f"No company with id {args.company_id}.")

        clash = conn.execute(
            "SELECT id FROM branches WHERE company_id = ? AND LOWER(name) = ?",
            (int(args.company_id), args.name.strip().lower()),
        ).fetchone()

        if clash:
            raise OperatorError(
                f"{company['name']} already has a branch named {args.name!r}."
            )

        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO branches (
                company_id, name, code, address, phone, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                int(args.company_id),
                args.name.strip(),
                (args.code or "").strip() or None,
                (args.address or "").strip() or None,
                (args.phone or "").strip() or None,
                now,
                now,
            ),
        )
        conn.commit()

    banner("BRANCH ADDED")
    out()
    out(f"  Company : {company['name']} (id {company['id']})")
    out(f"  Branch  : {args.name.strip()} (id {cursor.lastrowid})")
    out()
    out("  It can now be chosen on the Roles screen and when connecting a")
    out("  channel.")
    out()

    return 0


def cmd_list_branches(args: argparse.Namespace) -> int:
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    with database_manager.control() as conn:
        rows = conn.execute(
            """
            SELECT branches.id, branches.name, branches.code, branches.status,
                   companies.name AS company_name
            FROM branches
            JOIN companies ON companies.id = branches.company_id
            WHERE (? IS NULL OR branches.company_id = ?)
            ORDER BY companies.name, branches.name
            """,
            (args.company_id, args.company_id),
        ).fetchall()

    banner("BRANCHES")
    out()

    if not rows:
        out("  None. Nothing on this platform has a branch yet.")
        out()
        return 0

    for row in rows:
        code = f" [{row['code']}]" if row["code"] else ""
        out(f"  {row['company_name']}: {row['name']}{code} (id {row['id']}, {row['status']})")

    out()

    return 0


def cmd_reset_totp(args: argparse.Namespace) -> int:
    """Clear a second factor from the server, so the account can enrol again.

    The emergency exit for the one account that has no other. A platform
    administrator who has lost both their authenticator device and their
    recovery codes cannot be helped from inside the product — there is nobody
    above them to reset it, and the console refuses every route until they are
    enrolled. Without this the platform would be permanently unadministrable
    after a lost phone.

    It is a command on the server rather than an endpoint for exactly that
    reason: whoever can run it already has the master key and the database, so
    it grants nothing they did not already hold. An endpoint would be a way to
    strip anybody's second factor over the network.

    The account is left **unenrolled**, not exempt. The next sign-in mints a
    session that can reach nothing but enrolment, so the requirement stands.
    """
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager

    from backend.services.totp_service import totp_service

    user = _find_user(database_manager, args.email)

    totp_service.disable(int(user["id"]), force=True)

    banner("TWO-FACTOR AUTHENTICATION CLEARED")
    out()
    out(f"  Account : {user['email']} (id {user['id']})")
    out()
    out("  The secret and every recovery code were discarded.")
    out("  The password was not changed.")

    if bool(user["is_super_admin"]):
        out()
        out("  This is a platform administrator, so enrolment is still")
        out("  required: their next sign-in reaches the enrolment screen and")
        out("  nothing else until they finish it.")

    out()

    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    """Set a password from the server, without granting anything.

    Deliberately separate from `create-super-admin`, which was the only
    per-user lever there was and which sets `is_super_admin = 1` as a side
    effect. Using that to help a locked-out employee handed them the platform.
    """
    keyring = load_keyring()
    require_master_key(keyring)

    manager = load_manager()
    database_manager = manager.database_manager
    auth_service = load_auth_service()

    user = _find_user(database_manager, args.email)

    try:
        auth_service.set_password(
            user_id=int(user["id"]),
            new_password=args.password,
            must_change=not args.no_forced_change,
        )
    except ValueError as exc:
        raise OperatorError(f"Password rejected: {exc}") from exc

    banner("PASSWORD RESET")
    out()
    out(f"  Account : {user['email']} (id {user['id']})")
    out(f"  Platform administrator : {'yes' if user['is_super_admin'] else 'no'}")
    out()
    out(RULE)
    out("  What this did")
    out()
    out("      - set the password to the one you supplied")
    out("      - cleared any lockout and the failed attempts behind it")
    out("      - ended every existing session for this account")

    if not args.no_forced_change:
        out("      - required a change at next sign-in")

    out()
    out("  It did NOT change what this account is allowed to do.")
    out()
    out(RULE)
    out()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.manage_platform",
        description=(
            "Operator CLI for the T-ZONE platform: master key, companies, "
            "workspace codes, super admins, backups and preflight checks."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Every command except generate-master-key needs TZONE_MASTER_KEY\n"
            "in the environment.\n\n"
            "Typical first deploy:\n"
            "  python -m tools.manage_platform generate-master-key\n"
            "  python -m tools.manage_platform check\n"
            "  python -m tools.manage_platform create-company --name ... \n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    generate = subparsers.add_parser(
        "generate-master-key",
        help="Print a new server master key and where to put it.",
    )
    generate.set_defaults(handler=cmd_generate_master_key)

    create_company = subparsers.add_parser(
        "create-company",
        help="Create a company, its encrypted database and its owner user.",
    )
    create_company.add_argument("--name", required=True, help="Company display name.")
    create_company.add_argument("--slug", required=True, help="URL-safe company slug.")
    create_company.add_argument(
        "--workspace",
        required=True,
        help="Workspace name. Created if it does not exist yet.",
    )
    create_company.add_argument("--owner-email", required=True, help="Owner's email.")
    create_company.add_argument("--owner-name", required=True, help="Owner's full name.")
    create_company.add_argument(
        "--owner-password",
        required=True,
        help="Owner's initial password, at least 8 characters.",
    )
    create_company.add_argument("--country", default=None, help="ISO country code.")
    create_company.add_argument("--currency", default="USD", help="Default currency.")
    create_company.add_argument(
        "--timezone",
        default="Asia/Beirut",
        dest="timezone",
        help="IANA timezone for this company.",
    )
    create_company.add_argument(
        "--language",
        default="ar",
        help="Default language code for this company.",
    )
    create_company.set_defaults(handler=cmd_create_company)

    list_companies = subparsers.add_parser(
        "list-companies",
        help="Show every company and the state of its database file.",
    )
    list_companies.set_defaults(handler=cmd_list_companies)

    rotate = subparsers.add_parser(
        "rotate-workspace-code",
        help="Issue a new workspace code for one company.",
    )
    rotate.add_argument("--company-id", required=True, type=int, help="Company id.")
    rotate.set_defaults(handler=cmd_rotate_workspace_code)

    super_admin = subparsers.add_parser(
        "create-super-admin",
        help="Create a platform super admin.",
    )
    super_admin.add_argument("--email", required=True, help="Super admin email.")
    super_admin.add_argument("--name", required=True, help="Super admin full name.")
    super_admin.add_argument(
        "--password",
        required=True,
        help="Password, at least 8 characters.",
    )
    super_admin.set_defaults(handler=cmd_create_super_admin)

    import_knowledge = subparsers.add_parser(
        "import-knowledge",
        help="Import the legacy config/*.json knowledge into one company.",
        description=(
            "Load config/knowledge_base.json and config/training_knowledge.json "
            "into one company's own encrypted database. Safe to re-run: entries "
            "are matched on their legacy id and refreshed rather than "
            "duplicated."
        ),
    )
    import_knowledge.add_argument(
        "--company-id",
        required=True,
        type=int,
        help="The company that receives this knowledge.",
    )
    import_knowledge.add_argument(
        "--file",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Import this file instead of the two default ones. "
            "Repeat for several files; later files win on a shared id."
        ),
    )
    import_knowledge.set_defaults(handler=cmd_import_knowledge)

    import_departments = subparsers.add_parser(
        "import-departments",
        help="Import config/business_modules.json into one company.",
        description=(
            "Load config/business_modules.json — the founding company's real "
            "sections — into that one company's own encrypted database. It is "
            "never seeded into every company: it names one business's "
            "departments, and serving it to everybody is the leak this "
            "replaces. Safe to re-run: entries are matched on their code and "
            "refreshed rather than duplicated."
        ),
    )
    import_departments.add_argument(
        "--company-id",
        required=True,
        type=int,
        help="The company that receives these departments.",
    )
    import_departments.add_argument(
        "--file",
        default=None,
        metavar="PATH",
        help="Import this file instead of config/business_modules.json.",
    )
    import_departments.set_defaults(handler=cmd_import_departments)

    backup = subparsers.add_parser(
        "backup",
        help="Copy the control database and every company database.",
    )
    backup.add_argument(
        "--output",
        required=True,
        help="Directory to write the timestamped backup folder into.",
    )
    backup.set_defaults(handler=cmd_backup)

    mint_code = subparsers.add_parser(
        "mint-activation-code",
        help="Mint one activation code. The plaintext prints once.",
    )
    mint_code.add_argument(
        "--plan-id",
        type=int,
        default=None,
        dest="plan_id",
        help="Plan this code grants. Omit to leave the plan chosen at redemption.",
    )
    mint_code.add_argument("--note", default=None, help="A note stored beside the code.")
    mint_code.add_argument(
        "--expires-at",
        default=None,
        dest="expires_at",
        help="ISO timestamp after which the code is dead. Omit for no expiry.",
    )
    mint_code.set_defaults(handler=cmd_mint_activation_code)

    add_branch = subparsers.add_parser(
        "add-branch",
        help="Create a branch for one company.",
    )
    add_branch.add_argument("--company-id", required=True, type=int)
    add_branch.add_argument("--name", required=True, help="What the branch is called.")
    add_branch.add_argument("--code", help="Short code, optional.")
    add_branch.add_argument("--address", help="Street address, optional.")
    add_branch.add_argument("--phone", help="Contact number, optional.")
    add_branch.set_defaults(handler=cmd_add_branch)

    list_branches = subparsers.add_parser(
        "list-branches",
        help="Show the branches on this platform.",
    )
    list_branches.add_argument(
        "--company-id", type=int, default=None, help="Narrow to one company."
    )
    list_branches.set_defaults(handler=cmd_list_branches)

    unlock_user = subparsers.add_parser(
        "unlock-user",
        help="Clear a lockout on one account. Does not change the password.",
    )
    unlock_user.add_argument("--email", required=True, help="Account address.")
    unlock_user.set_defaults(handler=cmd_unlock_user)

    reset_password = subparsers.add_parser(
        "reset-password",
        help="Set a password on one account, granting nothing.",
    )
    reset_password.add_argument("--email", required=True, help="Account address.")
    reset_password.add_argument("--password", required=True, help="The new password.")
    reset_password.add_argument(
        "--no-forced-change",
        action="store_true",
        help="Do not require the account to change it at next sign-in.",
    )
    reset_password.set_defaults(handler=cmd_reset_password)

    reset_totp = subparsers.add_parser(
        "reset-totp",
        help=(
            "Clear two-factor authentication on one account so it can enrol "
            "again. The way back in after a lost device."
        ),
    )
    reset_totp.add_argument("--email", required=True, help="Account address.")
    reset_totp.set_defaults(handler=cmd_reset_totp)

    check = subparsers.add_parser(
        "check",
        help="Preflight the deployment. Exits non-zero on any failure.",
    )
    check.set_defaults(handler=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "handler", None):
        parser.print_help()
        return 1

    try:
        return int(args.handler(args))
    except OperatorError as exc:
        err("")
        err(f"ERROR: {exc}")
        err("")
        return 1
    except KeyboardInterrupt:
        err("")
        err("Cancelled.")
        return 130
    except BrokenPipeError:
        # Output was piped into something that stopped reading, such as `head`.
        # That is not an error worth reporting to the operator.
        try:
            sys.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        return 0
    except Exception as exc:  # noqa: BLE001 - operators get a message, not a traceback
        err("")
        err(f"ERROR: {type(exc).__name__}: {exc}")
        err("")
        err(
            "If this is not obviously a configuration problem, re-run with "
            "TZONE_CLI_TRACEBACK=1 to see the full traceback."
        )
        err("")

        if os.getenv("TZONE_CLI_TRACEBACK", "").strip():
            raise

        return 1


if __name__ == "__main__":
    sys.exit(main())
