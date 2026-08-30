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

from typing import Any


TENANT_SCHEMA_VERSION = 5


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
        -- `department_id` is the identity link: company → channel account →
        -- department → employee. `department` is the same department's code,
        -- kept alongside it so the inbox filter, the export and every existing
        -- query keep working unchanged. The two are always written together by
        -- `conversation_control_service`; dropping the text column is a
        -- separate change from adding this one.
        department TEXT DEFAULT 'Unassigned',
        department_id INTEGER REFERENCES business_departments(id) ON DELETE SET NULL,
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
        -- Soft delete. `create_tag` reactivates a retired tag on conflict
        -- rather than failing the unique constraint, and `list_tags` shows
        -- only active ones.
        --
        -- This column and `created_by_user_id` below have both been read and
        -- written by the tag code since the feature was written, and neither
        -- was ever in the table. Listing tags raised "no such column: status"
        -- and creating one raised "no such column: created_by_user_id", so the
        -- inbox's tag list had never once loaded and no tag had ever been
        -- created — for any company, since the day it shipped.
        status TEXT NOT NULL DEFAULT 'active',
        created_by_user_id INTEGER,
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
        lifecycle_stage TEXT NOT NULL DEFAULT 'lead',
        tags_json TEXT NOT NULL DEFAULT '[]',
        assigned_user_id INTEGER,
        custom_fields_json TEXT NOT NULL DEFAULT '{}',
        documents_json TEXT NOT NULL DEFAULT '[]',
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
    CREATE TABLE IF NOT EXISTS customer_segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        filters_json TEXT NOT NULL DEFAULT '{}',
        created_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, name)
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
        title TEXT,
        task_type TEXT NOT NULL DEFAULT 'support',
        due_date TEXT,
        created_by_user_id INTEGER,
        closed_at TEXT,
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
    """
    CREATE TABLE IF NOT EXISTS ticket_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        ticket_id INTEGER NOT NULL,
        author_user_id INTEGER,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
    )
    """,
    # --- Catalogue -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS product_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        parent_id INTEGER,
        sort_order INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, name)
    )
    """,
    # The assistant treats these rows as verified facts. Anything it states
    # about price or stock must come from here rather than from the model, which
    # is what the price guardrail in core/ai_router.py enforces.
    """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        category_id INTEGER,
        sku TEXT,
        name TEXT NOT NULL,
        name_en TEXT,
        description TEXT,
        brand TEXT,
        price REAL,
        sale_price REAL,
        currency TEXT NOT NULL DEFAULT 'USD',
        stock_quantity INTEGER,
        in_stock INTEGER NOT NULL DEFAULT 1,
        image_url TEXT,
        attributes_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, sku),
        FOREIGN KEY(category_id) REFERENCES product_categories(id) ON DELETE SET NULL
    )
    """,
    # --- Assistant training --------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS bot_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel_account_id INTEGER,
        name TEXT NOT NULL,
        default_language TEXT NOT NULL DEFAULT 'ar',
        tone TEXT,
        system_prompt TEXT,
        welcome_enabled INTEGER NOT NULL DEFAULT 1,
        welcome_message_ar TEXT,
        welcome_message_en TEXT,
        examples_json TEXT NOT NULL DEFAULT '[]',
        ai_enabled INTEGER NOT NULL DEFAULT 1,
        ai_model TEXT,
        memory_enabled INTEGER NOT NULL DEFAULT 1,
        human_handover_enabled INTEGER NOT NULL DEFAULT 1,
        is_default INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # The sections this company offers its customers: the menu the assistant
    # shows, the quick-reply buttons it renders, and the department list handed
    # to the model. These used to come from `config/business_modules.json` — one
    # file naming one company's departments, served to every company's
    # customers, so a clinic's visitor was offered an IPTV menu. There is no
    # default set: a company that has defined none gets no menu rather than
    # somebody else's.
    """
    CREATE TABLE IF NOT EXISTS business_departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        name_ar TEXT,
        name_en TEXT,
        button_ar TEXT,
        button_en TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, code)
    )
    """,
    # The manager's private training chat with the assistant. It is not a
    # customer conversation and must never be mixed into `messages`: nothing
    # here was said to or by a customer, and an export or a retention sweep
    # that treated it as one would be wrong in both directions.
    #
    # `instruction_saved` records that this turn changed the assistant's
    # standing instructions, so the transcript still explains why the bot's
    # behaviour changed after the fact — the instruction itself lives in the
    # profile's `system_prompt`, which is what the prompt builder reads.
    """
    CREATE TABLE IF NOT EXISTS ai_teaching_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        instruction_saved INTEGER NOT NULL DEFAULT 0,
        instruction_text TEXT,
        actor_user_id INTEGER,
        created_at TEXT NOT NULL
    )
    """,
    # --- Post comments --------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS post_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        provider_comment_id TEXT NOT NULL,
        parent_comment_id TEXT,
        post_id TEXT,
        post_caption TEXT,
        author_external_id TEXT,
        author_name TEXT,
        customer_id INTEGER,
        message TEXT NOT NULL DEFAULT '',
        permalink TEXT,
        is_hidden INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
        replied_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, provider_comment_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comment_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        comment_id INTEGER NOT NULL,
        provider_reply_id TEXT,
        author_user_id INTEGER,
        sender_type TEXT NOT NULL DEFAULT 'employee',
        body TEXT NOT NULL,
        send_status TEXT NOT NULL DEFAULT 'sent',
        error TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(comment_id) REFERENCES post_comments(id) ON DELETE CASCADE
    )
    """,
    # --- Scheduled publishing -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS scheduled_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        channel_account_id INTEGER,
        body TEXT NOT NULL,
        media_url TEXT,
        link_url TEXT,
        scheduled_for TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        approved_by_user_id INTEGER,
        approved_at TEXT,
        created_by_user_id INTEGER,
        published_at TEXT,
        provider_post_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        locked_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # --- Appointments ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS availability_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        staff_user_id INTEGER,
        weekday INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        slot_minutes INTEGER NOT NULL DEFAULT 30,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        customer_id INTEGER,
        conversation_id INTEGER,
        staff_user_id INTEGER,
        branch_id INTEGER,
        title TEXT NOT NULL,
        notes TEXT,
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'scheduled',
        created_by_user_id INTEGER,
        cancelled_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # --- Internal team chat ----------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS team_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        topic TEXT,
        is_private INTEGER NOT NULL DEFAULT 0,
        created_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_channel_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        last_read_at TEXT,
        joined_at TEXT NOT NULL,
        UNIQUE(channel_id, user_id),
        FOREIGN KEY(channel_id) REFERENCES team_channels(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        author_user_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        mentions_json TEXT NOT NULL DEFAULT '[]',
        linked_conversation_id INTEGER,
        edited_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(channel_id) REFERENCES team_channels(id) ON DELETE CASCADE
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
    """
    -- Everything that happened in this company's workspace, in one place.
    --
    -- Of seventeen modules, three wrote any audit at all and two of those had
    -- no endpoint to read it back — the trail existed and nobody could see it.
    -- An owner asking "who changed that price" had nowhere to look, and the
    -- answer is the kind that matters: the assistant quotes catalogue prices to
    -- customers as facts.
    --
    -- Inside the company's own encrypted database, not the control plane. This
    -- is the company's record of its own business, and the control plane is
    -- shared — the security mirror in `audit_log` carries only what an operator
    -- needs, never the detail.
    --
    -- `actor_label` is a snapshot of the display name, not a join. `users`
    -- lives in the control plane and SQLite cannot join across files: three
    -- existing queries do `LEFT JOIN users` inside a tenant connection, match
    -- nothing, and render every actor as "System". A snapshot also survives the
    -- employee leaving, which a join never would.
    --
    -- `kind` separates three things that need different retention: a change is
    -- kept, a read is high-volume and expires sooner, and a security event is
    -- also mirrored to the control plane.
    CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        kind TEXT NOT NULL DEFAULT 'change',
        category TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_user_id INTEGER,
        actor_label TEXT,
        target_type TEXT,
        target_id TEXT,
        summary TEXT,
        before_json TEXT,
        after_json TEXT,
        ip_address TEXT,
        severity TEXT NOT NULL DEFAULT 'info',
        created_at TEXT NOT NULL
    )
    """,
    """
    -- Canned replies an employee can drop into a conversation.
    --
    -- Company-owned text, so it lives in the company's own encrypted database
    -- rather than the control plane, and needs no company_id filter to be safe:
    -- the file is the tenant. `company_id` is carried anyway for the same
    -- reason every other tenant table carries it -- it makes an exported row
    -- self-describing and a restore into the wrong file obvious.
    --
    -- `department` is a plain string, empty for a reply that suits every
    -- section, because a reply written for one section is noise in another.
    CREATE TABLE IF NOT EXISTS saved_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        department TEXT NOT NULL DEFAULT '',
        created_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    -- A follow-up an employee set on one conversation: come back to this at a
    -- time, optionally sending a message when it arrives.
    --
    -- One live reminder per conversation, enforced by the unique key rather
    -- than by the caller: setting a second one replaces the first, which is
    -- what "remind me at" means to the person clicking it.
    CREATE TABLE IF NOT EXISTS conversation_reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        remind_at TEXT NOT NULL,
        note TEXT,
        auto_send INTEGER NOT NULL DEFAULT 0,
        message_text TEXT,
        created_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(channel, external_user_id)
    )
    """,
    """
    -- A one-to-many campaign: one message, sent once, to every contact the
    -- targeting resolves to. Ported from the design branch's `broadcasts`
    -- table (backend/services/broadcast_service.py::ensure_schema there),
    -- minus the foreign keys it declared onto `companies`, `users` and
    -- `customer_segments` -- the first two live in the control-plane database
    -- and SQLite cannot enforce a key across files, and this platform has no
    -- customer segments (see `segment_id` below).
    --
    -- `recipient_count` is a snapshot taken when the draft is created. The
    -- send always re-resolves recipients, so the two can disagree while a
    -- draft sits; `/recipient-count` recomputes it for display.
    --
    -- `send_lock_acquired_at` is the mutual-exclusion claim that stops two
    -- overlapping sends of the same broadcast. It is cleared when the send
    -- finishes, and a lock older than ten minutes is assumed abandoned by a
    -- crashed request.
    CREATE TABLE IF NOT EXISTS broadcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        message_text TEXT NOT NULL,
        channel TEXT NOT NULL,
        -- The three targeting columns the design branch's API carries. This
        -- platform's contacts have no segment, no lifecycle stage and no
        -- tags, so a broadcast that names one is refused rather than
        -- silently widened to everybody -- see `broadcast_service`. The
        -- columns stay so the stored row keeps saying what was asked for if
        -- those dimensions ever arrive.
        segment_id INTEGER,
        lifecycle_stage TEXT,
        tag TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        recipient_count INTEGER NOT NULL DEFAULT 0,
        sent_count INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0,
        -- A pasted number list, stored as the normalized numbers it resolved
        -- to. Present exactly when this broadcast targets numbers rather
        -- than contacts.
        raw_numbers_json TEXT,
        media_url TEXT,
        media_type TEXT,
        send_lock_acquired_at TEXT,
        created_by_user_id INTEGER,
        created_at TEXT NOT NULL,
        sent_at TEXT
    )
    """,
    """
    -- One row per contact a broadcast was actually sent to, written as each
    -- send returns so an interrupted run can be resumed without sending the
    -- same person the same campaign twice.
    CREATE TABLE IF NOT EXISTS broadcast_recipients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        broadcast_id INTEGER NOT NULL,
        customer_id INTEGER,
        channel TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        provider_message_id TEXT,
        send_status TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(broadcast_id) REFERENCES broadcasts(id) ON DELETE CASCADE
    )
    """,
)


# Columns added to a table after its first release. `CREATE TABLE IF NOT EXISTS`
# never adds a column to a table that already exists, so a company provisioned
# before a release would be missing them. `DatabaseManager.upgrade_tenant`
# applies these; services must never patch their own tables at runtime.
TENANT_COLUMNS: dict[str, dict[str, str]] = {
    "conversations": {
        # SQLite allows a REFERENCES clause on an added column only when the
        # default is NULL, which it is. `ON DELETE SET NULL` rather than the
        # default RESTRICT: a company retiring a section must still be able to
        # delete it, and a conversation that outlives its department is
        # unassigned, not undeletable.
        "department_id": (
            "INTEGER REFERENCES business_departments(id) ON DELETE SET NULL"
        ),
    },
    # Added after the tag feature shipped without it. Existing companies have a
    # `conversation_tags` table with no `status`, so the column has to arrive
    # through the upgrade path rather than through CREATE TABLE.
    "conversation_tags": {
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "created_by_user_id": "INTEGER",
    },
    "tickets": {
        "title": "TEXT",
        "task_type": "TEXT NOT NULL DEFAULT 'support'",
        "due_date": "TEXT",
        "created_by_user_id": "INTEGER",
        "closed_at": "TEXT",
    },
    # An internal note can name the colleagues it is for. The ids are this
    # company's own employees, checked against the control plane before they
    # are written (`conversation_control_service.add_note`), so a note can
    # never carry an id belonging to somebody else's company.
    #
    # A JSON list rather than a join table: it is only ever read with the note
    # that holds it and never queried across notes, and the default is the
    # empty list, so every note written before this column existed reads back
    # correctly the moment it arrives.
    "conversation_notes": {
        "mentioned_user_ids_json": "TEXT NOT NULL DEFAULT '[]'",
    },
    # A team channel is one of three things now. `channel` is the named,
    # joinable discussion team chat has always had; `dm` is a two-person
    # conversation and `group` a private one with a member list. All three are
    # the same row under the same membership and privacy rules — the kind only
    # decides how a client titles it, which is why this is a column and not a
    # second set of tables.
    #
    # `display_name` keeps the name as a person typed it. `name` is normalised
    # for the uniqueness constraint (lowercased, spaces to hyphens), which is
    # the right key and the wrong label.
    "team_channels": {
        "kind": "TEXT NOT NULL DEFAULT 'channel'",
        "display_name": "TEXT",
    },
    # One file per message, uploaded through `/api/media/upload` exactly as the
    # customer composer uploads one. The URL names a file this workspace has
    # already stored; nothing here accepts an arbitrary address.
    "team_messages": {
        "attachment_url": "TEXT",
        "attachment_type": "TEXT",
        "attachment_filename": "TEXT",
    },
    # The CRM fields the Contacts screen edits: a lifecycle stage, free-form
    # tags, an owning employee, and the two free-form stores (custom fields and
    # documents) a contact file needs. A company provisioned before this
    # release has a `customers` table without them, so they arrive here rather
    # than through CREATE TABLE.
    #
    # `assigned_user_id` carries no REFERENCES clause: `users` lives in the
    # control-plane database and SQLite cannot key across files. It is resolved
    # through `auth_service.user_display_names`, the same way every other
    # user id in a tenant table is.
    "customers": {
        "lifecycle_stage": "TEXT NOT NULL DEFAULT 'lead'",
        "tags_json": "TEXT NOT NULL DEFAULT '[]'",
        "assigned_user_id": "INTEGER",
        "custom_fields_json": "TEXT NOT NULL DEFAULT '{}'",
        "documents_json": "TEXT NOT NULL DEFAULT '[]'",
    },
}


TENANT_INDEXES: tuple[str, ...] = (
    # Saved replies are listed for the whole company or filtered to one
    # section, and reminders are swept by the time they come due.
    "CREATE INDEX IF NOT EXISTS idx_saved_replies_department ON saved_replies(department, title)",
    "CREATE INDEX IF NOT EXISTS idx_reminders_due ON conversation_reminders(remind_at)",
    # The log is read newest-first, filtered by category or by actor, and swept
    # by kind for retention. Each index matches one of those three readings.
    "CREATE INDEX IF NOT EXISTS idx_activity_recent ON activity_log(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_activity_category ON activity_log(category, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_activity_actor ON activity_log(actor_user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_activity_kind ON activity_log(kind, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_folder ON conversations(folder, last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_assigned ON conversations(assigned_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_department ON conversations(department_id, last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_account ON conversations(channel_account_id)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_expiry ON conversations(takeover_expires_at) WHERE takeover_expires_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_lookup ON messages(channel, external_user_id, created_at)",
    # Dedup is per conversation, not per company. Telegram message ids are
    # unique per chat, not per bot, so two customers both open at message_id 1;
    # a company-wide unique index silently rejected the second as a duplicate.
    # Drop the old single-column index and key uniqueness on the conversation
    # too, which still catches a genuine provider retry (same id, same chat).
    "DROP INDEX IF EXISTS idx_messages_provider",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_provider ON messages(channel, external_user_id, provider_message_id) WHERE provider_message_id IS NOT NULL",
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
    "CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets(assigned_user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_due ON tickets(due_date) WHERE due_date IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_ticket_comments ON ticket_comments(ticket_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_pending_due ON pending_replies(deliver_after)",
    "CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)",
    "CREATE INDEX IF NOT EXISTS idx_products_stock ON products(in_stock, status)",
    "CREATE INDEX IF NOT EXISTS idx_bot_profiles_default ON bot_profiles(is_default, status)",
    "CREATE INDEX IF NOT EXISTS idx_departments_order ON business_departments(enabled, sort_order, id)",
    "CREATE INDEX IF NOT EXISTS idx_comments_status ON post_comments(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_comments_post ON post_comments(post_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_comment_replies ON comment_replies(comment_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_posts(status, scheduled_for)",
    "CREATE INDEX IF NOT EXISTS idx_appointments_window ON appointments(starts_at, ends_at)",
    "CREATE INDEX IF NOT EXISTS idx_appointments_staff ON appointments(staff_user_id, starts_at)",
    "CREATE INDEX IF NOT EXISTS idx_appointments_customer ON appointments(customer_id, starts_at)",
    "CREATE INDEX IF NOT EXISTS idx_availability_staff ON availability_rules(staff_user_id, weekday)",
    "CREATE INDEX IF NOT EXISTS idx_team_messages ON team_messages(channel_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_team_members ON team_channel_members(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_team_channels_kind ON team_channels(kind, updated_at DESC)",
    # The campaign list is read newest-first, and a report reads one campaign's
    # recipients in insertion order.
    "CREATE INDEX IF NOT EXISTS idx_broadcasts_recent ON broadcasts(created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_broadcast_recipients ON broadcast_recipients(broadcast_id, id)",
)


# Seeded into every new company database so the assistant has working defaults
# from the first message, rather than depending on a settings screen being
# visited first.
# Sections whose values are not this database's to hold. `company_profile`
# comes from the company's control-plane row and `modules` from the operator's
# switches, both resolved per request in `company_settings_service._defaults_for`.
#
# They are in the catalogue below for their keys — so the section exists, so
# `get_all` enumerates it, and so a write naming one of their keys is
# recognised. They must not be *seeded*, because a stored row wins over a
# resolved default: seeding `company_profile` writes `company_name: ""` into
# every new company and the settings screen then opens showing an empty name
# instead of the company's own.
RESOLVED_SECTIONS: frozenset[str] = frozenset({"company_profile", "modules"})


DEFAULT_SETTINGS: dict[str, Any] = {
    # The one catalogue. There used to be two: this one, seeded into every
    # company's database at provisioning, and a different one in
    # `company_settings_service` that was actually served. They had drifted
    # completely apart — this file seeded `working_hours`, `reply_language`,
    # `escalate_on_low_confidence` and three `notify_on_*` keys, and the
    # service served none of them and merged its own set over the top.
    #
    # That is why those keys read as "stored and never used". They were not
    # merely unread: there was no path to them at all. A company's database
    # held them, `get_section` never returned them, and `update_section`
    # dropped any write naming them.
    #
    # The service now imports this. Seeding and serving cannot disagree again
    # without one of them failing to import.
    #
    # `company_profile` and `modules` are listed for their keys only — the real
    # values are resolved per company in `_defaults_for`, from the control
    # plane and from the operator's module switches.
    "company_profile": {
        "company_name": "",
        "timezone": "Asia/Beirut",
        "default_language": "ar",
        "country": "",
        "currency": "USD",
    },
    "ai_behavior": {
        "enabled": True,
        "mode": "ai_first",
        "collect_message_delay_seconds": 20,
        "return_to_ai_timeout_minutes": 5,
        "reply_access_mode": "take_required",
        "auto_read_mode": "assigned_owner_only",
        "auto_release_to_ai": True,
        # Which language to answer in before the customer has asked for one.
        # `auto` detects from the message, which is what every company had.
        "reply_language": "auto",
        # `welcome_immediate` and `reply_only_when_customer_stops_typing` were
        # here and read by nothing. Both already have an owner elsewhere:
        # `welcome_enabled` and `welcome_mode` in the reply policy decide the
        # first, per channel, and are enforced; `collect_message_delay_seconds`
        # two lines up is the second — it is the buffer that waits for a
        # customer to stop typing. Two switches for one decision leaves an
        # owner setting the one they found and unable to tell why nothing
        # changed.
    },
    # When this company's team is reachable. Off by default, so a company that
    # has not set hours behaves exactly as it did before hours existed.
    #
    # These change escalation, not the assistant: the bot keeps answering, and
    # a hand-over outside hours tells the customer when somebody will be there
    # instead of implying somebody is there now.
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
    # Whether each kind of bell entry is written at all. Enforced in
    # `notification_service.create`, keyed by notification type, so a call site
    # added later cannot forget to ask.
    #
    # `in_app_popup`, `desktop`, `sound`, `ai_replied` and `employee_replied`
    # used to be here and were read by nothing on the server. They are browser
    # preferences, and the browser already keeps its own — per user, in
    # `frontend/src/utils/notificationPreferences.js`, which is the right place
    # for them: whether a sound plays is one person's choice on one machine,
    # not a company-wide setting. The server copies were a second store that
    # nothing read and nothing wrote.
    #
    # What is left is what the server actually decides: whether the row exists.
    "notifications": {
        "new_customer_message": True,
        # Two events that happen, are recorded, and had nobody to tell: a
        # colleague taking a conversation off the assistant, and the assistant
        # failing to answer a customer at all.
        "handover": True,
        "ai_error": True,
    },
    "reply_flow": {
        "steps": [
            "welcome",
            "language_detection",
            "intent_detection",
            "knowledge_lookup",
            "answer",
            "escalation",
        ]
    },
    # How this company answers, per channel. Sparse on purpose and empty by
    # default: an absent key inherits the platform's shipped value in
    # `config/response_policy.json`, and an absent channel inherits this
    # company's default. Seeding the shipped values here would freeze a copy of
    # them and make "clear this override" impossible to tell from "set it to
    # the same thing". `backend/services/reply_policy_service.py` owns the
    # shape, the validation and the resolution.
    "reply_policy": {
        "default": {},
        "channels": {},
    },
    # Read-only. Module visibility is the platform administrator's decision,
    # enforced by `backend/services/module_access`; this section reports it and
    # refuses writes.
    "modules": {},
}
