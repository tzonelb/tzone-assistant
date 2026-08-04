"""Regression test for the legacy-database self-heal path.

Real failure seen on a dev machine (2026-08-04): its database/tzone.db
was created by an early, pre-release iteration of the Broadcast feature
whose broadcast_recipients table had no `conversation_id` column.
CREATE TABLE IF NOT EXISTS skips recreation for existing tables, so
db.create_tables() crashed on every boot at
CREATE UNIQUE INDEX ... ON broadcast_recipients(broadcast_id,
conversation_id) with "sqlite3.OperationalError: no such column:
conversation_id" -- making the app unbootable on that machine.

This proves _heal_legacy_broadcast_recipients_table renames the
old-shaped table aside (preserving its rows as a backup) so the correct
table is created fresh and boot succeeds.

Run with: python3 -m pytest tests/test_legacy_db_self_heal.py -v
"""
import os
import sqlite3
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def legacy_db_path():
    """A throwaway SQLite file pre-seeded with the OLD broadcast_recipients
    shape (no conversation_id / external_user_id), exactly like the
    affected dev machine."""
    tmp_path = tempfile.mktemp(suffix=".db")

    conn = sqlite3.connect(tmp_path)
    conn.execute(
        """
        CREATE TABLE broadcast_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_id INTEGER NOT NULL,
            phone_number TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO broadcast_recipients (broadcast_id, phone_number) "
        "VALUES (1, '+96170000000')"
    )
    conn.commit()
    conn.close()

    yield tmp_path

    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_create_tables_self_heals_old_broadcast_recipients(legacy_db_path):
    from pathlib import Path
    from database.database import db

    original_path = db.db_path
    db.db_path = Path(legacy_db_path)

    try:
        # Before the heal existed, this exact call raised
        # sqlite3.OperationalError: no such column: conversation_id.
        db.create_tables()

        with db.connect() as conn:
            # The recreated table has the current shape...
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(broadcast_recipients)")
            }
            assert "conversation_id" in columns
            assert "external_user_id" in columns

            # ...the unique index exists...
            index = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index'
                  AND name = 'idx_broadcast_recipients_unique'
                """
            ).fetchone()
            assert index is not None

            # ...and the old data was preserved aside, not destroyed.
            backup_rows = conn.execute(
                "SELECT * FROM broadcast_recipients_legacy_backup"
            ).fetchall()
            assert len(backup_rows) == 1

        # Boot must also be idempotent: a second create_tables() run on
        # the healed database succeeds without touching the backup.
        db.create_tables()
    finally:
        db.db_path = original_path


def test_create_tables_leaves_current_shape_untouched(legacy_db_path):
    """A database that already has the CURRENT broadcast_recipients shape
    must not be renamed/backed up by the heal."""
    from pathlib import Path
    from database.database import db

    original_path = db.db_path

    fresh_path = tempfile.mktemp(suffix=".db")
    db.db_path = Path(fresh_path)

    try:
        db.create_tables()  # creates the current shape

        # Seed via a raw connection (foreign_keys off by default in
        # sqlite3) -- this test is about the heal's no-op behavior, not
        # about referential integrity of the seeded row.
        raw = sqlite3.connect(fresh_path)
        raw.execute(
            """
            INSERT INTO broadcast_recipients (
                broadcast_id, conversation_id, external_user_id
            ) VALUES (1, 2, 'ext_1')
            """
        )
        raw.commit()
        raw.close()

        db.create_tables()  # second boot: heal must be a no-op

        with db.connect() as conn:
            rows = conn.execute("SELECT * FROM broadcast_recipients").fetchall()
            assert len(rows) == 1

            backup = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'broadcast_recipients_legacy_backup'
                """
            ).fetchone()
            assert backup is None
    finally:
        db.db_path = original_path
        for _attempt in range(5):
            try:
                if os.path.exists(fresh_path):
                    os.remove(fresh_path)
                break
            except PermissionError:
                time.sleep(0.1)
