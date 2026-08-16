"""Shared test fixtures.

Every test runs against a real, freshly provisioned, encrypted database. Nothing
is mocked at the storage layer, because the properties worth testing here —
tenant isolation and encryption at rest — only exist at that layer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.security import keyring  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def master_key() -> str:
    """Provide a master key for the whole session before anything imports config."""
    key = keyring.generate_master_key()
    os.environ["TZONE_MASTER_KEY"] = key
    return key


@pytest.fixture()
def platform(tmp_path, master_key, monkeypatch):
    """A platform with two provisioned companies.

    Two rather than one on purpose: a single-company fixture cannot catch a
    cross-company leak, which is the failure this codebase actually had.
    """
    from database.manager import DatabaseManager, utc_now_iso

    manager = DatabaseManager(data_dir=tmp_path / "data")

    companies: dict[str, dict] = {}

    with manager.control() as conn:
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO workspaces (name, slug, status, created_at, updated_at)
            VALUES ('Test Workspace', 'test-workspace', 'active', ?, ?)
            """,
            (now, now),
        )

        for name, slug in (("Alpha Corp", "alpha"), ("Beta Corp", "beta")):
            cursor = conn.execute(
                """
                INSERT INTO companies (
                    workspace_id, name, slug, status, created_at, updated_at
                )
                VALUES (1, ?, ?, 'active', ?, ?)
                """,
                (name, slug, now, now),
            )
            companies[slug] = {"id": int(cursor.lastrowid), "name": name}

        conn.commit()

    for slug, company in companies.items():
        code = keyring.generate_workspace_code()
        manager.provision_company(company_id=company["id"], workspace_code=code)
        company["workspace_code"] = code
        company["path"] = manager.tenant_path(company["id"])

    return {"manager": manager, "companies": companies}


@pytest.fixture()
def alpha(platform) -> dict:
    return platform["companies"]["alpha"]


@pytest.fixture()
def beta(platform) -> dict:
    return platform["companies"]["beta"]
