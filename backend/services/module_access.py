"""Enforcing the Super Admin's module switches on the customer API.

The control plane lets a platform administrator decide which modules a company
sees. Storing that decision is the easy half. This module is the other half: if
the only thing a switch did was hide a link in the sidebar, then a company whose
"Catalogue" was turned off could still read and write its catalogue by calling
the API directly, and the operator would believe otherwise.

So the switch is enforced here, at the same layer permissions are enforced, and
the navigation merely reflects it.

A module that is absent from a company's stored config is **on**. Defaulting to
off would mean that shipping a new module silently disables it for every
existing company until somebody edits each one by hand.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import Depends, HTTPException, status

from backend.services.auth_service import auth_service, get_current_user
from backend.services.module_gate import UnknownModule, module_gate
from backend.services.platform_service import PLATFORM_MODULES


logger = logging.getLogger(__name__)


# Re-exported: `UnknownModule` was raised from here before the gate existed, and
# callers that catch it should keep working. There is one class, not two, so a
# handler cannot miss the half it was not told about.
__all__ = ["UnknownModule", "module_states", "module_enabled", "require_module"]


def module_states(company_id: int) -> dict[str, bool]:
    """Every module key with its resolved on/off state for this company."""
    return module_gate.states(company_id)


def module_enabled(company_id: int, module_key: str) -> bool:
    return module_gate.enabled(company_id, module_key)


def require_module(module_key: str) -> Callable:
    """Build a dependency that refuses a module the operator switched off.

    The key is validated at import time rather than per request, so a typo in a
    router registration fails the process on startup instead of quietly
    permitting everything.
    """
    if module_key not in PLATFORM_MODULES:
        raise UnknownModule(
            f"{module_key!r} is not a platform module. "
            f"Valid keys are: {', '.join(PLATFORM_MODULES)}."
        )

    def dependency(
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        company_id = auth_service.resolve_company_id(current_user)

        # No `DatabaseError` handling here any more: the gate fails open on a
        # control-plane failure and says so in the log. Catching it a second
        # time was how the two layers could have drifted apart — one of them
        # allowing on error while the other refused.
        if not module_gate.enabled(company_id, module_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This module is not enabled for your company. "
                    "Contact your platform administrator."
                ),
            )

        return current_user

    dependency.__name__ = f"require_module_{module_key}"
    return dependency
