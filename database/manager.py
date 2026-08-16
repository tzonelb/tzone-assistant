"""Routes every database call to the right encrypted file.

There are two kinds of connection and no third:

``control()``
    The shared database. Users, companies, roles, sessions and the webhook
    routing table. Encrypted with a key derived from the server master key.

``tenant(company_id)``
    One company's own database, encrypted with that company's key. A caller can
    only ever name one company, so reading another company's data is not a rule
    to remember — it is not reachable.

Both are SQLCipher connections. The database files carry no readable SQLite
header, so a stolen disk or a stolen backup yields nothing without the key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlcipher3 import dbapi2 as sqlcipher

from backend.security import keyring
from config.settings import config
from database.schema_control import (
    CONTROL_INDEXES,
    CONTROL_TABLES,
    DEFAULT_PERMISSIONS,
    DEFAULT_PLANS,
    DEFAULT_ROLES,
)
from database.schema_tenant import (
    DEFAULT_SETTINGS,
    TENANT_INDEXES,
    TENANT_SCHEMA_VERSION,
    TENANT_TABLES,
)


CONTROL_FILENAME = "control.db"
TENANT_DIRNAME = "tenants"

BUSY_TIMEOUT_MS = 15_000


class DatabaseError(RuntimeError):
    """Raised when a database cannot be opened or provisioned."""


class UnknownCompany(DatabaseError):
    """The requested company has no provisioned database."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_control_key(master_key: bytes) -> bytes:
    """Derive the control database key from the master key.

    Using a derived subkey rather than the master key itself means the control
    file and the key-wrapping operations never share key material directly.
    """
    return hmac.new(master_key, b"tzone:control-db:v1", hashlib.sha256).digest()


class DatabaseManager:
    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = Path(data_dir or config.DATA_DIR)
        self._tenant_dir = self._data_dir / TENANT_DIRNAME
        self._control_path = self._data_dir / CONTROL_FILENAME

        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._tenant_dir.mkdir(parents=True, exist_ok=True)

        self._master_key: bytes | None = None
        self._key_cache: dict[int, bytes] = {}
        self._lock = threading.RLock()
        self._control_ready = False

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def master_key(self) -> bytes:
        with self._lock:
            if self._master_key is None:
                self._master_key = keyring.load_master_key()
            return self._master_key

    def reset_cache(self) -> None:
        """Drop cached keys. Used by tests and after a key rotation."""
        with self._lock:
            self._master_key = None
            self._key_cache.clear()
            self._control_ready = False

    def company_key(self, company_id: int) -> bytes:
        """Return a company's database key, unwrapping it once and caching it."""
        company_id = int(company_id)

        with self._lock:
            cached = self._key_cache.get(company_id)

        if cached is not None:
            return cached

        with self.control() as conn:
            row = conn.execute(
                """
                SELECT key_sealed_master
                FROM company_databases
                WHERE company_id = ?
                LIMIT 1
                """,
                (company_id,),
            ).fetchone()

        if not row:
            raise UnknownCompany(
                f"Company {company_id} has no provisioned database. "
                "Provision it with `python -m tools.manage_platform create-company`."
            )

        company_key = keyring.unwrap_with_master(
            row["key_sealed_master"],
            company_id,
            self.master_key(),
        )

        with self._lock:
            self._key_cache[company_id] = company_key

        return company_key

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------

    def _open(self, path: Path, key: bytes):
        connection = sqlcipher.connect(
            str(path),
            timeout=BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        connection.row_factory = sqlcipher.Row

        # PRAGMA key must be the first statement on the connection; anything
        # before it operates on an unkeyed database and fails.
        connection.execute(f"PRAGMA key = \"{keyring.sqlcipher_key_literal(key)}\"")

        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")

            # Forces SQLCipher to decrypt a page. A wrong key is only detected
            # on first read, so without this the failure surfaces later as an
            # unrelated-looking query error.
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher.Error as exc:
            connection.close()
            raise DatabaseError(
                f"Could not decrypt {path.name}. The key does not match this "
                "file, or the file is not a SQLCipher database."
            ) from exc

        return connection

    @contextmanager
    def control(self) -> Iterator[Any]:
        """Open the shared control-plane database."""
        self._ensure_control_schema()
        connection = self._open(
            self._control_path,
            _derive_control_key(self.master_key()),
        )
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def tenant(self, company_id: int) -> Iterator[Any]:
        """Open one company's encrypted database."""
        company_id = int(company_id)
        path = self.tenant_path(company_id)

        if not path.exists():
            raise UnknownCompany(
                f"Database file for company {company_id} is missing at {path}."
            )

        connection = self._open(path, self.company_key(company_id))
        try:
            yield connection
        finally:
            connection.close()

    def tenant_path(self, company_id: int) -> Path:
        return self._tenant_dir / f"company_{int(company_id)}.db"

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_control_schema(self) -> None:
        with self._lock:
            if self._control_ready:
                return
            # Set before building so the control() call below does not recurse.
            self._control_ready = True

        try:
            connection = self._open(
                self._control_path,
                _derive_control_key(self.master_key()),
            )
        except Exception:
            with self._lock:
                self._control_ready = False
            raise

        try:
            for statement in CONTROL_TABLES:
                connection.execute(statement)
            for statement in CONTROL_INDEXES:
                connection.execute(statement)

            now = utc_now_iso()

            connection.executemany(
                """
                INSERT INTO permissions (code, name, description, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description
                """,
                [(code, name, desc, now) for code, name, desc in DEFAULT_PERMISSIONS],
            )

            connection.executemany(
                """
                INSERT OR IGNORE INTO plans (
                    name, code, price_monthly, max_users, max_channel_accounts,
                    max_ai_messages, max_knowledge_items, voice_ai_enabled,
                    image_ai_enabled, accounting_connector_enabled,
                    product_connector_enabled, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(*plan, now) for plan in DEFAULT_PLANS],
            )

            connection.commit()
        finally:
            connection.close()

    def _build_tenant_schema(self, connection) -> None:
        for statement in TENANT_TABLES:
            connection.execute(statement)
        for statement in TENANT_INDEXES:
            connection.execute(statement)

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def provision_company(
        self,
        *,
        company_id: int,
        workspace_code: str,
    ) -> Path:
        """Create and register one company's encrypted database.

        Generates a fresh key, seals it under both the master key and the
        workspace code, creates the file, builds the schema and seeds defaults.
        Safe to call only once per company; provisioning an existing company
        raises rather than risking an unreadable overwrite.
        """
        company_id = int(company_id)
        path = self.tenant_path(company_id)

        if path.exists():
            raise DatabaseError(
                f"Company {company_id} already has a database at {path}. "
                "Refusing to overwrite it."
            )

        company_key = keyring.generate_company_key()
        salt = keyring.generate_salt()
        master_key = self.master_key()

        sealed_master = keyring.wrap_with_master(company_key, company_id, master_key)
        sealed_code = keyring.wrap_with_code(
            company_key, workspace_code, salt, company_id
        )

        connection = self._open(path, company_key)
        try:
            self._build_tenant_schema(connection)

            now = utc_now_iso()
            connection.executemany(
                """
                INSERT OR IGNORE INTO company_settings (
                    company_id, section, settings_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (company_id, section, json.dumps(values, ensure_ascii=False), now, now)
                    for section, values in DEFAULT_SETTINGS.items()
                ],
            )
            connection.commit()
        except Exception:
            connection.close()
            path.unlink(missing_ok=True)
            raise
        else:
            connection.close()

        now = utc_now_iso()
        with self.control() as conn:
            conn.execute(
                """
                INSERT INTO company_databases (
                    company_id, database_filename, key_sealed_master,
                    key_sealed_code, code_salt, schema_version,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    path.name,
                    sealed_master,
                    sealed_code,
                    salt.hex(),
                    TENANT_SCHEMA_VERSION,
                    now,
                    now,
                ),
            )
            conn.commit()

        with self._lock:
            self._key_cache[company_id] = company_key

        return path

    def verify_workspace_code(self, company_id: int, workspace_code: str) -> bool:
        """Check a typed workspace code against the company's sealed key."""
        company_id = int(company_id)

        with self.control() as conn:
            row = conn.execute(
                """
                SELECT key_sealed_code, code_salt
                FROM company_databases
                WHERE company_id = ?
                LIMIT 1
                """,
                (company_id,),
            ).fetchone()

        if not row:
            return False

        return keyring.verify_workspace_code(
            row["key_sealed_code"],
            workspace_code,
            bytes.fromhex(row["code_salt"]),
            company_id,
        )

    def rotate_workspace_code(self, company_id: int, new_workspace_code: str) -> None:
        """Change a company's workspace code without re-encrypting its data."""
        company_id = int(company_id)

        with self.control() as conn:
            row = conn.execute(
                "SELECT key_sealed_master FROM company_databases WHERE company_id = ?",
                (company_id,),
            ).fetchone()

            if not row:
                raise UnknownCompany(f"Company {company_id} is not provisioned.")

            sealed_code, salt = keyring.rewrap_with_new_code(
                row["key_sealed_master"],
                company_id,
                new_workspace_code,
                self.master_key(),
            )

            conn.execute(
                """
                UPDATE company_databases
                SET key_sealed_code = ?,
                    code_salt = ?,
                    code_rotated_at = ?,
                    updated_at = ?
                WHERE company_id = ?
                """,
                (sealed_code, salt.hex(), utc_now_iso(), utc_now_iso(), company_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Webhook routing
    # ------------------------------------------------------------------

    def resolve_company_for_channel(
        self,
        *,
        channel: str,
        page_id: str | None = None,
        phone_number_id: str | None = None,
        instagram_business_id: str | None = None,
    ) -> int | None:
        """Map an inbound message to the company that owns the receiving account.

        Returns ``None`` when nothing matches, which the webhook treats as a
        message for an account this platform does not serve. Guessing a company
        here is what previously funnelled every company's traffic into company 1.
        """
        candidates = [
            ("page_id", page_id),
            ("phone_number_id", phone_number_id),
            ("instagram_business_id", instagram_business_id),
            ("external_account_id", page_id or instagram_business_id or phone_number_id),
        ]

        with self.control() as conn:
            for column, value in candidates:
                if not value:
                    continue

                row = conn.execute(
                    f"""
                    SELECT company_id
                    FROM channel_accounts
                    WHERE {column} = ?
                      AND status = 'active'
                    LIMIT 1
                    """,
                    (str(value),),
                ).fetchone()

                if row:
                    return int(row["company_id"])

        return None

    def default_company_id(self) -> int | None:
        """Return the only active company, when the platform serves exactly one.

        Deliberately returns ``None`` when several companies exist: with more
        than one tenant there is no safe default, and picking the lowest id would
        silently misroute another company's customers.
        """
        with self.control() as conn:
            rows = conn.execute(
                "SELECT id FROM companies WHERE status = 'active' ORDER BY id LIMIT 2"
            ).fetchall()

        if len(rows) == 1:
            return int(rows[0]["id"])

        return None

    def list_company_ids(self) -> list[int]:
        with self.control() as conn:
            rows = conn.execute(
                """
                SELECT companies.id
                FROM companies
                JOIN company_databases
                    ON company_databases.company_id = companies.id
                WHERE companies.status = 'active'
                ORDER BY companies.id
                """
            ).fetchall()

        return [int(row["id"]) for row in rows]


database_manager = DatabaseManager()
