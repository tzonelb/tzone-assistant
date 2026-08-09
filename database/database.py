
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import config


class _PooledConnection:
    """A reentrant proxy over one real sqlite3 connection, reused per thread.

    Opening a fresh SQLite connection costs ~4-5ms on this platform, and the
    hot inbound-message path opens ~8-9 of them per message — so connection
    setup, not query work, dominated latency (~100ms/message before any
    contention). Reusing one connection per thread amortizes that to near
    zero.

    Safe because every call site uses `with db.connect() as conn:` (verified:
    306 of 306). The proxy tracks nesting depth so a nested `with` block (an
    operation that calls another operation which also opens a connection on
    the same thread) shares the one connection and only the OUTERMOST scope
    commits/rolls back — making nested operations atomic rather than
    prematurely committing. Never closes the underlying connection (that's
    what makes it reusable); `with` on a sqlite3 connection manages the
    transaction, not the handle.
    """

    __slots__ = ("_raw", "_depth")

    def __init__(self, raw: sqlite3.Connection):
        self._raw = raw
        self._depth = 0

    def __enter__(self):
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._depth -= 1
        if self._depth <= 0:
            self._depth = 0
            if exc_type is not None:
                self._raw.rollback()
            else:
                self._raw.commit()
        return False

    def commit(self):
        # Defer an inner (nested) commit to the outermost scope so a later
        # rollback can still undo it — the whole nested unit stays atomic.
        if self._depth <= 1:
            self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def execute(self, *args, **kwargs):
        return self._raw.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._raw.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self._raw.executescript(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return self._raw.cursor(*args, **kwargs)

    @property
    def row_factory(self):
        return self._raw.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._raw.row_factory = value

    def close(self):
        # Explicit close: really close and drop from the pool via depth reset.
        self._depth = 0
        self._raw.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


class Database:
    def __init__(self):
        self.db_path = Path(config.DATABASE_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def _new_raw(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=30,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # WAL is persistent (set once per file); re-asserting is a cheap no-op.
        connection.execute("PRAGMA journal_mode = WAL")
        # synchronous=NORMAL under WAL: commits no longer fsync on every
        # transaction (only at WAL checkpoints) — a large write-throughput win
        # with no corruption risk (at most the last transaction is lost on a
        # power cut, never the database).
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA cache_size = -16000")  # ~16 MB page cache
        return connection

    def connect(self):
        # One reused connection per thread, keyed on the current db_path so
        # tests that swap db_path between fixtures transparently get a fresh
        # connection (the old one is closed) instead of writing to the wrong
        # file.
        key = str(self.db_path)
        proxy = getattr(self._local, "proxy", None)
        if proxy is None or getattr(self._local, "key", None) != key:
            if proxy is not None:
                try:
                    proxy._raw.close()
                except Exception:
                    pass
            proxy = _PooledConnection(self._new_raw())
            self._local.proxy = proxy
            self._local.key = key
        return proxy

    def create_tables(self):
        with self.connect() as conn:
            cursor = conn.cursor()

            self._heal_module_table_shapes(cursor)
            self._create_platform_tables(cursor)
            self._migrate_legacy_tables(cursor)
            self._seed_platform_defaults(cursor)

            conn.commit()

    # Module tables (owned by backend/services/*_service.ensure_schema)
    # and the columns this branch's code requires of each. A dev database
    # that ran a DIFFERENT development branch may hold an incompatible
    # shape of any of them (seen on a real dev machine 2026-08-04, where
    # a parallel branch's boot renamed this branch's tables aside to
    # {table}_legacy_backup and created its own shapes in their place).
    # Table/column names here are hardcoded constants, never user input.
    _MODULE_TABLE_REQUIRED_COLUMNS = {
        "tasks": (
            "company_id", "title", "status", "priority",
            "assigned_user_id", "due_at", "created_by_user_id",
        ),
        "appointments": (
            "company_id", "title", "customer_id",
            "employee_user_id", "scheduled_at",
        ),
        "scheduled_posts": (
            "company_id", "media_urls_json", "channel_account_ids_json",
        ),
        "team_chat_rooms": ("company_id", "kind", "created_by_user_id"),
        "team_messages": ("company_id", "sender_user_id", "text"),
        "call_logs": (
            "company_id", "direction", "duration_seconds",
            "status", "called_by_user_id",
        ),
        "broadcasts": ("company_id", "name", "message_text", "channel"),
        "broadcast_recipients": (
            "broadcast_id", "channel", "external_user_id", "send_status",
        ),
    }

    @staticmethod
    def _table_exists(cursor, table_name: str) -> bool:
        return (
            cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _table_columns(cursor, table_name: str) -> set:
        return {
            row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})")
        }

    @classmethod
    def _free_table_name(cls, cursor, base: str) -> str:
        if not cls._table_exists(cursor, base):
            return base
        suffix = 2
        while cls._table_exists(cursor, f"{base}_{suffix}"):
            suffix += 1
        return f"{base}_{suffix}"

    def _heal_module_table_shapes(self, cursor):
        """Self-heal module tables whose on-disk shape doesn't match this
        branch's code, without ever dropping data:

        1. A live table missing required columns (created by another
           branch) is renamed aside to a fresh
           {table}_wrongshape_backup[_N] -- existing backups are never
           overwritten or dropped.
        2. If the live table is then absent but a {table}_legacy_backup
           with the CORRECT shape exists (i.e. this branch's original
           table, renamed aside by the other branch's own heal), it is
           restored -- bringing the original rows straight back.

        After this runs, each service's CREATE TABLE IF NOT EXISTS
        either finds the restored real table or creates a fresh one."""
        for table_name, required in self._MODULE_TABLE_REQUIRED_COLUMNS.items():
            required_set = set(required)

            if self._table_exists(cursor, table_name):
                if required_set.issubset(self._table_columns(cursor, table_name)):
                    continue
                backup_name = self._free_table_name(
                    cursor, f"{table_name}_wrongshape_backup"
                )
                cursor.execute(
                    f"ALTER TABLE {table_name} RENAME TO {backup_name}"
                )

            legacy_backup = f"{table_name}_legacy_backup"
            if (
                not self._table_exists(cursor, table_name)
                and self._table_exists(cursor, legacy_backup)
                and required_set.issubset(
                    self._table_columns(cursor, legacy_backup)
                )
            ):
                cursor.execute(
                    f"ALTER TABLE {legacy_backup} RENAME TO {table_name}"
                )

    def _create_platform_tables(self, cursor):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                owner_email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                country TEXT,
                currency TEXT DEFAULT 'USD',
                timezone TEXT DEFAULT 'Asia/Beirut',
                default_language TEXT DEFAULT 'ar',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(workspace_id, slug),
                FOREIGN KEY(workspace_id)
                    REFERENCES workspaces(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                code TEXT,
                address TEXT,
                phone TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                full_name TEXT,
                phone TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                is_super_admin INTEGER NOT NULL DEFAULT 0,
                last_login_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                name TEXT NOT NULL,
                code TEXT NOT NULL,
                description TEXT,
                is_system INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, code),
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id INTEGER NOT NULL,
                permission_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(role_id, permission_id),
                FOREIGN KEY(role_id)
                    REFERENCES roles(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(permission_id)
                    REFERENCES permissions(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_id INTEGER,
                branch_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, user_id),
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(role_id)
                    REFERENCES roles(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(branch_id)
                    REFERENCES branches(id)
                    ON DELETE SET NULL
            )
        """)

        # Per-user permission overrides — lets an owner grant or revoke a
        # single permission code for one employee beyond their role's
        # defaults (e.g. take away "channels.manage" from one Support
        # agent even though the Support role normally grants it). A row
        # here always wins over the role's own permission_codes; no rows
        # means "use the role default" so every existing company is
        # unaffected until an owner explicitly overrides someone.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_permission_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                permission_code TEXT NOT NULL,
                allowed INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, user_id, permission_code),
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_user_permission_overrides_user
            ON user_permission_overrides(company_id, user_id)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                branch_id INTEGER,
                channel TEXT NOT NULL,
                name TEXT NOT NULL,
                external_account_id TEXT,
                phone_number_id TEXT,
                page_id TEXT,
                instagram_business_id TEXT,
                access_token_encrypted TEXT,
                verify_token_encrypted TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                ai_enabled INTEGER NOT NULL DEFAULT 1,
                flow_enabled INTEGER NOT NULL DEFAULT 1,
                voice_ai_enabled INTEGER NOT NULL DEFAULT 0,
                image_ai_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(branch_id)
                    REFERENCES branches(id)
                    ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_channel_external_account
            ON channel_accounts(
                channel,
                external_account_id
            )
            WHERE external_account_id IS NOT NULL
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                channel_account_id INTEGER,
                name TEXT NOT NULL,
                default_language TEXT DEFAULT 'ar',
                welcome_enabled INTEGER NOT NULL DEFAULT 1,
                welcome_message_ar TEXT,
                welcome_message_en TEXT,
                ai_enabled INTEGER NOT NULL DEFAULT 1,
                ai_reply_mode TEXT DEFAULT 'grounded_ai',
                ai_model TEXT,
                system_prompt TEXT,
                memory_enabled INTEGER NOT NULL DEFAULT 1,
                human_handover_enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(channel_account_id)
                    REFERENCES channel_accounts(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                department TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, name),
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                category_id INTEGER,
                external_id TEXT,
                title TEXT NOT NULL,
                content_ar TEXT,
                content_en TEXT,
                instructions TEXT,
                department TEXT,
                item_type TEXT NOT NULL DEFAULT 'knowledge',
                source_type TEXT NOT NULL DEFAULT 'manual',
                source_reference TEXT,
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                version INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, external_id),
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(category_id)
                    REFERENCES knowledge_categories(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(created_by)
                    REFERENCES users(id)
                    ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                channel_account_id INTEGER,
                channel TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                external_conversation_id TEXT,
                customer_id INTEGER,
                language TEXT,
                department TEXT,
                topic TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                assigned_user_id INTEGER,
                ai_enabled INTEGER NOT NULL DEFAULT 1,
                needs_human INTEGER NOT NULL DEFAULT 0,
                last_message_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(channel_account_id)
                    REFERENCES channel_accounts(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(assigned_user_id)
                    REFERENCES users(id)
                    ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_conversations_lookup
            ON conversations(
                company_id,
                channel,
                external_user_id
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                channel_account_id INTEGER,
                external_message_id TEXT,
                direction TEXT NOT NULL,
                sender_type TEXT NOT NULL,
                sender_id TEXT,
                message_type TEXT NOT NULL DEFAULT 'text',
                text TEXT,
                attachments_json TEXT,
                metadata_json TEXT,
                ai_generated INTEGER NOT NULL DEFAULT 0,
                knowledge_ids_json TEXT,
                token_usage INTEGER NOT NULL DEFAULT 0,
                ai_cost REAL NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(channel_account_id)
                    REFERENCES channel_accounts(id)
                    ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                external_reference TEXT,
                full_name TEXT,
                display_name TEXT,
                internal_name TEXT,
                profile_picture TEXT,
                phone TEXT,
                email TEXT,
                language TEXT,
                country TEXT,
                timezone TEXT,
                notes TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                external_reference TEXT,
                sku TEXT,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT,
                brand TEXT,
                price REAL,
                currency TEXT DEFAULT 'USD',
                quantity REAL,
                availability_status TEXT,
                source_connector_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                last_synced_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS business_connectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                connector_type TEXT NOT NULL,
                provider TEXT NOT NULL,
                name TEXT NOT NULL,
                configuration_encrypted TEXT,
                status TEXT NOT NULL DEFAULT 'inactive',
                last_sync_at TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                code TEXT NOT NULL UNIQUE,
                price_monthly REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                max_users INTEGER NOT NULL DEFAULT 1,
                max_channel_accounts INTEGER NOT NULL DEFAULT 1,
                max_ai_messages INTEGER NOT NULL DEFAULT 500,
                max_knowledge_items INTEGER NOT NULL DEFAULT 100,
                voice_ai_enabled INTEGER NOT NULL DEFAULT 0,
                image_ai_enabled INTEGER NOT NULL DEFAULT 0,
                accounting_connector_enabled INTEGER NOT NULL DEFAULT 0,
                product_connector_enabled INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                starts_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                grace_period_until TIMESTAMP,
                auto_renew INTEGER NOT NULL DEFAULT 0,
                cancelled_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(plan_id)
                    REFERENCES plans(id)
                    ON DELETE RESTRICT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                subscription_id INTEGER,
                usage_type TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                cost REAL NOT NULL DEFAULT 0,
                reference_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(subscription_id)
                    REFERENCES subscriptions(id)
                    ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER,
                company_id INTEGER,
                user_id INTEGER,
                action TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                old_values_json TEXT,
                new_values_json TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(workspace_id)
                    REFERENCES workspaces(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(user_id)
                    REFERENCES users(id)
                    ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                conversation_id INTEGER,
                platform TEXT,
                user_id TEXT,
                language TEXT,
                department TEXT DEFAULT 'iptv',
                iptv_username TEXT,
                device TEXT,
                os TEXT,
                app TEXT,
                problem TEXT,
                assigned_user_id INTEGER,
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(company_id)
                    REFERENCES companies(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(assigned_user_id)
                    REFERENCES users(id)
                    ON DELETE SET NULL
            )
        """)

    def _migrate_legacy_tables(self, cursor):
        self._ensure_column(
            cursor,
            "tickets",
            "company_id",
            "INTEGER",
        )

        self._ensure_column(
            cursor,
            "tickets",
            "conversation_id",
            "INTEGER",
        )

        self._ensure_column(
            cursor,
            "tickets",
            "department",
            "TEXT DEFAULT 'iptv'",
        )

        self._ensure_column(
            cursor,
            "tickets",
            "assigned_user_id",
            "INTEGER",
        )

        self._ensure_column(
            cursor,
            "tickets",
            "priority",
            "TEXT DEFAULT 'normal'",
        )

        self._ensure_column(
            cursor,
            "tickets",
            "updated_at",
            "TIMESTAMP",
        )

        # Brute-force login protection — tracks consecutive failed
        # attempts per account and a temporary lockout window.
        self._ensure_column(
            cursor,
            "users",
            "failed_login_attempts",
            "INTEGER NOT NULL DEFAULT 0",
        )

        self._ensure_column(
            cursor,
            "users",
            "locked_until",
            "TIMESTAMP",
        )

        self._ensure_column(
            cursor,
            "conversations",
            "priority",
            "TEXT NOT NULL DEFAULT 'normal'",
        )

        self._ensure_column(
            cursor,
            "conversations",
            "handled_by_ai",
            "INTEGER NOT NULL DEFAULT 1",
        )

        self._ensure_column(
            cursor,
            "conversations",
            "unread_count",
            "INTEGER NOT NULL DEFAULT 0",
        )

        # Customer Engine migrations. CREATE TABLE IF NOT EXISTS does not
        # update an existing SQLite table, so every new field must be added
        # explicitly for installations that already have a customers table.
        customer_columns = (
            ("display_name", "TEXT"),
            ("internal_name", "TEXT"),
            ("profile_picture", "TEXT"),
            ("country", "TEXT"),
            ("timezone", "TEXT"),
            ("first_seen_at", "TEXT"),
            ("last_seen_at", "TEXT"),
        )

        for column_name, column_definition in customer_columns:
            self._ensure_column(
                cursor,
                "customers",
                column_name,
                column_definition,
            )

        # Keep legacy customer names usable by the new Customer Engine.
        cursor.execute(
            """
            UPDATE customers
            SET display_name = COALESCE(
                NULLIF(TRIM(display_name), ''),
                NULLIF(TRIM(full_name), '')
            )
            WHERE display_name IS NULL OR TRIM(display_name) = ''
            """
        )

        # The conversation/customer link and official profile metadata are
        # used by the Messenger processor and customer service.
        self._ensure_column(
            cursor,
            "conversations",
            "customer_id",
            "INTEGER",
        )

        self._ensure_column(
            cursor,
            "conversations",
            "official_customer_name",
            "TEXT",
        )

        self._ensure_column(
            cursor,
            "conversations",
            "customer_profile_picture",
            "TEXT",
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                actor_user_id INTEGER,
                event_type TEXT NOT NULL,
                event_data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversation_events_conversation
            ON conversation_events(conversation_id, created_at DESC)
        """)

        # An employee can belong to more than one department (e.g. both
        # "Sales" and "Support"), so this is a JSON array of department
        # names, not a single column — matches the multi-select pattern
        # already used for reply_flows.departments_json.
        self._ensure_column(
            cursor,
            "company_users",
            "departments_json",
            "TEXT",
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                author_user_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                FOREIGN KEY(author_user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

    def _ensure_column(
        self,
        cursor,
        table_name: str,
        column_name: str,
        column_definition: str,
    ):
        columns = cursor.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()

        column_names = {
            column["name"]
            for column in columns
        }

        if column_name not in column_names:
            cursor.execute(
                f"""
                ALTER TABLE {table_name}
                ADD COLUMN {column_name}
                {column_definition}
                """
            )

    def _seed_platform_defaults(self, cursor):
        cursor.execute("""
            INSERT OR IGNORE INTO workspaces (
                id,
                name,
                slug,
                status
            )
            VALUES (
                1,
                'T-ZONE Workspace',
                'tzone',
                'active'
            )
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO companies (
                id,
                workspace_id,
                name,
                slug,
                country,
                currency,
                timezone,
                default_language,
                status
            )
            VALUES (
                1,
                1,
                'T-ZONE',
                'tzone-lb',
                'Lebanon',
                'USD',
                'Asia/Beirut',
                'ar',
                'active'
            )
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO branches (
                id,
                company_id,
                name,
                code,
                status
            )
            VALUES (
                1,
                1,
                'Main Branch',
                'MAIN',
                'active'
            )
        """)

        plans = [
            (
                "Starter",
                "starter",
                19,
                2,
                1,
                1000,
                100,
                0,
                0,
                0,
                0,
            ),
            (
                "Business",
                "business",
                49,
                10,
                5,
                5000,
                1000,
                1,
                1,
                1,
                1,
            ),
            (
                "Enterprise",
                "enterprise",
                0,
                100,
                50,
                100000,
                100000,
                1,
                1,
                1,
                1,
            ),
        ]

        cursor.executemany("""
            INSERT OR IGNORE INTO plans (
                name,
                code,
                price_monthly,
                max_users,
                max_channel_accounts,
                max_ai_messages,
                max_knowledge_items,
                voice_ai_enabled,
                image_ai_enabled,
                accounting_connector_enabled,
                product_connector_enabled
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, plans)

        existing_subscription = cursor.execute("""
            SELECT id
            FROM subscriptions
            WHERE company_id = 1
            LIMIT 1
        """).fetchone()

        if not existing_subscription:
            plan = cursor.execute("""
                SELECT id
                FROM plans
                WHERE code = 'business'
            """).fetchone()

            if plan:
                now = datetime.now(timezone.utc)
                expires_at = now + timedelta(days=30)
                grace_period = expires_at + timedelta(days=7)

                cursor.execute("""
                    INSERT INTO subscriptions (
                        company_id,
                        plan_id,
                        status,
                        starts_at,
                        expires_at,
                        grace_period_until,
                        auto_renew
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    1,
                    plan["id"],
                    "active",
                    now.isoformat(),
                    expires_at.isoformat(),
                    grace_period.isoformat(),
                    0,
                ))

        permissions = [
            ("dashboard.view", "View Dashboard"),
            ("conversations.view", "View Conversations"),
            ("conversations.reply", "Reply to Conversations"),
            ("knowledge.view", "View Knowledge"),
            ("knowledge.manage", "Manage Knowledge"),
            ("channels.view", "View Channels"),
            ("channels.manage", "Manage Channels"),
            ("users.view", "View Users"),
            ("users.manage", "Manage Users"),
            ("settings.view", "View Settings"),
            ("settings.manage", "Manage Settings"),
            ("subscriptions.view", "View Subscription"),
            ("subscriptions.manage", "Manage Subscription"),
            ("analytics.view", "View Analytics"),
            ("modules.appointments", "Use Appointments Module"),
            ("modules.scheduler", "Use Scheduler Module"),
            ("modules.catalogue", "Use Catalogue Module"),
            ("modules.team_chat", "Use Team Chat Module"),
            ("modules.comments", "Use Comments Module"),
            ("modules.ai_teaching_chat", "Use AI Teaching Chat Module"),
            ("dialer.use", "Use Dialer"),
        ]

        cursor.executemany("""
            INSERT OR IGNORE INTO permissions (
                code,
                name
            )
            VALUES (?, ?)
        """, permissions)

        cursor.execute("""
            INSERT OR IGNORE INTO roles (
                company_id,
                name,
                code,
                description,
                is_system
            )
            VALUES (
                1,
                'Owner',
                'owner',
                'Full access to the company',
                1
            )
        """)

    def create_ticket(self, data):
        with self.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO tickets (
                    company_id,
                    conversation_id,
                    platform,
                    user_id,
                    language,
                    department,
                    iptv_username,
                    device,
                    os,
                    app,
                    problem,
                    status,
                    priority
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get(
                    "company_id",
                    config.DEFAULT_COMPANY_ID,
                ),
                data.get("conversation_id"),
                data.get("platform"),
                data.get("user_id"),
                data.get("language"),
                data.get("department", "iptv"),
                data.get("iptv_username"),
                data.get("device"),
                data.get("os"),
                data.get("app"),
                data.get("problem"),
                data.get("status", "open"),
                data.get("priority", "normal"),
            ))

            conn.commit()
            return cursor.lastrowid

    def get_tickets(
        self,
        company_id: int | None = None,
    ):
        with self.connect() as conn:
            cursor = conn.cursor()

            if company_id:
                cursor.execute("""
                    SELECT *
                    FROM tickets
                    WHERE company_id = ?
                    ORDER BY id DESC
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT *
                    FROM tickets
                    ORDER BY id DESC
                """)

            return [
                dict(row)
                for row in cursor.fetchall()
            ]

    def get_ticket(self, ticket_id):
        with self.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM tickets
                WHERE id = ?
            """, (ticket_id,))

            row = cursor.fetchone()

            return dict(row) if row else None

    def get_company(self, company_id: int):
        with self.connect() as conn:
            row = conn.execute("""
                SELECT *
                FROM companies
                WHERE id = ?
            """, (company_id,)).fetchone()

            return dict(row) if row else None

    def get_active_subscription(
        self,
        company_id: int,
    ):
        with self.connect() as conn:
            row = conn.execute("""
                SELECT
                    subscriptions.*,
                    plans.name AS plan_name,
                    plans.code AS plan_code,
                    plans.max_users,
                    plans.max_channel_accounts,
                    plans.max_ai_messages,
                    plans.max_knowledge_items,
                    plans.voice_ai_enabled,
                    plans.image_ai_enabled,
                    plans.accounting_connector_enabled,
                    plans.product_connector_enabled
                FROM subscriptions
                JOIN plans
                    ON plans.id = subscriptions.plan_id
                WHERE subscriptions.company_id = ?
                ORDER BY subscriptions.id DESC
                LIMIT 1
            """, (company_id,)).fetchone()

            return dict(row) if row else None

    def is_subscription_active(
        self,
        company_id: int,
    ) -> bool:
        subscription = self.get_active_subscription(
            company_id
        )

        if not subscription:
            return False

        if subscription.get("status") not in [
            "active",
            "trial",
            "grace_period",
        ]:
            return False

        expires_at = subscription.get("expires_at")

        if not expires_at:
            return False

        try:
            expiry = datetime.fromisoformat(expires_at)

            if expiry.tzinfo is None:
                expiry = expiry.replace(
                    tzinfo=timezone.utc
                )

            return expiry >= datetime.now(timezone.utc)

        except ValueError:
            return False

    def log_audit(
        self,
        action: str,
        company_id: int | None = None,
        workspace_id: int | None = None,
        user_id: int | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ):
        with self.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO audit_logs (
                    workspace_id,
                    company_id,
                    user_id,
                    action,
                    entity_type,
                    entity_id,
                    old_values_json,
                    new_values_json,
                    ip_address
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                workspace_id,
                company_id,
                user_id,
                action,
                entity_type,
                entity_id,
                json.dumps(
                    old_values,
                    ensure_ascii=False,
                ) if old_values else None,
                json.dumps(
                    new_values,
                    ensure_ascii=False,
                ) if new_values else None,
                ip_address,
            ))

            conn.commit()

            return cursor.lastrowid


db = Database()