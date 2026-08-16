"""Base module: identity and company settings."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from ...config import get_settings
from ...core.entities import CHANGE_SEQ, EntityDescriptor
from ...core.registry import Registry
from ...db import next_counter, utcnow
from ...security import hash_password
from . import api

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

DEFAULT_SETTINGS = {
    "company_name": "T-ZONE",
    "language": "ar",
    "lock_date": None,
}


def setup(registry: Registry) -> None:
    registry.add_schema(SCHEMA)
    registry.add_settings_defaults(DEFAULT_SETTINGS)

    registry.add_entity(
        EntityDescriptor(
            name="settings",
            table="settings",
            columns=("payload",),
            json_columns=("payload",),
            defaults={"payload": "{}"},
        )
    )

    registry.add_router(api.router)
    registry.add_seed(seed_admin_user)
    # Reads registry.settings_defaults lazily, at seed time — by then every installed module
    # has contributed its own keys, even though base itself loaded first.
    registry.add_seed(lambda conn: seed_company_settings(conn, registry))


def seed_admin_user(conn: sqlite3.Connection) -> None:
    settings = get_settings()
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ?", (settings.admin_username,)
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role, is_active,"
        " created_at) VALUES (?,?,?,?,?,1,?)",
        (
            str(uuid.uuid4()),
            settings.admin_username,
            hash_password(settings.admin_password),
            "Administrator",
            "admin",
            utcnow(),
        ),
    )


def seed_company_settings(conn: sqlite3.Connection, registry: Registry) -> None:
    """Create the company settings row, or backfill keys added by newly installed modules.

    Backfilling matters: installing a module later must not require the user to re-enter
    settings, and must not clobber values they have already changed.
    """
    row = conn.execute("SELECT payload FROM settings WHERE id = 'company'").fetchone()
    defaults = registry.settings_defaults
    if row is None:
        conn.execute(
            "INSERT INTO settings (id, payload, rev, updated_at, deleted, origin, change_seq)"
            " VALUES ('company', ?, 1, ?, 0, 'seed', ?)",
            (
                json.dumps(defaults, ensure_ascii=False),
                utcnow(),
                next_counter(conn, CHANGE_SEQ),
            ),
        )
        return

    try:
        current = json.loads(row["payload"] or "{}")
    except json.JSONDecodeError:
        current = {}
    missing = {key: value for key, value in defaults.items() if key not in current}
    if not missing:
        return
    current.update(missing)
    conn.execute(
        "UPDATE settings SET payload = ?, rev = rev + 1, updated_at = ?, change_seq = ?"
        " WHERE id = 'company'",
        (
            json.dumps(current, ensure_ascii=False),
            utcnow(),
            next_counter(conn, CHANGE_SEQ),
        ),
    )
