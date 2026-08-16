"""T-ZONE Accounting API.

The application is assembled at startup from whatever modules are installed: the kernel loads
them, builds the schema, mounts their routers, and gets out of the way.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .core import sync, system
from .core.bootstrap import bootstrap
from .core.registry import get_registry


def create_app() -> FastAPI:
    registry = get_registry()

    app = FastAPI(
        title="T-ZONE Accounting API",
        version="1.0.0",
        description=(
            "Modular, offline-first accounting. The browser owns the working copy of the "
            "ledger; this service validates, stores and consolidates it. Every business "
            "capability is a module — see GET /api/system/modules."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Kernel endpoints, then everything the installed modules contribute.
    app.include_router(system.router)
    app.include_router(sync.router)
    for router in registry.routers:
        app.include_router(router)

    @app.on_event("startup")
    def _startup() -> None:
        bootstrap(registry)

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return system.health()

    return app


app = create_app()
