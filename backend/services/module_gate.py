"""One answer to "is this module on for this company", for every layer.

The Super Admin's module switches used to be enforced in exactly one place:
`require_module`, a FastAPI dependency on the customer routers. That closed the
API but left the half a customer actually sees wide open. A company that turned
**Catalogue** off got the screen hidden from its team — and its assistant went
on quoting catalogue prices to customers, because `core/business_connectors.py`
calls `catalogue_service` with no switch anywhere near it. Same for **Tasks**
(the engine opened tickets at `engine.create_ticket`) and **Knowledge** (the
engine read the base at `engine.load_knowledge`).

So the switch said off and the behaviour stayed on. The owner's decision was
being overruled by the code — which is exactly backwards: the code is the
mechanism, the owner sets the policy.

The rule this module enforces is the one the owner chose: **a module that is
off is off as if it had never been installed.** Not hidden — absent.

Why this file exists rather than calling `module_access.module_enabled`:

* `module_access` imports FastAPI and `auth_service` to build a dependency.
  `core/` and `channels/` have no business importing the web layer to ask a
  yes/no question about configuration, and doing so would make the engine
  untestable without a request.
* The reply path asks per message. `get_platform_config` opens the encrypted
  control database every call; at a thousand companies answering at once that
  is a read per message for a value that changes a few times a year. This
  caches it for `MODULE_GATE_CACHE_SECONDS`.

`module_access` now delegates here, so the HTTP layer and the assistant read
the same truth from the same cache. Two sources would eventually disagree, and
the disagreement would be invisible: the screen off, the assistant on.

**Failure is open, deliberately.** If the control plane cannot be read, every
module reports on. Refusing instead would mean a blip in one database silently
strips every company's assistant of its knowledge and its catalogue mid
conversation — a far worse outcome than briefly honouring a switch late. The
HTTP layer already made this same choice; it is stated here once for both.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.gate_cache import GateCache
from backend.services.platform_service import (
    PLATFORM_MODULES,
    PlatformNotFound,
    platform_service,
)
from database.manager import DatabaseError


logger = logging.getLogger(__name__)


class UnknownModule(KeyError):
    """A module key the platform does not define."""


# Long enough that a busy company answering hundreds of messages reads the
# control plane once rather than hundreds of times; short enough that an
# operator flipping a switch sees it take effect while still watching the
# screen. Writes invalidate immediately anyway — this bounds only the case
# where the row was changed by another process.
CACHE_SECONDS = 30.0


class ModuleGate:
    """Resolved module states for a company, cached and invalidated on write."""

    # The caching lives in `GateCache` rather than here. All three gates had
    # the same hand-written cache and therefore the same hole: a read that
    # began before an invalidate published its stale answer afterwards, and
    # the operator's change was masked for the whole window. See that module
    # for the generation counter that closes it.
    def __init__(self) -> None:
        self._cache = GateCache(CACHE_SECONDS)

    # ------------------------------------------------------------------ read

    def states(self, company_id: Any) -> dict[str, bool]:
        """Every module key with its on/off state for this company.

        Returns every module as on when the company is unknown or the control
        plane is unreadable. See the note on failing open in the module
        docstring.
        """
        try:
            resolved = int(company_id)
        except (TypeError, ValueError):
            return self._all_on()

        # `copy=dict` because this answer is mutable: without it a caller that
        # edited what it was handed would edit what every later caller is told.
        return self._cache.read_through(
            resolved, lambda: self._read(resolved), copy=dict
        )

    def enabled(self, company_id: Any, module_key: str) -> bool:
        """Whether one module is on. Unknown keys are a programming error.

        Validated rather than defaulted: a typo in a call site would otherwise
        read as "off" and silently remove a feature nobody switched off, or as
        "on" and silently keep one nobody could switch off. Both are worse than
        a crash in a test.
        """
        if module_key not in PLATFORM_MODULES:
            raise UnknownModule(
                f"{module_key!r} is not a platform module. "
                f"Valid keys are: {', '.join(PLATFORM_MODULES)}."
            )

        return bool(self.states(company_id).get(module_key, True))

    # ----------------------------------------------------------- invalidation

    def invalidate(self, company_id: Any = None) -> None:
        """Drop cached states, for one company or all of them.

        Called by `platform_service` whenever the config row is written, so an
        operator's change applies to the next message rather than the next
        thirty seconds of them.
        """
        if company_id is None:
            self._cache.invalidate()

            return

        try:
            self._cache.invalidate(int(company_id))
        except (TypeError, ValueError):
            self._cache.invalidate()

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _all_on() -> dict[str, bool]:
        return {key: True for key in PLATFORM_MODULES}

    def _read(self, company_id: int) -> dict[str, bool]:
        try:
            config = platform_service.get_platform_config(company_id)
        except PlatformNotFound:
            # No such company. Nothing will be served for it anyway; reporting
            # on keeps this identical to the pre-existing HTTP behaviour rather
            # than inventing a second meaning for a missing row.
            return self._all_on()
        except DatabaseError:
            logger.exception(
                "Could not read module config for company %s; treating every "
                "module as enabled.",
                company_id,
            )

            return self._all_on()

        modules = config.get("modules") or {}

        return {key: bool(modules.get(key, True)) for key in PLATFORM_MODULES}


module_gate = ModuleGate()


def module_enabled(company_id: Any, module_key: str) -> bool:
    """Module-level shorthand, so a call site reads as a plain question."""
    return module_gate.enabled(company_id, module_key)
