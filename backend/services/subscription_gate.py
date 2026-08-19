"""One answer to "may this company operate today", for every layer.

The owner's decision, taken explicitly: **when a subscription lapses the
company stops working until it is renewed.** Before this, expiry did nothing —
`is_active` was computed, shown on a screen, and consulted by no code path that
could refuse anything. A company could stop paying and carry on indefinitely,
and the operator's only lever was suspension, which is a much heavier act and
reads to a customer as an accusation rather than an invoice.

What "stops working" means, stated part by part because each part was a
separate decision:

* **Every module refuses**, with `402 Payment Required`. Not `403`: a 403 tells
  somebody they are not allowed, and this is a company that *is* allowed and
  has not paid. The person reading the message is usually not the person who
  pays, so the words have to be different or the first support call is "why
  can't I open the inbox".
* **The assistant stops answering customers.** This is what makes the policy
  real. Screens nobody can open is an inconvenience; an assistant that keeps
  working is the service still being delivered, for free, with no reason to
  renew.
* **Inbound messages are still stored, and still raise a notification.** A
  company that renews on Thursday must find Tuesday's customers waiting, not a
  hole. The customer owes nobody anything and must not lose their message.
* **Nothing is said to the customer.** Silence, not "this business has not paid
  its bill" — which would expose the owner to their own customers.
* **Sign-in and the subscription screen stay open.** A company locked out of
  the page explaining why it is locked out cannot act on it, and prompting the
  action is the entire point.

The grace period belongs to the operator, not to this module.
`plan_service.is_active` already treats `grace_period_until` as entitlement, so
setting it per company is how an operator says "the payment is late and the
service continues". Nothing here invents a second one.

**Failure is open**, the same way the module gate fails open and for the same
reason. If the control plane cannot be read, refusing would take every company
on the platform off the air over a bill none of them owe. Being late to enforce
one lapse costs the operator minutes of service; being wrong the other way
costs every customer their assistant mid-conversation.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from backend.services.plan_service import plan_service


logger = logging.getLogger(__name__)


# The same window the module gate uses, for the same reasons: a busy company
# answering hundreds of messages reads the control plane once rather than
# hundreds of times, and an operator who renews sees it take effect while still
# looking at the screen. Renewal invalidates immediately anyway, so this bounds
# only the case where another process changed the row.
CACHE_SECONDS = 30.0


class SubscriptionGate:
    """Whether a company may operate, cached and invalidated on renewal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[int, tuple[float, bool]] = {}

    # ------------------------------------------------------------------ read

    def entitled(self, company_id: Any) -> bool:
        """True when this company may operate today.

        An unreadable company id, or a control plane that will not answer,
        resolves to entitled — see the note on failing open above.
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

        entitled = self._read(resolved)

        with self._lock:
            self._cache[resolved] = (now + CACHE_SECONDS, entitled)

        return entitled

    def lapsed(self, company_id: Any) -> bool:
        """The same question the other way round, because every caller asks it
        that way and `not gate.entitled(...)` reads worse at each of them."""
        return not self.entitled(company_id)

    def _read(self, company_id: int) -> bool:
        try:
            subscription = plan_service.subscription(company_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not read the subscription for company %s; allowing it to "
                "operate",
                company_id,
            )

            return True

        # **No subscription is not a lapsed subscription.** `is_active(None)`
        # is False, and taking that as "pause them" would have bricked every
        # newly created company: `create_company` takes `plan_code` as an
        # optional argument and the CLI has no `--plan` flag at all, so a
        # company provisioned today has no subscription row until an operator
        # assigns one. Reading `is_active` directly here meant a company was
        # dark from the moment it was created — every screen 402, the assistant
        # silent — with nothing in the console explaining why.
        #
        # The decision was that a subscription which *ends* stops the company.
        # Something that never began has not ended. An operator who wants a
        # company stopped before it is billed has suspension, which is
        # immediate and says so.
        if not subscription:
            return True

        return bool(plan_service.is_active(subscription))

    # ----------------------------------------------------------- invalidation

    def invalidate(self, company_id: Any = None) -> None:
        """Drop cached entitlement, for one company or all of them.

        Called when a plan is assigned, so a renewal takes effect on the next
        request rather than up to thirty seconds later. That half minute is
        exactly when an operator is watching, having just told a customer they
        are back on.
        """
        with self._lock:
            if company_id is None:
                self._cache.clear()

                return

            try:
                self._cache.pop(int(company_id), None)
            except (TypeError, ValueError):
                self._cache.clear()


subscription_gate = SubscriptionGate()
