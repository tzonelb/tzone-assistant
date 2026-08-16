"""Database bootstrap: build the schema from the installed modules, then run their seeds.

There is no central schema file. Each module owns its tables and the kernel concatenates the
fragments in install order, so a dependency's tables always exist before a dependent's foreign
keys reference them.
"""

from __future__ import annotations

from ..db import connect, transaction
from .registry import Registry, get_registry

BASE_SCHEMA = """
PRAGMA foreign_keys = ON;

-- Monotonic counters owned by the kernel: the change feed, and any numbering a module needs.
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
"""


def init_db(registry: Registry | None = None) -> None:
    registry = registry or get_registry()
    with connect() as conn:
        conn.executescript(BASE_SCHEMA)
        for key, sql in registry.schema_fragments:
            try:
                conn.executescript(sql)
            except Exception as exc:  # pragma: no cover - developer error surface
                raise RuntimeError(f"module {key!r}: schema failed — {exc}") from exc


def run_seeds(registry: Registry | None = None) -> None:
    """Seeds are idempotent and run in install order, so this is safe on every boot."""
    registry = registry or get_registry()
    with transaction() as conn:
        for _key, seed in registry.seeds:
            seed(conn)


def bootstrap(registry: Registry | None = None) -> None:
    registry = registry or get_registry()
    init_db(registry)
    run_seeds(registry)
