"""One answer to "has an operator switched this company off", for every layer.

Suspension already worked at the front door. `set_company_status` flips
`companies.status` and revokes every live session, and `auth_service` joins on
`companies.status = 'active'`, so a suspended company's employees are signed
out and cannot sign back in.

What it did not do was stop the company. A customer messaging the suspended
company's Messenger page still routed to it -- `resolve_account_for_channel`
filters on `channel_accounts.status`, never on the company's -- and the
assistant answered, because nothing on the inbound path asked. The screens went
dark and the service carried on.

That is backwards twice over. Suspension is the operator's heaviest lever, and
it was doing strictly less than the lightest one: an unpaid bill already stops
the assistant, through `subscription_gate`. `subscription_gate` even leans on
this, in as many words -- "an operator who wants a company stopped before it is
billed has suspension, which is immediate and says so". It was not immediate
and it did not say so.

The rule enforced here is the one suspension is for: **a suspended company
stops acting.** Its assistant answers nobody, its scheduled posts do not
publish, its queued replies are not sent.

What suspension deliberately does *not* do, matching the subscription gate
line for line, because the reasoning is identical:

* **Inbound messages are still stored, and still raise a notification.** The
  customer owes nobody anything and must not lose their message. A company that
  is reinstated on Thursday must find Tuesday's customers waiting, not a hole.
* **Nothing is said to the customer.** Silence. Explaining that a business has
  been suspended would expose the owner to their own customers, which is a
  worse thing to do to them than the pause.

Why this is not folded into `subscription_gate`: the two answers drive
different words. An unpaid bill is `402 Payment Required` -- that company *is*
allowed and has not paid. A suspension is `403` -- an operator decided. Sharing
a cache would be convenient and would make one of the two messages wrong.

Why it is not a FastAPI dependency: `channels/` and `core/` ask this question
per message and have no business importing the web layer to do it. The HTTP
side is already covered by sign-in itself.

**Failure is open**, the same way the module gate and the subscription gate
fail open, and for the same reason. If the control plane cannot be read,
refusing would take every company on the platform off the air over a suspension
none of them are under. Being late to enforce one suspension costs minutes;
being wrong the other way costs every customer their assistant mid-sentence.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


# The same window the module gate and the subscription gate use, for the same
# reason: a busy company answering hundreds of messages reads the control plane
# once rather than hundreds of times. Suspension invalidates immediately
# anyway, so this bounds only the case where another process changed the row.
CACHE_SECONDS = 30.0


class CompanyGate:
    """Whether an operator has switched this company off."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[int, tuple[float, bool]] = {}

    # ------------------------------------------------------------------ read

    def active(self, company_id: Any) -> bool:
        """True when this company has not been suspended.

        An unreadable company id, or a control plane that will not answer,
        resolves to active -- see the note on failing open above.
        """
        try:
            resolved = int(company_id)
        except (TypeError, ValueError):
            return True

        now = time.monotonic()

        with self._lock:
            cached = self._cache.get(resolved)

            if cached and cached[0] > now:
                return cached[1]

        active = self._read(resolved)

        with self._lock:
            self._cache[resolved] = (now + CACHE_SECONDS, active)

        return active

    def suspended(self, company_id: Any) -> bool:
        """The same question the other way round, because every caller asks it
        that way and `not gate.active(...)` reads worse at each of them."""
        return not self.active(company_id)

    def _read(self, company_id: int) -> bool:
        try:
            with database_manager.control() as conn:
                row = conn.execute(
                    "SELECT status FROM companies WHERE id = ? LIMIT 1",
                    (company_id,),
                ).fetchone()
        except Exception:  # noqa: BLE001 - see the note on failing open
            logger.exception(
                "Could not read the status of company %s; allowing it to operate",
                company_id,
            )

            return True

        # A company id with no row is not a suspended company. It is a routing
        # bug or a deleted company, and either way refusing here would report it
        # as a suspension in the diagnostics an owner reads.
        if not row:
            return True

        return str(row["status"]).strip().lower() == "active"

    # ----------------------------------------------------------- invalidation

    def invalidate(self, company_id: Any = None) -> None:
        """Drop the cached status, for one company or all of them.

        Called when an operator suspends or reinstates a company, so the change
        takes effect on the next message rather than up to thirty seconds
        later. That half minute is exactly when the operator is watching.
        """
        with self._lock:
            if company_id is None:
                self._cache.clear()

                return

            try:
                self._cache.pop(int(company_id), None)
            except (TypeError, ValueError):
                self._cache.clear()


company_gate = CompanyGate()
