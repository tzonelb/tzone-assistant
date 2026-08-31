"""Whether a stream that is already open may keep running.

Every other route is checked once, answers, and is gone. The two live-event
endpoints are different: they authenticate once and then hold the connection
open for as long as the browser is on the screen, pushing the company's inbox
or its team chat every time either changes. A dependency runs at connection
time, so all three guards -- the permission, the session, the gates -- were
answered once, for a screen that then stayed open for hours.

That left revocation not revoking. Measured, on a stream already proven to be
pushing: suspending the company and revoking every one of the employee's
sessions each left it pushing the next change anyway. `set_company_status` does
revoke sessions, and it made no difference, because nothing in the loop ever
looked at the session again. A token lasts twelve hours by default and a
dashboard left open on a desk lasts longer than that.

So this is asked once per pass instead. Three questions, and the reason for
each:

* **Is the session still real?** This is what makes revoking access mean it,
  and it also covers expiry, a disabled account and a disabled membership,
  because `get_user_from_token` already checks all of them.
* **Has an operator suspended the company?** The heaviest lever must reach a
  screen that is already open, not only the next one to be opened.
* **Has the subscription lapsed?** Every module answers 402 once it has. A
  stream that keeps delivering the inbox is the same screen, still working.

The cost is one indexed control-plane read per pass, beside the tenant read the
poll already does. The two gates are cached, so they are usually free.

Failure here is **closed**, unlike the gates. That looks inconsistent and is
deliberate: the gates fail open because refusing would take a thousand
companies off the air over a bill none of them owe, whereas this refuses one
already-open stream that the browser will immediately reopen -- and reopening
runs the real dependencies. The cheap mistake is to close; the expensive one is
to keep streaming to somebody whose access was taken away.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.auth_service import auth_service
from backend.services.company_gate import company_gate
from backend.services.subscription_gate import subscription_gate


logger = logging.getLogger(__name__)


def may_continue(current_user: dict[str, Any]) -> bool:
    """True while this open stream is still entitled to be delivering."""
    try:
        token = current_user.get("_raw_token")

        # No token to re-check means the caller did not carry one -- which is
        # not something to wave through on a stream that is already running.
        if not token:
            return False

        live = auth_service.get_user_from_token(str(token))

        if not live:
            return False

        if int(live.get("id") or 0) != int(current_user.get("id") or -1):
            return False

        company_id = auth_service.resolve_company_id(live)

        if company_gate.suspended(company_id):
            return False

        if subscription_gate.lapsed(company_id):
            return False

        return True
    except Exception:  # noqa: BLE001 - see the note on failing closed above
        logger.exception("Could not re-check an open stream; closing it")

        return False
