"""Whether a Meta/WhatsApp Graph API call actually succeeded.

Meta's own API can answer a request with HTTP 200 and an ``error`` object
in the body -- documented behavior on their side (an expired token, a
disallowed recipient, a rate limit), not a transport failure -- so
``response.is_success`` alone proves only that the HTTP transaction
completed, never that the request it carried was accepted.

Before this existed, every sender here judged success by the HTTP status
alone: a send Meta itself rejected with a 200-wrapped error was recorded as
delivered, the pending-reply queue closed it as answered, and nothing ever
retried it -- a customer's reply silently never arrived, and a scheduled
post or a comment reply "published" that never actually posted. Every one
of those call sites now checks this instead.
"""

from __future__ import annotations

from typing import Any


def graph_call_succeeded(response: Any, payload: dict[str, Any]) -> bool:
    """``response`` is the ``httpx.Response``; ``payload`` is its already
    -parsed JSON body (or ``{}`` for an empty one) -- passed in rather than
    parsed again here, since every caller already needs the body itself for
    its own return value."""
    return bool(response.is_success) and "error" not in payload
