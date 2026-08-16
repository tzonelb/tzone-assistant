"""The kernel: discover modules, order them, load them, and hold what they contribute.

Loading is three passes, in Odoo's spirit:

1. **Discover** — read every `app/modules/*/__manifest__.py`. Manifests are data, so the
   dependency graph is known before any module code runs.
2. **Resolve** — topological sort over `depends`, ties broken by `sequence` then key, so the
   install order is deterministic on every machine.
3. **Load** — import each module's `module.py` and call its `setup(registry)`, which is the one
   and only place a module registers tables, entities, routers, seeds and hooks.

Nothing in this file mentions accounting. Deleting every module directory would leave a working
(if empty) server.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter

from .entities import EntityDescriptor
from .errors import ModuleError
from .hooks import HookBus
from .manifest import LoadedModule, Manifest

MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"
MODULES_PACKAGE = "app.modules"

SeedFn = Callable[[sqlite3.Connection], None]


class Registry:
    """Everything the running server knows, contributed by modules."""

    def __init__(self) -> None:
        self.hooks = HookBus()
        self.modules: dict[str, LoadedModule] = {}
        self.entities: dict[str, EntityDescriptor] = {}
        self.routers: list[APIRouter] = []
        self.schema_fragments: list[tuple[str, str]] = []
        self.seeds: list[tuple[str, SeedFn]] = []
        self.settings_defaults: dict[str, Any] = {}
        self._loading: str = "core"
        self._loaded = False

    # ---------------------------------------------------------------- module-facing API

    def add_schema(self, sql: str) -> None:
        """Contribute a schema fragment. Must be idempotent (`CREATE TABLE IF NOT EXISTS`)."""
        self.schema_fragments.append((self._loading, sql))

    def add_entity(self, descriptor: EntityDescriptor) -> None:
        """Declare a replicated table. It joins the sync protocol with no further work."""
        if descriptor.name in self.entities:
            raise ModuleError(
                f"module {self._loading!r}: entity {descriptor.name!r} is already declared by "
                f"{self.entities[descriptor.name].module!r}"
            )
        descriptor.module = self._loading
        self.entities[descriptor.name] = descriptor

    def extend_entity(
        self,
        name: str,
        *,
        validators: list = None,
        before_write: list = None,
    ) -> None:
        """Attach behaviour to an entity another module owns.

        This is how `invoicing` puts legal numbering on `documents`' table without either
        module importing the other.
        """
        descriptor = self.entities.get(name)
        if descriptor is None:
            raise ModuleError(
                f"module {self._loading!r}: cannot extend unknown entity {name!r}"
            )
        descriptor.validators.extend(validators or [])
        descriptor.before_write.extend(before_write or [])

    def add_router(self, router: APIRouter) -> None:
        self.routers.append(router)
        self.modules[self._loading].routers += 1

    def add_seed(self, fn: SeedFn) -> None:
        """Data every fresh database needs. Seeds run in module install order, idempotently."""
        self.seeds.append((self._loading, fn))

    def add_settings_defaults(self, values: dict[str, Any]) -> None:
        """Contribute keys to the company settings document."""
        self.settings_defaults.update(values)

    def on(self, hook: str, fn: Callable[..., Any], sequence: int = 10) -> None:
        self.hooks.on(hook, fn, sequence)

    # ---------------------------------------------------------------- loading

    def load(self, only: list[str] | None = None) -> "Registry":
        if self._loaded:
            return self
        manifests = discover(MODULES_DIR)
        selected = _select(manifests, only)
        for manifest in resolve_order(selected):
            self._loading = manifest.key
            self.hooks.binding_module(manifest.key)
            self.modules[manifest.key] = LoadedModule(manifest=manifest)
            module = importlib.import_module(f"{MODULES_PACKAGE}.{manifest.key}.module")
            setup = getattr(module, "setup", None)
            if setup is None:
                raise ModuleError(f"module {manifest.key!r}: module.py has no setup(registry)")
            setup(self)
            self.modules[manifest.key].entities = [
                name for name, e in self.entities.items() if e.module == manifest.key
            ]
        self._loading = "core"
        self.hooks.binding_module("core")
        self._loaded = True
        return self

    def schema(self) -> str:
        return "\n\n".join(
            f"-- module: {key}\n{sql}" for key, sql in self.schema_fragments
        )

    def describe(self) -> dict:
        return {
            "modules": [
                {
                    **loaded.manifest.as_dict(),
                    "entities": loaded.entities,
                    "routers": loaded.routers,
                }
                for loaded in self.modules.values()
            ],
            "entities": sorted(self.entities),
            "hooks": self.hooks.describe(),
        }


# ---------------------------------------------------------------------- discovery


def discover(root: Path) -> dict[str, Manifest]:
    manifests: dict[str, Manifest] = {}
    if not root.exists():
        return manifests
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if not (entry / "__manifest__.py").exists():
            continue
        module = importlib.import_module(f"{MODULES_PACKAGE}.{entry.name}.__manifest__")
        data = getattr(module, "MANIFEST", None)
        if data is None:
            raise ModuleError(f"module {entry.name!r}: __manifest__.py defines no MANIFEST")
        manifests[entry.name] = Manifest.from_dict(entry.name, data)
    return manifests


def _select(manifests: dict[str, Manifest], only: list[str] | None) -> dict[str, Manifest]:
    """Pick the modules to install, pulling in dependencies of anything explicitly selected."""
    available = {k: m for k, m in manifests.items() if m.installable}
    if only is None:
        env = os.environ.get("ACCOUNTING_MODULES", "").strip()
        only = [part.strip() for part in env.split(",") if part.strip()] or None
    if only is None:
        return available

    wanted: dict[str, Manifest] = {}
    stack = list(only)
    while stack:
        key = stack.pop()
        if key in wanted:
            continue
        manifest = available.get(key)
        if manifest is None:
            raise ModuleError(f"unknown or non-installable module {key!r}")
        wanted[key] = manifest
        stack.extend(manifest.depends)

    # auto_install modules join once all of their dependencies are in.
    changed = True
    while changed:
        changed = False
        for key, manifest in available.items():
            if key in wanted or not manifest.auto_install:
                continue
            if all(dep in wanted for dep in manifest.depends):
                wanted[key] = manifest
                changed = True
    return wanted


def resolve_order(manifests: dict[str, Manifest]) -> list[Manifest]:
    """Topological sort. Raises on a missing dependency or a cycle, naming the modules."""
    for manifest in manifests.values():
        for dependency in manifest.depends:
            if dependency not in manifests:
                raise ModuleError(
                    f"module {manifest.key!r} depends on {dependency!r}, which is not installed"
                )

    ordered: list[Manifest] = []
    placed: set[str] = set()
    remaining = dict(manifests)

    while remaining:
        ready = [
            manifest
            for manifest in remaining.values()
            if all(dependency in placed for dependency in manifest.depends)
        ]
        if not ready:
            raise ModuleError(
                "dependency cycle between modules: " + ", ".join(sorted(remaining))
            )
        ready.sort(key=lambda m: (m.sequence, m.key))
        for manifest in ready:
            ordered.append(manifest)
            placed.add(manifest.key)
            del remaining[manifest.key]
    return ordered


_registry: Registry | None = None


def get_registry() -> Registry:
    """The process-wide registry, loaded on first use."""
    global _registry
    if _registry is None:
        _registry = Registry().load()
    return _registry


def reset_registry() -> None:
    """Drop the loaded registry. Used by tests that install a different module set."""
    global _registry
    _registry = None
