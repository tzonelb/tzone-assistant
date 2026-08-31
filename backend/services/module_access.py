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
from backend.services.demo_gate import demo_gate
from backend.services.module_gate import UnknownModule, module_gate
from backend.services.subscription_gate import subscription_gate
from backend.services.platform_service import PLATFORM_MODULES


logger = logging.getLogger(__name__)


# Re-exported: `UnknownModule` was raised from here before the gate existed, and
# callers that catch it should keep working. There is one class, not two, so a
# handler cannot miss the half it was not told about.
__all__ = [
    "UnknownModule",
    "module_states",
    "module_enabled",
    "require_module",
    "require_active_subscription",
    "refuse_a_demonstration",
]


def module_states(company_id: int) -> dict[str, bool]:
    """Every module key with its resolved on/off state for this company."""
    return module_gate.states(company_id)


def module_enabled(company_id: int, module_key: str) -> bool:
    return module_gate.enabled(company_id, module_key)


def require_active_subscription(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Refuse a company whose subscription has ended.

    `402 Payment Required`, not `403 Forbidden`. A 403 says "you are not
    allowed"; this company *is* allowed and has not paid, and the employee
    reading the message is usually not the person who pays. Same status the
    plan-limit refusal already uses, for the same reason.

    Applied at `include_router` in `main.py`, beside the module gate and for
    the same reason that one is there: a router registered later cannot forget
    a dependency that lives at the registration.

    Not applied to `auth`, and not to `dashboard`. A company locked out of the
    screen that says why it is locked out — and out of the sign-in that reaches
    it — cannot do the thing the lock exists to prompt.
    """
    company_id = auth_service.resolve_company_id(current_user)

    if subscription_gate.lapsed(company_id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                "This workspace is paused because its subscription has ended. "
                "Renew it to bring the screens and the assistant back. Nothing "
                "has been deleted, and messages from customers are still "
                "arriving and being saved."
            ),
        )

    return current_user


def refuse_a_demonstration(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Refuse a demo workspace the one thing it must not do: connect a channel.

    `403 Forbidden`, not the `402` above it. A lapsed company is allowed and
    has not paid; a demonstration has not been shown to be a business at all,
    and money is not what is missing -- an activation code is.

    Applied at `include_router` in `main.py`, beside the module gate and for
    the same reason: a dependency that lives at the registration cannot be
    forgotten by a route added to that router later.

    Why connection and not sending, because the choice is the whole design:
    every outbound path resolves the company's channel credentials first and
    refuses without them, so a workspace that cannot connect cannot send by any
    route -- including one written next year by somebody who never read this
    file. Gating each sender instead is six checks to remember and a seventh
    that ships without one.
    """
    company_id = auth_service.resolve_company_id(current_user)

    if demo_gate.is_demo(company_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This is a demonstration workspace, so it cannot connect a "
                "real channel yet — everything else here works, and the "
                "conversations you can see are samples. Enter your activation "
                "code under Company Settings to turn this into a live "
                "workspace and connect your own accounts."
            ),
        )

    return current_user


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
