"""`python -m app.seed` — build the schema and run every installed module's seeds.

There is no central seed script: the kernel asks each installed module for its data, in install
order. Installing a module later and re-running this is safe — seeds are idempotent.
"""

from __future__ import annotations

from .config import get_settings
from .core.bootstrap import bootstrap
from .core.registry import get_registry


def main() -> None:
    registry = get_registry()
    bootstrap(registry)

    settings = get_settings()
    print(f"database: {settings.db_path}")
    print(f"modules ({len(registry.modules)}): {', '.join(registry.modules)}")
    print(f"entities ({len(registry.entities)}): {', '.join(sorted(registry.entities))}")
    print(f"admin user: {settings.admin_username}")


if __name__ == "__main__":
    main()
