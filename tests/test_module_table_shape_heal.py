"""Regression tests for the module-table shape self-heal in
database.Database.create_tables().

Real scenario (dev machine, 2026-08-04): a parallel development branch
booted against this project's database. Its own self-heal renamed THIS
branch's module tables aside to {table}_legacy_backup and created its
own, incompatible shapes in their place (e.g. a `tasks` table with
`assignee_user_id`/`due_date` instead of this branch's
`assigned_user_id`/`due_at`). Switching back to this branch then left
the app querying columns that don't exist -- and the original rows
stranded in the backup.

These tests prove create_tables() now:
  1. renames a wrong-shape live table aside (never dropping anything),
  2. restores this branch's original table from {table}_legacy_backup
     WITH its rows,
  3. leaves a correct-shape database completely untouched,
  4. is idempotent across repeated boots.

Run with: python3 -m pytest tests/test_module_table_shape_heal.py -v
"""
import os
import sqlite3
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def db_path():
    tmp_path = tempfile.mktemp(suffix=".db")
    yield tmp_path
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def _seed_laptop_state(path):
    """The exact cross-branch state: a foreign-shape `tasks` table live,
    and this branch's original tasks (with a real row) renamed aside to
    tasks_legacy_backup by the other branch's heal."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'normal',
            assignee_user_id INTEGER,
            due_date TEXT,
            related_customer_id INTEGER,
            created_by INTEGER,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tasks_legacy_backup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            task_type TEXT NOT NULL DEFAULT 'other',
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'normal',
            assigned_user_id INTEGER,
            customer_id INTEGER,
            conversation_id INTEGER,
            due_at TEXT,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tasks_legacy_backup (
            company_id, title, created_at, updated_at
        ) VALUES (1, 'my real task from before', '2026-08-01', '2026-08-01')
        """
    )
    conn.commit()
    conn.close()


def test_boot_restores_original_table_and_rows(db_path):
    from pathlib import Path
    from database.database import db

    _seed_laptop_state(db_path)

    original_path = db.db_path
    db.db_path = Path(db_path)
    try:
        db.create_tables()

        with db.connect() as conn:
            # The live tasks table is this branch's shape again...
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(tasks)")
            }
            assert "assigned_user_id" in columns
            assert "due_at" in columns

            # ...with the original row restored, not an empty recreation.
            rows = conn.execute("SELECT title FROM tasks").fetchall()
            assert [row["title"] for row in rows] == [
                "my real task from before"
            ]

            # The foreign-shape table was preserved aside, not dropped.
            wrongshape = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE 'tasks_wrongshape_backup%'
                """
            ).fetchall()
            assert len(wrongshape) == 1

        # Idempotent: a second boot changes nothing further.
        db.create_tables()
        with db.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()
            assert rows["c"] == 1
    finally:
        db.db_path = original_path


def test_correct_shape_database_is_untouched(db_path):
    from pathlib import Path
    from database.database import db
    from backend.services.task_service import task_service

    original_path = db.db_path
    db.db_path = Path(db_path)
    try:
        db.create_tables()
        task_service.ensure_schema()

        raw = sqlite3.connect(db_path)
        raw.execute(
            """
            INSERT INTO tasks (
                company_id, title, status, priority, created_at, updated_at
            ) VALUES (1, 'kept task', 'open', 'normal', '2026-08-04', '2026-08-04')
            """
        )
        raw.commit()
        raw.close()

        db.create_tables()  # second boot: heal must be a no-op

        with db.connect() as conn:
            rows = conn.execute("SELECT title FROM tasks").fetchall()
            assert [row["title"] for row in rows] == ["kept task"]

            backups = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND (
                    name LIKE '%_wrongshape_backup%'
                    OR name LIKE '%_legacy_backup%'
                )
                """
            ).fetchall()
            assert backups == []
    finally:
        db.db_path = original_path


def test_wrong_shape_without_backup_is_renamed_aside(db_path):
    from pathlib import Path
    from database.database import db

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE call_logs (id INTEGER PRIMARY KEY, company_id INTEGER, called_at TEXT)"
    )
    conn.commit()
    conn.close()

    original_path = db.db_path
    db.db_path = Path(db_path)
    try:
        db.create_tables()

        with db.connect() as conn:
            assert (
                conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'call_logs_wrongshape_backup'
                    """
                ).fetchone()
                is not None
            )
            # The live name is free (or recreated correctly by the
            # service later) -- either way no wrong-shape table remains
            # under the real name.
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='call_logs'"
            ).fetchone():
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(call_logs)")
                }
                assert "called_by_user_id" in columns
    finally:
        db.db_path = original_path
