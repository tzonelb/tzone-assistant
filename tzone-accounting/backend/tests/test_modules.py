"""Tests for the kernel itself: discovery, dependency resolution, and module isolation."""

from __future__ import annotations

import pytest

from app.core.errors import ModuleError
from app.core.manifest import Manifest
from app.core.registry import Registry, discover, resolve_order
from app.core.registry import MODULES_DIR


def _manifest(key: str, depends: tuple[str, ...] = (), sequence: int = 100) -> Manifest:
    return Manifest(key=key, name=key.title(), depends=depends, sequence=sequence)


def test_every_module_directory_has_a_valid_manifest():
    manifests = discover(MODULES_DIR)
    assert {"base", "accounting", "documents", "invoicing", "payments"} <= set(manifests)
    for key, manifest in manifests.items():
        assert manifest.key == key
        assert manifest.name
        assert manifest.summary, f"module {key} has no summary"


def test_dependencies_always_load_before_dependents():
    order = [m.key for m in resolve_order(discover(MODULES_DIR))]
    position = {key: index for index, key in enumerate(order)}
    for manifest in discover(MODULES_DIR).values():
        for dependency in manifest.depends:
            assert position[dependency] < position[manifest.key], (
                f"{manifest.key} loads before its dependency {dependency}"
            )


def test_order_is_deterministic():
    manifests = discover(MODULES_DIR)
    assert [m.key for m in resolve_order(manifests)] == [
        m.key for m in resolve_order(dict(reversed(list(manifests.items()))))
    ]


def test_missing_dependency_is_reported_by_name():
    with pytest.raises(ModuleError, match="depends on 'ghost'"):
        resolve_order({"a": _manifest("a", depends=("ghost",))})


def test_dependency_cycle_is_reported():
    with pytest.raises(ModuleError, match="cycle"):
        resolve_order(
            {"a": _manifest("a", depends=("b",)), "b": _manifest("b", depends=("a",))}
        )


def test_sequence_breaks_ties_between_independent_modules():
    order = resolve_order(
        {
            "late": _manifest("late", sequence=90),
            "early": _manifest("early", sequence=10),
        }
    )
    assert [m.key for m in order] == ["early", "late"]


def test_unknown_manifest_key_is_rejected():
    with pytest.raises(ModuleError, match="unknown manifest keys"):
        Manifest.from_dict("x", {"name": "X", "dependz": ["base"]})


def test_a_subset_install_pulls_in_dependencies_only(app_env):
    registry = Registry().load(only=["reports"])
    assert set(registry.modules) == {"base", "accounting", "reports"}
    # Nothing invoicing-related is present, and its entity is therefore unknown.
    assert "document" not in registry.entities
    assert "journal_entry" in registry.entities


def test_the_server_runs_without_the_optional_modules(app_env):
    from fastapi.testclient import TestClient

    from app.core.registry import reset_registry

    reset_registry()
    import os

    os.environ["ACCOUNTING_MODULES"] = "reports"
    try:
        from app.main import create_app

        with TestClient(create_app()) as client:
            body = client.get("/api/health").json()
            assert body["status"] == "ok"
            assert body["modules"] == 3
            login = client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin123"}
            )
            assert login.status_code == 200
            client.headers["Authorization"] = f"Bearer {login.json()['token']}"
            # The documents module is not installed, so its endpoint does not exist.
            assert client.get("/api/documents/types").status_code == 404
    finally:
        os.environ.pop("ACCOUNTING_MODULES", None)


def test_two_modules_cannot_claim_the_same_entity(app_env):
    from app.core.entities import EntityDescriptor

    registry = Registry()
    registry.add_entity(EntityDescriptor(name="thing", table="things", columns=("a",)))
    with pytest.raises(ModuleError, match="already declared"):
        registry.add_entity(EntityDescriptor(name="thing", table="others", columns=("a",)))


def test_extending_an_unknown_entity_fails_loudly(app_env):
    registry = Registry()
    with pytest.raises(ModuleError, match="unknown entity"):
        registry.extend_entity("nope", validators=[])


def test_modules_endpoint_describes_the_graph(client):
    body = client.get("/api/system/modules").json()
    keys = {module["key"] for module in body["modules"]}
    assert {"base", "accounting", "documents", "invoicing", "audit_log"} <= keys
    assert body["install_order"][0] == "base"
    assert "record_stored" in body["hooks"]


def test_document_types_come_from_their_owning_modules(client):
    types = {t["key"]: t for t in client.get("/api/documents/types").json()["types"]}
    assert types["sales_invoice"]["module"] == "invoicing"
    assert types["receipt"]["module"] == "payments"
    assert types["receipt"]["role"] == "settlement"


def test_audit_log_records_changes_from_a_module_it_knows_nothing_about(client, push):
    from conftest import entry

    push([("journal_entry", entry("a1", "2026-03-01", [("acc-1110", 100, 0), ("acc-4100", 0, 100)]))])
    entries = client.get("/api/audit", params={"entity": "journal_entry"}).json()["entries"]
    assert entries[0]["record_id"] == "a1"
    assert entries[0]["summary"] == "entry_no=JV-a1"


def test_audit_log_records_rejections(client, push):
    from conftest import entry

    push([("journal_entry", entry("bad", "2026-03-01", [("acc-1110", 100, 0), ("acc-4100", 0, 90)]))])
    entries = client.get("/api/audit", params={"action": "rejected"}).json()["entries"]
    assert "unbalanced" in entries[0]["summary"]
