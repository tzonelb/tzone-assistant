"""One answer to "is this workspace a demonstration", for every layer.

Anyone can create a workspace from the sign-up screen. That is the point of it,
and it is also the whole security problem: a self-service account that can
connect a real WhatsApp number and start sending is a spam relay with the
operator's infrastructure behind it and the operator's name on the abuse
report. So a demo workspace is defined by what it cannot reach, and the
definition is enforced here rather than described in the sign-up copy.

**What a demo workspace cannot do: connect a channel.** Not "cannot send" --
cannot connect. That is deliberately one step earlier than the obvious place,
and it is the difference between a rule and a rule with a hole in it. Every
outbound path on this platform -- a manual reply, a broadcast, a scheduled
post, a comment reply, the assistant's own answer -- resolves the company's
channel credentials before it can send anything, and refuses when there are
none. Gating the connection therefore closes all of them at once, in a single
place that a new outbound feature cannot forget to consult, because a feature
added next year still has to ask for credentials that are not there.

Gating each sender instead would mean six checks that must each be remembered,
and the seventh sender ships without one.

**Everything else works.** A demo workspace reads its seeded conversations,
edits its catalogue and its knowledge, writes tasks, changes its settings and
reads its reports. A demonstration that refuses at every turn demonstrates
nothing, and the restriction that matters is the one at the boundary where a
message would leave the building.

**Failure is closed here**, and that is the opposite of the module and
subscription gates, on purpose. Those two fail open because being unable to
read the control plane must not take paying companies off the air over a bill
none of them owe. This one answers a different question: if the platform cannot
tell whether a workspace is a demonstration, the safe answer is that it is,
because the cost of being wrong is a stranger connecting a real channel, and
the cost the other way is one legitimate owner waiting for the control plane to
come back before they can connect theirs.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.gate_cache import GateCache
from database.manager import database_manager


logger = logging.getLogger(__name__)


# The same window the other two gates use. Redeeming an activation code
# invalidates immediately, so this bounds only the case where another process
# changed the row -- and the owner who has just typed their code is watching
# the screen, which is exactly when half a minute is too long.
CACHE_SECONDS = 30.0


class DemoGate:
    """Whether a company is a demonstration, cached and invalidated on activation."""

    def __init__(self) -> None:
        self._cache = GateCache(CACHE_SECONDS)

    # ------------------------------------------------------------------ read

    def is_demo(self, company_id: Any) -> bool:
        """True when this workspace has not been activated with a code.

        An unreadable company id resolves to demo, for the reason in the module
        docstring: the failure that costs least is refusing a connection.
        """
        try:
            resolved = int(company_id)
        except (TypeError, ValueError):
            return True

        return self._cache.read_through(resolved, lambda: self._read(resolved))

    def _read(self, company_id: int) -> bool:
        try:
            with database_manager.control() as conn:
                row = conn.execute(
                    "SELECT is_demo FROM companies WHERE id = ? LIMIT 1",
                    (company_id,),
                ).fetchone()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not read the demo flag for company %s; treating it as a "
                "demonstration",
                company_id,
            )

            return True

        # A company id that names no row is not a company. Refusing is both the
        # safe answer and the honest one.
        if row is None:
            return True

        return bool(row["is_demo"])

    # ----------------------------------------------------------- invalidation

    def invalidate(self, company_id: Any = None) -> None:
        """Drop the cached answer, for one company or all of them.

        Called when an activation code is redeemed, so the owner's very next
        request can connect a channel rather than being refused for up to
        thirty more seconds by an answer that is no longer true.
        """
        if company_id is None:
            self._cache.invalidate()

            return

        try:
            self._cache.invalidate(int(company_id))
        except (TypeError, ValueError):
            self._cache.invalidate()


demo_gate = DemoGate()
