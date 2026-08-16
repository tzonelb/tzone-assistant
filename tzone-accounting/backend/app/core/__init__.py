"""The kernel.

It knows how to load modules, replicate any table they declare, and mount their routers.
It knows nothing about accounting — every business concept lives in `app/modules/`.
"""

from .entities import ChildTable, EntityDescriptor
from .errors import ModuleError, ValidationError
from .manifest import Manifest
from .registry import Registry, get_registry, reset_registry

__all__ = [
    "ChildTable",
    "EntityDescriptor",
    "Manifest",
    "ModuleError",
    "Registry",
    "ValidationError",
    "get_registry",
    "reset_registry",
]
