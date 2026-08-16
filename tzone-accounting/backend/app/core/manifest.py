"""Module manifests.

Every module directory contains a `__manifest__.py` that exports a `MANIFEST` dict:

    MANIFEST = {
        "name": "Invoicing",
        "name_ar": "الفوترة",
        "version": "1.0.0",
        "summary": "Sales and purchase invoices with tax and aging.",
        "category": "Accounting",
        "depends": ["documents", "accounting", "partners", "catalog"],
        "sequence": 40,
    }

The manifest is data, deliberately: the kernel can read the whole module graph — names,
dependencies, install order — without importing a single line of module code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ModuleError


@dataclass(frozen=True)
class Manifest:
    key: str
    name: str
    version: str = "1.0.0"
    name_ar: str = ""
    summary: str = ""
    category: str = "Other"
    author: str = "T-ZONE"
    depends: tuple[str, ...] = ()
    # Lower loads first among modules whose dependencies are equally satisfied.
    sequence: int = 100
    # Installed automatically once all of its dependencies are installed (Odoo's `auto_install`).
    auto_install: bool = False
    # A module can be excluded from a deployment without deleting it.
    installable: bool = True

    @staticmethod
    def from_dict(key: str, data: dict) -> "Manifest":
        if not isinstance(data, dict):
            raise ModuleError(f"module {key!r}: MANIFEST must be a dict")
        if "name" not in data:
            raise ModuleError(f"module {key!r}: manifest has no 'name'")
        unknown = set(data) - {f for f in Manifest.__dataclass_fields__ if f != "key"}
        if unknown:
            raise ModuleError(f"module {key!r}: unknown manifest keys {sorted(unknown)}")
        return Manifest(
            key=key,
            name=data["name"],
            version=str(data.get("version", "1.0.0")),
            name_ar=data.get("name_ar", ""),
            summary=data.get("summary", ""),
            category=data.get("category", "Other"),
            author=data.get("author", "T-ZONE"),
            depends=tuple(data.get("depends", ())),
            sequence=int(data.get("sequence", 100)),
            auto_install=bool(data.get("auto_install", False)),
            installable=bool(data.get("installable", True)),
        )

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "name_ar": self.name_ar,
            "version": self.version,
            "summary": self.summary,
            "category": self.category,
            "author": self.author,
            "depends": list(self.depends),
            "sequence": self.sequence,
        }


@dataclass
class LoadedModule:
    manifest: Manifest
    schema_sql: str = ""
    entities: list[str] = field(default_factory=list)
    routers: int = 0
