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
        password_changed_at TEXT,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        locked_reason TEXT,
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
        scope TEXT NOT NULL DEFAULT 'company',
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
    # One-time links that let a locked-out or reset employee set a new password.
    #
    # The link itself is never stored — only its SHA-256, exactly as
    # `auth_sessions.token_hash` stores a session. Somebody with read access to
    # this table can see that a reset was issued and to whom; they cannot use it.
    #
    # `created_by_user_id` is the administrator who pressed the button. It is
    # the whole point of the audit trail on this table: a reset link is a way
    # into somebody else's account, so who asked for one matters.
    """
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_by_user_id INTEGER,
        ip_address TEXT,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
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
        department_id INTEGER,
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
    # What the Super Admin decides a company may see and use. Control-plane
    # because the customer app reads it at sign-in, before any tenant database
    # is opened, and because a company must not be able to grant itself a
    # module its plan does not include.
    """
    CREATE TABLE IF NOT EXISTS company_platform_config (
        company_id INTEGER PRIMARY KEY,
        modules_json TEXT NOT NULL DEFAULT '{}',
        branding_json TEXT NOT NULL DEFAULT '{}',
        layout_json TEXT NOT NULL DEFAULT '{}',
        updated_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
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
    # Which companies have background work outstanding, and when the earliest
    # piece of it comes due. Control-plane for the same reason the webhook
    # routing table is: a sweep has to know *whether* to open a company's
    # encrypted database before it opens it, and this is the only place it can
    # read without opening one.
    #
    # Nothing a company owns appears here. "Company 42 has a reply due at
    # 12:01:03" is a scheduling fact about the platform's own queue — no
    # customer, no channel, no message, no content.
    #
    # One row per (company, kind). `due_at` is the earliest outstanding item of
    # that kind, as an ISO-8601 UTC timestamp, so it sorts and compares as text
    # exactly the way the tenant queues already compare their own deadlines.
    #
    # `revision` exists so a sweep can *remove* an entry without racing a writer
    # that is adding one: the sweep re-reads the tenant tables and then writes
    # back only if the revision it read is still current. Adding is unconditional
    # — see `work_index_service` for which direction each caller is allowed to
    # move, and why.
    """
    CREATE TABLE IF NOT EXISTS company_work_index (
        company_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        due_at TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (company_id, kind),
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
    )
    """,
    """
    -- One company's departure from its plan's allowance, set from the console.
    --
    -- Separate from `plans` on purpose. Editing the plan row to accommodate one
    -- customer silently raises the ceiling for every company on that plan, and
    -- nothing records that it happened or why. A row here changes one company,
    -- carries the reason, and can be removed to put them back on the plan.
    --
    -- `value` is NOT NULL: a row that exists means a decision was made. Meaning
    -- "back to the plan" would be a second way of saying what deleting the row
    -- already says, and two ways lead to disagreement.
    CREATE TABLE IF NOT EXISTS company_plan_overrides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        limit_key TEXT NOT NULL,
        value INTEGER NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by_user_id INTEGER,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
        UNIQUE(company_id, limit_key)
    )
    """,
    """
    -- Numbers, never content. What a company is charged for and what it is
    -- measured against — counted per month so a monthly allowance has
    -- something to compare with, and per channel and department so the owner
    -- can see where it went.
    --
    -- In the control plane rather than the company's own database because
    -- billing must survive a tenant database that will not open, and because
    -- the console has to read a thousand companies' totals without holding a
    -- thousand encryption keys.
    CREATE TABLE IF NOT EXISTS usage_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        period TEXT NOT NULL,
        metric TEXT NOT NULL,
        channel TEXT,
        department_id INTEGER,
        quantity INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
        UNIQUE(company_id, period, metric, channel, department_id)
    )
    """,
    # Theme Studio's versioned design tokens.
    #
    # A row is one *patch*, not a snapshot: it holds only the tokens its author
    # actually changed, so the layers below can be merged key by key at read
    # time. Storing a full snapshot instead meant every new draft silently
    # reset each token it did not touch back to the bundled default rather than
    # inheriting the layer beneath it.
    #
    # Scoped rather than per-company, because that is the decision this table
    # records: `platform` reaches every workspace, `plan` reaches the companies
    # on one plan, `company` is one workspace's own override. `scope_id` is the
    # plan code or the company id, and NULL for the platform layer.
    #
    # Control plane and not a tenant database, for two reasons: the platform
    # layer belongs to no company and could not be stored in one, and the read
    # path resolves a workspace's appearance before its encrypted database is
    # ever opened. Nothing customer-owned goes in here — a design token is a
    # colour, a font name and a number of pixels.
    """
    CREATE TABLE IF NOT EXISTS ui_themes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope_type TEXT NOT NULL,
        scope_id TEXT,
        version INTEGER NOT NULL DEFAULT 0,
        tokens_json TEXT NOT NULL,
        modules_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        created_by INTEGER,
        created_at TEXT NOT NULL,
        published_at TEXT
    )
    """,
    # A company asking to move onto a different plan, or to renew the one it is
    # on. Control-plane rather than tenant, and for the same reason the
    # subscription itself is: the operator reviews these across every company
    # from the console, and a table inside one company's encrypted file cannot
    # be read from a screen that lists all of them.
    #
    # Nothing a company owns is here — a plan id, a status and a short note the
    # employee typed about how they paid. No customer, no conversation.
    #
    # `note` is the company's own words (a transfer reference, usually), so it
    # is bounded at the route rather than trusted at whatever length it arrives.
    """
    CREATE TABLE IF NOT EXISTS subscription_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        plan_id INTEGER NOT NULL,
        requested_by_user_id INTEGER,
        note TEXT,
        -- 'pending' until an operator acts on it, then 'approved' or
        -- 'rejected'. No `reviewed_by`/`reviewed_at` columns: the console side
        -- that would set them is not built yet, and columns nothing writes are
        -- how a table comes to hold fields that look answered and are not.
        -- They belong in the release that adds the review screen.
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
        FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE RESTRICT
    )
    """,
    # A company reporting a problem with the platform itself to the T-ZONE team.
    #
    # Distinct from `tickets` in the tenant schema, which is a company's own
    # end-customers' cases and belongs inside that company's file. This one is
    # addressed *to the operator*, so it lives where the operator can read it
    # without opening a company's encrypted database — the same reasoning as
    # `subscription_requests` above.
    """
    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        created_by_user_id INTEGER,
        subject TEXT NOT NULL,
        description TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
    )
    """,
    # A verification code sent to an email address during self-service
    # sign-up. Not an identity check -- it proves somebody reached that mailbox
    # once, which is enough to make creating workspaces in bulk tedious and is
    # exactly why the workspace it produces is still a demonstration.
    #
    # The code is stored as a hash, like every other code in this schema, and
    # `attempts` is what keeps six digits from being guessable: past the
    # ceiling the row is dead and a new code must be requested. One row per
    # address, so asking again replaces the last code rather than adding a
    # second valid one.
    """
    CREATE TABLE IF NOT EXISTS signup_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        ip_address TEXT
    )
    """,
    # A code the operator issues that turns a demo workspace into a real one.
    #
    # The code itself is never stored. `code_hash` is the same pattern as
    # `auth_sessions.token_hash` and `password_reset_tokens`: the operator sees
    # the code once, when it is minted, and a leaked control database yields
    # nothing anyone can redeem. `used_at` and `used_by_company_id` are what
    # make it single-use, and they are set in the same transaction that clears
    # the company's demo flag so a code cannot be spent twice by two requests
    # arriving together.
    #
    # `plan_id` is the plan the workspace lands on. Nullable: a code may simply
    # lift the demo restriction and leave the plan to be chosen later.
    """
    CREATE TABLE IF NOT EXISTS activation_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code_hash TEXT NOT NULL UNIQUE,
        plan_id INTEGER,
        note TEXT,
        created_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        used_at TEXT,
        used_by_company_id INTEGER,
        FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE SET NULL
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


# Columns added to a control table after its first release. CREATE TABLE IF NOT
# EXISTS never adds a column to a table that already exists, so an installation
# from an earlier release would be missing them and fail at query time.
CONTROL_COLUMNS: dict[str, dict[str, str]] = {
    "companies": {
        # A workspace anyone can create from the sign-up screen, carrying
        # sample data so the platform has something to show, and forbidden from
        # reaching a real customer until an activation code is redeemed.
        #
        # Defaulting to 0 is what keeps this additive: every company that
        # existed before this column was added is a real one, which is true.
        "is_demo": "INTEGER NOT NULL DEFAULT 0",
        # When a code was redeemed. Kept after `is_demo` goes to 0 because
        # "this workspace started as a trial on 3 March" is a fact the operator
        # wants later and cannot reconstruct from a flag that is now false.
        "activated_at": "TEXT",
    },
    # The company's design tokens (colour, type, shape, layout) for the theme
    # the interface renders with. Additive: a company with no theme published
    # falls back to the platform defaults, so an existing install looks exactly
    # as it did before anyone opens Theme Studio.
    "company_platform_config": {
        "theme_json": "TEXT NOT NULL DEFAULT '{}'",
    },
    "auth_sessions": {
        "scope": "TEXT NOT NULL DEFAULT 'company'",
    },
    "channel_accounts": {
        # The department this account feeds by default: a company that runs
        # three Instagram accounts can point each one at a different section,
        # and a message arriving on it starts there instead of waiting for the
        # model to guess.
        #
        # No foreign key, deliberately. `business_departments` lives inside the
        # company's own encrypted database and SQLite cannot enforce a key
        # across files — the same reason every other cross-file column here
        # carries no constraint. Ownership is checked in
        # `channel_account_service` before the value is written.
        #
        # Nullable because routing by channel is optional: a company may want
        # every account to fall through to the customer's own choice.
        "department_id": "INTEGER",
    },
    "users": {
        # When the password was last set. Shown on the user's own record so an
        # administrator can see an account still on the password it was created
        # with, which is the one most likely to be shared or reused.
        "password_changed_at": "TEXT",
        # Set when an administrator forces a reset. While it is on, the session
        # is minted but every route except changing the password refuses — the
        # enforcement is server-side, not a message the interface could skip.
        "must_change_password": "INTEGER NOT NULL DEFAULT 0",
        # When the account stops accepting sign-ins. An explicit column rather
        # than a count derived from `login_attempts`, because unlocking then
        # means clearing a field instead of deleting rows — and the old
        # `clear_login_attempts` deleted by email only, so it could not clear
        # an address-side block at all.
        "locked_until": "TEXT",
        "locked_reason": "TEXT",
        # --- Two-factor authentication.
        #
        # The TOTP secret is sealed, never stored in the clear. It is a
        # password-equivalent: anyone holding it can generate this account's
        # codes for ever, so a leaked database would hand over the second
        # factor along with the first — which is the same as having neither.
        #
        # Sealed under the platform master key rather than a company key: a
        # Super Admin belongs to no company, and their account is the one this
        # protects most.
        "totp_secret_sealed": "TEXT",
        # Enrolment is two steps. A secret is issued and stored, then confirmed
        # by the user typing a code it produced. Only after that does
        # `totp_enabled` go on. Turning it on at issue time would lock out
        # anybody who scanned the QR into an app that failed to save it.
        "totp_enabled": "INTEGER NOT NULL DEFAULT 0",
        "totp_confirmed_at": "TEXT",
        # Recovery codes, hashed. Shown once at enrolment and never again —
        # storing them readable would make them a second copy of the second
        # factor. Same reasoning as `auth_sessions.token_hash`.
        "totp_recovery_hashes": "TEXT",
        # The last TOTP time-step this account accepted. A code is only valid
        # for its ~90s window, but RFC 6238 requires a validated code be
        # single-use: without this, an observed code could be replayed for the
        # rest of that window. Verification refuses any step not strictly newer
        # than this one and claims the new step atomically.
        "totp_last_step": "INTEGER",
    },
}


CONTROL_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_companies_workspace ON companies(workspace_id)",
    "CREATE INDEX IF NOT EXISTS idx_branches_company ON branches(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_users_user ON company_users(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_users_company ON company_users(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_scope ON auth_sessions(scope, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_password_resets_hash ON password_reset_tokens(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_reset_tokens(user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_company ON channel_accounts(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_department ON channel_accounts(company_id, department_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_page ON channel_accounts(page_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_phone ON channel_accounts(phone_number_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_accounts_instagram ON channel_accounts(instagram_business_id)",
    "CREATE INDEX IF NOT EXISTS idx_subscriptions_company ON subscriptions(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_plan_overrides_company ON company_plan_overrides(company_id)",
    # The billing read is always "this company, this month", and the console's
    # is "this month, every company" — so period leads.
    "CREATE INDEX IF NOT EXISTS idx_usage_company_period ON usage_records(company_id, period, metric)",
    "CREATE INDEX IF NOT EXISTS idx_usage_period ON usage_records(period, metric)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_company ON audit_log(company_id, created_at)",
    # The read path asks one question -- "the published theme for this scope" --
    # on every workspace configuration request, so the index carries the whole
    # of it rather than the scope alone.
    """
    CREATE INDEX IF NOT EXISTS idx_ui_themes_scope
    ON ui_themes(scope_type, scope_id, status, version)
    """,
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
    # Back, and only because there is finally something for it to guard.
    #
    # It was retired for the right reason: it described "change the plan and
    # billing details", no endpoint checked it, and a company could not change
    # its own plan by design — so the Roles screen offered an owner a switch
    # that restricted nothing. A permission that decides nothing is the same
    # defect as a setting that saves and decides nothing, and worse, because it
    # tells an owner they have limited somebody.
    #
    # `POST /api/activation/redeem` is that endpoint. Redeeming a code takes a
    # workspace out of demonstration and puts it on a plan, which is exactly
    # what this permission always claimed to cover.
    #
    # Granted to no default role. The owner holds everything in code, and
    # whether a manager may spend the company's activation code is a decision
    # for the owner to take on the Roles screen rather than one this file
    # should take for every company.
    ("subscriptions.manage", "Manage Subscription", "Redeem an activation code and change the plan."),
    ("tasks.view", "View Tasks", "See the team's tasks and follow-ups."),
    ("tasks.manage", "Manage Tasks", "Create, assign, edit and close tasks."),
    ("catalogue.view", "View Catalogue", "Browse the product catalogue."),
    ("catalogue.manage", "Manage Catalogue", "Add, edit and remove products and categories."),
    ("comments.view", "View Comments", "Read comments left on the company's posts."),
    ("comments.reply", "Reply to Comments", "Publish replies to post comments."),
    ("scheduler.view", "View Scheduled Posts", "See the publishing calendar."),
    ("scheduler.manage", "Manage Scheduled Posts", "Create, approve and cancel scheduled posts."),
    ("appointments.view", "View Appointments", "See the appointment calendar."),
    ("appointments.manage", "Manage Appointments", "Book, reschedule and cancel appointments."),
    ("team_chat.use", "Use Team Chat", "Read and post in internal team channels."),
    # Reading the call history rides on `conversations.view`/`conversations.reply`
    # — logging a call is answering a customer by another route. This one is
    # only for the live line, because making the company's number ring a
    # customer spends money and speaks in the company's name, which is a
    # narrower thing to hand out than the inbox.
    ("dialer.use", "Use the Dialer", "Place, transfer and end live phone calls."),
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
            "tasks.view",
            "tasks.manage",
            "catalogue.view",
            "catalogue.manage",
            "comments.view",
            "comments.reply",
            "scheduler.view",
            "scheduler.manage",
            "appointments.view",
            "appointments.manage",
            "team_chat.use",
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
            "tasks.view",
            "tasks.manage",
            "catalogue.view",
            "comments.view",
            "comments.reply",
            "appointments.view",
            "appointments.manage",
            "team_chat.use",
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
            "tasks.view",
            "catalogue.view",
            "comments.view",
            "scheduler.view",
            "appointments.view",
        ),
    ),
)


DEFAULT_PLANS: tuple[tuple, ...] = (
    ("Starter", "starter", 19, 2, 1, 1000, 100, 0, 0, 0, 0),
    ("Business", "business", 49, 10, 5, 5000, 1000, 1, 1, 1, 1),
    ("Enterprise", "enterprise", 0, 100, 50, 100000, 100000, 1, 1, 1, 1),
)
