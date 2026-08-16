"""Schema for a single company's encrypted database.

One file per company, encrypted with that company's own key. A company can only
ever be handed a connection to its own file, which is what makes cross-company
data access impossible rather than merely forbidden.

Two deliberate differences from the old shared schema:

* Foreign keys that pointed at `companies` or `users` are gone. Those tables
  live in the control-plane database and SQLite cannot enforce a key across
  files. The columns remain so application queries keep working; the values are
  resolved against the control plane in a second query.
* `company_id` is still stored on every row. It is redundant inside a file that
  belongs to one company, but it makes a misrouted write detectable instead of
  silent, and it keeps existing queries unchanged.

This module is the single source of truth for these tables.
"""

from __future__ import annotations


TENANT_SCHEMA_VERSION = 1


TENANT_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        customer_id INTEGER,
        status TEXT NOT NULL DEFAULT 'ai_handling',
        workflow_state TEXT NOT NULL DEFAULT 'ai_active',
        ai_enabled INTEGER NOT NULL DEFAULT 1,
        handled_by_ai INTEGER NOT NULL DEFAULT 1,
        priority TEXT NOT NULL DEFAULT 'normal',
        department TEXT DEFAULT 'Unassigned',
        topic TEXT,
        language TEXT,
        assigned_user_id INTEGER,
        needs_human INTEGER NOT NULL DEFAULT 0,
        unread_count INTEGER NOT NULL DEFAULT 0,
        takeover_expires_at TEXT,
        human_last_reply_at TEXT,
        last_message_at TEXT,
        branch_id INTEGER,
        channel_account_id INTEGER,
        customer_alias TEXT,
        official_customer_name TEXT,
        customer_profile_picture TEXT,
        folder TEXT NOT NULL DEFAULT 'inbox',
        is_starred INTEGER NOT NULL DEFAULT 0,
        is_pinned INTEGER NOT NULL DEFAULT 0,
        tags_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, channel, external_user_id)
    )
    """,
    # Replaces the per-conversation .jsonl files. Holding messages here is what
    # makes listing the inbox a single indexed query instead of a full disk scan,
    # and it is what puts message bodies inside the company's encryption boundary.
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        conversation_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        sender_type TEXT NOT NULL DEFAULT 'customer',
        sender_user_id INTEGER,
        body TEXT NOT NULL DEFAULT '',
        provider_message_id TEXT,
        source TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        company_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        event_type TEXT NOT NULL,
        event_data_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        company_id INTEGER NOT NULL,
        author_user_id INTEGER,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        color TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, normalized_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        display_name TEXT,
        internal_name TEXT,
        profile_picture TEXT,
        phone TEXT,
        email TEXT,
        language TEXT,
        country TEXT,
        timezone TEXT,
        notes TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customer_identities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        username TEXT,
        display_name TEXT,
        profile_picture TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, channel, external_user_id),
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customer_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        action TEXT NOT NULL,
        data_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        recipient_user_id INTEGER,
        notification_type TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT,
        channel TEXT,
        external_user_id TEXT,
        conversation_id INTEGER,
        actor_user_id INTEGER,
        severity TEXT NOT NULL DEFAULT 'info',
        data_json TEXT NOT NULL DEFAULT '{}',
        dedupe_key TEXT,
        is_read INTEGER NOT NULL DEFAULT 0,
        read_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS diagnostic_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        channel TEXT,
        external_user_id TEXT,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'info',
        status TEXT,
        duration_ms INTEGER,
        data_json TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        section TEXT NOT NULL,
        settings_json TEXT NOT NULL DEFAULT '{}',
        updated_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, section)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_setting_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        section TEXT NOT NULL,
        actor_user_id INTEGER,
        old_value_json TEXT,
        new_value_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS super_admin_setting_overrides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        section TEXT NOT NULL,
        setting_key TEXT NOT NULL,
        value_json TEXT,
        is_locked INTEGER NOT NULL DEFAULT 0,
        updated_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, section, setting_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        department TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        UNIQUE(company_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        category_id INTEGER,
        external_id TEXT,
        title TEXT NOT NULL,
        content_ar TEXT,
        content_en TEXT,
        department TEXT,
        keywords TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(category_id) REFERENCES knowledge_categories(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        conversation_id INTEGER,
        platform TEXT,
        user_id TEXT,
        language TEXT,
        department TEXT,
        iptv_username TEXT,
        device TEXT,
        os TEXT,
        app TEXT,
        problem TEXT,
        assigned_user_id INTEGER,
        status TEXT NOT NULL DEFAULT 'open',
        priority TEXT NOT NULL DEFAULT 'normal',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # Durable replacement for the in-process reply buffer. Persisting the queue
    # is what stops a restart or a deploy from silently dropping customer
    # messages that were waiting to be answered.
    """
    CREATE TABLE IF NOT EXISTS pending_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        messages_json TEXT NOT NULL DEFAULT '[]',
        deliver_after TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        locked_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, channel, external_user_id)
    )
    """,
)


TENANT_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_folder ON conversations(folder, last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_assigned ON conversations(assigned_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_expiry ON conversations(takeover_expires_at) WHERE takeover_expires_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_lookup ON messages(channel, external_user_id, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_provider ON messages(provider_message_id) WHERE provider_message_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_events_conversation ON conversation_events(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notes_conversation ON conversation_notes(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_customers_seen ON customers(last_seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_identities_customer ON customer_identities(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_user_id, is_read)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key) WHERE dedupe_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_diagnostics_created ON diagnostic_events(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diagnostics_type ON diagnostic_events(event_type, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_department ON knowledge_items(department, status)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pending_due ON pending_replies(deliver_after)",
)


# Seeded into every new company database so the assistant has working defaults
# from the first message, rather than depending on a settings screen being
# visited first.
DEFAULT_SETTINGS: dict[str, dict] = {
    "ai_behavior": {
        "enabled": True,
        "collect_message_delay_seconds": 20,
        "return_to_ai_timeout_minutes": 5,
        "reply_language": "auto",
        "escalate_on_low_confidence": True,
    },
    "working_hours": {
        "enabled": False,
        "timezone": "Asia/Beirut",
        "days": {
            "monday": {"open": "09:00", "close": "18:00", "closed": False},
            "tuesday": {"open": "09:00", "close": "18:00", "closed": False},
            "wednesday": {"open": "09:00", "close": "18:00", "closed": False},
            "thursday": {"open": "09:00", "close": "18:00", "closed": False},
            "friday": {"open": "09:00", "close": "18:00", "closed": False},
            "saturday": {"open": "09:00", "close": "14:00", "closed": False},
            "sunday": {"open": "09:00", "close": "18:00", "closed": True},
        },
    },
    "notifications": {
        "notify_on_customer_message": True,
        "notify_on_handover": True,
        "notify_on_ai_error": True,
    },
}
