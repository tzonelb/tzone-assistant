-- Monetary columns are INTEGER minor units throughout. Never REAL.

CREATE TABLE IF NOT EXISTS accounts (
    id         TEXT PRIMARY KEY,
    code       TEXT NOT NULL,
    name_en    TEXT NOT NULL,
    name_ar    TEXT NOT NULL DEFAULT '',
    type       TEXT NOT NULL CHECK (type IN ('asset','liability','equity','income','expense')),
    parent_id  TEXT,
    is_group   INTEGER NOT NULL DEFAULT 0,   -- group accounts cannot be posted to
    is_cash    INTEGER NOT NULL DEFAULT 0,
    currency   TEXT,                          -- when set, only this currency may be posted
    is_active  INTEGER NOT NULL DEFAULT 1,
    rev        INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    change_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_accounts_seq  ON accounts(change_seq);
CREATE INDEX IF NOT EXISTS idx_accounts_code ON accounts(code);

CREATE TABLE IF NOT EXISTS journal_entries (
    id          TEXT PRIMARY KEY,
    entry_no    TEXT NOT NULL,
    date        TEXT NOT NULL,                      -- YYYY-MM-DD
    memo        TEXT NOT NULL DEFAULT '',
    currency    TEXT NOT NULL,
    fx_rate     INTEGER NOT NULL DEFAULT 1000000,   -- micro-units: 1000000 == 1.0
    status      TEXT NOT NULL CHECK (status IN ('draft','posted','void')),
    source_kind TEXT NOT NULL DEFAULT 'manual',     -- which module produced it
    source_id   TEXT,                               -- the document it came from
    reverses_id TEXT,                               -- set on the reversing entry of a void
    created_by  TEXT NOT NULL DEFAULT '',
    rev        INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    change_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_seq    ON journal_entries(change_seq);
CREATE INDEX IF NOT EXISTS idx_entries_date   ON journal_entries(date);
CREATE INDEX IF NOT EXISTS idx_entries_source ON journal_entries(source_kind, source_id);

-- Lines belong to their entry and are replaced as a set on every write.
CREATE TABLE IF NOT EXISTS journal_lines (
    entry_id    TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    account_id  TEXT NOT NULL,
    partner_id  TEXT,
    description TEXT NOT NULL DEFAULT '',
    debit       INTEGER NOT NULL DEFAULT 0,
    credit      INTEGER NOT NULL DEFAULT 0,
    base_debit  INTEGER NOT NULL DEFAULT 0,
    base_credit INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entry_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_lines_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_lines_partner ON journal_lines(partner_id);
