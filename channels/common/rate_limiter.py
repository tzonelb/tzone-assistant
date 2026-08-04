"""Minimal in-process sliding-window rate limiter.

Defense-in-depth for public webhook endpoints (Meta / WhatsApp) that sit
in front of unlimited paid AI completions and real outbound messages.
This is intentionally hand-rolled rather than pulling in a dependency
(e.g. slowapi) -- it's a single dict of IP -> deque of recent request
timestamps, checked/pruned on every call. Good enough for a single
uvicorn process; it does NOT share state across multiple worker
processes/instances, so treat it as a secondary safeguard, not the
primary defense (that's the HMAC signature check).
"""

import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Return True if a request identified by key (e.g. client IP)
        is allowed under the current sliding window, recording it if so.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            hits = self._hits[key]

            while hits and hits[0] < cutoff:
                hits.popleft()

            if len(hits) >= self.max_requests:
                return False

            hits.append(now)
            return True


def get_client_ip(request) -> str:
    """Best-effort client IP extraction, honoring a proxy-set
    X-Forwarded-For header (first hop) before falling back to the
    direct socket peer.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    if request.client:
        return request.client.host

    return "unknown"


# Shared limiters for the two public webhook POST endpoints. 60
# requests/minute per source IP is generous for legitimate Meta traffic
# (which typically batches events) while capping abuse from a single
# source.
meta_webhook_rate_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=60.0)
whatsapp_webhook_rate_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=60.0)
