"""The caching the three gates share, in one place because it is the subtle part.

`module_gate`, `subscription_gate` and `company_gate` answer three different
questions and deliberately live in three files: they drive different words, and
sharing a cache between them would make one of those words wrong. What they do
share is the *shape* of the caching, and that shape has an invariant which is
easy to get wrong and impossible to see going wrong.

All three read the control plane outside their lock, on purpose: holding a
mutex across a database read would serialise every company's messages behind
one another. The cost is a window. A thread that reads "active", is
descheduled, and stores its answer only afterwards, will store it *after* an
operator changed the row and dropped the cache -- and that stale entry then
stands for the whole cache window.

Measured, not argued: with the read paused across the change, a suspension was
masked completely. `set_company_status` dropping the cache was not enough,
because the drop happened while a reader was already holding the old answer.
The half minute it was masked for is exactly the half minute an operator spends
watching the screen after suspending a company.

The fix is a generation counter. A read notes the generation it began under and
stores its answer only if nothing has invalidated since. The in-flight reader
still *returns* its stale answer -- it read before the change, and nothing can
alter that -- but it no longer publishes it to everyone else.

The counter is global to one cache rather than per key. Invalidation is an
operator action and therefore rare, so the cost is an occasional re-read, and a
counter per key would be more state to reason about for no gain.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class GateCache:
    """A read-through cache whose entries expire, and can be dropped safely."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._generation = 0

    def read_through(
        self,
        key: Any,
        read: Callable[[], T],
        *,
        copy: Callable[[T], T] | None = None,
    ) -> T:
        """Return the cached answer for `key`, or read and cache a fresh one.

        `copy` is for gates whose answer is mutable: without it a caller could
        edit the cached object and change what every later caller is told.
        """
        now = time.monotonic()

        with self._lock:
            entry = self._entries.get(key)

            if entry and entry[0] > now:
                return copy(entry[1]) if copy else entry[1]

            generation = self._generation

        answer = read()

        with self._lock:
            # Only publish it if nothing was invalidated while the read was in
            # flight. See the note on the generation counter above: without
            # this, a slow read overwrites the operator's change with the value
            # it was replacing.
            if generation == self._generation:
                self._entries[key] = (
                    now + self._ttl,
                    copy(answer) if copy else answer,
                )

        return answer

    def invalidate(self, key: Any = None) -> None:
        """Drop one entry, or all of them, and discard reads already in flight."""
        with self._lock:
            self._generation += 1

            if key is None:
                self._entries.clear()

                return

            self._entries.pop(key, None)
