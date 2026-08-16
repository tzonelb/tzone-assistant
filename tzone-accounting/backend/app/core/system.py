"""Introspection: what is installed, what it contributes, and in what order it loaded.

The client uses this to render the Modules screen; a developer uses it to confirm a new module
was picked up without reading any code.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db import utcnow
from ..security import current_user
from .registry import get_registry

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/modules")
def modules(user: dict = Depends(current_user)) -> dict:
    registry = get_registry()
    return {
        "count": len(registry.modules),
        "install_order": list(registry.modules),
        **registry.describe(),
    }


@router.get("/health", dependencies=[])
def health() -> dict:
    registry = get_registry()
    return {
        "status": "ok",
        "service": "tzone-accounting",
        "modules": len(registry.modules),
        "entities": len(registry.entities),
        "server_time": utcnow(),
    }
