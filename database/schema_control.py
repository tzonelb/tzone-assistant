"""Schema for the shared control-plane database.

This database holds only what the platform must read *before* it knows which
company a request belongs to: who the user is, which companies they may open,
and which company a webhook should be routed to. Everything a company actually
owns — conversations, customers, messages, settings — lives in that company's
own encrypted database and never appears here.

This module is the single source of truth for these tables. No service may
create or alter a control-plane table on its own; duplicate definitions across
modules is exactly what previously left the platform unable to boot on a fresh
database.
"""

from __future__ import annotations


CONTROL_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS workspaces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'active',
        owner_email TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
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
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(workspace_id, slug),
        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
    )
    """,
    # The registry that makes per-company encryption work. `key_sealed_master`
    # lets the server open the database unattended; `key_sealed_code` is the
    # same key sealed behind the workspace code an employee types at login.
    """
    CREATE TABLE IF NOT EXISTS company_databases (
        company_id INTEGER PRIMARY KEY,
        database_filename TEXT NOT NULL UNIQUE,
        key_sealed_master TEXT NOT NULL,
        key_sealed_code TEXT NOT NULL,
        code_salt TEXT NOT NULL,
        code_rotated_at TEXT,
        schema_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        code TEXT,
        address TEXT,
        phone TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT,
        full_name TEXT,
        phone TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        is_super_admin INTEGER NOT NULL DEFAULT 0,
        last_login_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        name TEXT NOT NULL,
        code TEXT NOT NULL,
        description TEXT,
        is_system INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(company_id, code),
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS role_permissions (
        role_id INTEGER NOT NULL,
        permission_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(role_id, permission_id),
        FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE,
        FOREIGN KEY(permission_id) REFERENCES permissions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        role_id INTEGER,
        branch_id INTEGER,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        UNIQUE(company_id, user_id),
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE SET NULL,
        FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        company_id INTEGER,
        token_hash TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        ip_address TEXT,
        user_agent TEXT,
        created_at TEXT NOT NULL,
        last_used_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    # Backs login throttling. Recording attempts in the database rather than in
    # process memory means the limit survives a restart and still holds when the
    # API runs behind more than one worker.
    """
    CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        ip_address TEXT,
        succeeded INTEGER NOT NULL DEFAULT 0,
        failure_reason TEXT,
        created_at TEXT NOT NULL
    )
    """,
    # Routing table for inbound webhooks. This must be control-plane: when a
    # message arrives we only know the page id or phone number id, and we need
    # this mapping to decide which company's database to open.
    """
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
        access_token_sealed TEXT,
        verify_token_sealed TEXT,
        app_secret_sealed TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        ai_enabled INTEGER NOT NULL DEFAULT 1,
        flow_enabled INTEGER NOT NULL DEFAULT 1,
        voice_ai_enabled INTEGER NOT NULL DEFAULT 0,
        image_ai_enabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
        FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        code TEXT NOT NULL UNIQUE,
        price_monthly REAL NOT NULL DEFAULT 0,
        max_users INTEGER NOT NULL DEFAULT 0,
        max_channel_accounts INTEGER NOT NULL DEFAULT 0,
        max_ai_messages INTEGER NOT NULL DEFAULT 0,
        max_knowledge_items INTEGER NOT NULL DEFAULT 0,
        voice_ai_enabled INTEGER NOT NULL DEFAULT 0,
        image_ai_enabled INTEGER NOT NULL DEFAULT 0,
        accounting_connector_enabled INTEGER NOT NULL DEFAULT 0,
        product_connector_enabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        plan_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        starts_at TEXT NOT NULL,
        expires_at TEXT,
        grace_period_until TEXT,
        auto_renew INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
        FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id INTEGER,
        company_id INTEGER,
        actor_user_id INTEGER,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id TEXT,
        data_json TEXT,
        ip_address TEXT,
        created_at TEXT NOT NULL
    )
    """,
)


CONTROL_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_companies_workspace ON companies(workspace_id)",
    "CREATE INDEX IF NOT EXISTS idx_branches_company ON branches(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_users_user ON company_users(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_users_company ON company_users(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_company ON channel_accounts(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_page ON channel_accounts(page_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_phone ON channel_accounts(phone_number_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_instagram ON channel_accounts(instagram_business_id)",
    "CREATE INDEX IF NOT EXISTS idx_subscriptions_company ON subscriptions(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_company ON audit_log(company_id, created_at)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_external_account
    ON channel_accounts(channel, external_account_id)
    WHERE external_account_id IS NOT NULL
    """,
)


# Every permission listed here is enforced somewhere in the API. Adding a code
# to this list without enforcing it recreates the problem where the roles screen
# promised access control the backend never applied.
DEFAULT_PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("dashboard.view", "View Dashboard", "Open the dashboard and see company totals."),
    ("analytics.view", "View Analytics", "See channel, assistant and team performance reports."),
    ("conversations.view", "View Conversations", "Open the shared inbox and read conversations."),
    ("conversations.reply", "Reply to Conversations", "Take over a conversation and send replies."),
    ("conversations.manage", "Manage Conversations", "Reassign, tag, archive and close any conversation."),
    ("customers.view", "View Customers", "Open the customer directory."),
    ("customers.manage", "Manage Customers", "Edit customer records and internal notes."),
    ("knowledge.view", "View Knowledge", "Read the knowledge base used by the assistant."),
    ("knowledge.manage", "Manage Knowledge", "Add, edit and remove knowledge entries."),
    ("channels.view", "View Channels", "See connected messaging channels."),
    ("channels.manage", "Manage Channels", "Connect, edit and disconnect messaging channels."),
    ("users.view", "View Users", "See team members and their roles."),
    ("users.manage", "Manage Users", "Invite users, assign roles and edit permissions."),
    ("settings.view", "View Settings", "Open company settings."),
    ("settings.manage", "Manage Settings", "Change company settings, including assistant behaviour."),
    ("subscriptions.view", "View Subscription", "See the current plan and billing status."),
    ("subscriptions.manage", "Manage Subscription", "Change the plan and billing details."),
)


# Roles created for every new company. `owner` is granted everything in code, so
# it deliberately carries no explicit permission list.
DEFAULT_ROLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("Owner", "owner", "Full access to the company.", ()),
    (
        "Manager",
        "manager",
        "Runs day-to-day operations and the team.",
        (
            "dashboard.view",
            "analytics.view",
            "conversations.view",
            "conversations.reply",
            "conversations.manage",
            "customers.view",
            "customers.manage",
            "knowledge.view",
            "knowledge.manage",
            "channels.view",
            "users.view",
            "settings.view",
            "subscriptions.view",
        ),
    ),
    (
        "Agent",
        "agent",
        "Answers customers in the shared inbox.",
        (
            "dashboard.view",
            "conversations.view",
            "conversations.reply",
            "customers.view",
            "knowledge.view",
        ),
    ),
    (
        "Viewer",
        "viewer",
        "Read-only access for reporting.",
        (
            "dashboard.view",
            "analytics.view",
            "conversations.view",
            "customers.view",
            "knowledge.view",
            "subscriptions.view",
        ),
    ),
)


DEFAULT_PLANS: tuple[tuple, ...] = (
    ("Starter", "starter", 19, 2, 1, 1000, 100, 0, 0, 0, 0),
    ("Business", "business", 49, 10, 5, 5000, 1000, 1, 1, 1, 1),
    ("Enterprise", "enterprise", 0, 100, 50, 100000, 100000, 1, 1, 1, 1),
)
