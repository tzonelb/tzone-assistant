CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'accountant',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

-- One row per terminal. `device_code` is the short prefix that makes offline document
-- numbering collision-free (docs/OFFLINE_SYNC.md §6).
CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    label       TEXT NOT NULL DEFAULT '',
    device_code TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    last_seen   TEXT
);

-- Company-wide settings as a single replicated document with id = 'company'.
-- Modules contribute their own keys through registry.add_settings_defaults().
CREATE TABLE IF NOT EXISTS settings (
    id         TEXT PRIMARY KEY,
    payload    TEXT NOT NULL DEFAULT '{}',
    rev        INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    change_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_settings_seq ON settings(change_seq);
