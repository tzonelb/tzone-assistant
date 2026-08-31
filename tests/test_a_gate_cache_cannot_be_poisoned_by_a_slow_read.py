"""An operator's change must not be undone by a read that started before it.

The three gates — module, subscription, company — cache the control plane for
thirty seconds and read it outside their lock. Reading outside the lock is
deliberate: holding a mutex across a database read would serialise every
company's messages behind one another.

The cost is a window. A thread that reads "active", is descheduled, and only
then stores its answer, stores it *after* an operator changed the row and
dropped the cache. The stale entry then stands for the full window — thirty
seconds in which a suspension is masked, which is exactly the half minute the
operator spends watching the screen.

Dropping the cache on change is not enough on its own, because the drop happens
while the reader is already holding the old answer. `GateCache` carries a
generation counter: a read notes the generation it began under and publishes
its answer only if nothing has invalidated since.

These tests pause a read across an invalidate rather than racing threads and
hoping. A race that reproduces one run in fifty is a test that passes by
accident forty-nine times.
"""

from __future__ import annotations

import threading

import pytest

from backend.services.gate_cache import GateCache


def test_a_fresh_read_is_cached():
    cache = GateCache(ttl_seconds=60)
    calls = []

    def read():
        calls.append(1)
        return "active"

    assert cache.read_through("a", read) == "active"
    assert cache.read_through("a", read) == "active"
    assert len(calls) == 1, "the second call did not come from the cache"


def test_invalidate_forces_the_next_read():
    cache = GateCache(ttl_seconds=60)
    answers = iter(["active", "suspended"])

    def read():
        return next(answers)

    assert cache.read_through("a", read) == "active"

    cache.invalidate("a")

    assert cache.read_through("a", read) == "suspended"


def test_invalidate_without_a_key_drops_everything():
    cache = GateCache(ttl_seconds=60)

    cache.read_through("a", lambda: 1)
    cache.read_through("b", lambda: 2)
    cache.invalidate()

    assert cache.read_through("a", lambda: 99) == 99
    assert cache.read_through("b", lambda: 99) == 99


def test_a_read_in_flight_across_an_invalidate_does_not_publish_its_answer():
    """The invariant this class exists for."""
    cache = GateCache(ttl_seconds=60)

    entered = threading.Event()
    release = threading.Event()

    def slow_read():
        entered.set()
        assert release.wait(timeout=10), "the test deadlocked"
        return "active"

    result = {}

    def reader():
        result["answer"] = cache.read_through("a", slow_read)

    thread = threading.Thread(target=reader)
    thread.start()

    assert entered.wait(timeout=10), "the slow read never started"

    # The operator's change lands while the read above is in flight.
    cache.invalidate("a")

    release.set()
    thread.join(timeout=10)

    assert not thread.is_alive(), "the reader never finished"

    # The in-flight reader still returns what it read. It read before the
    # change, and nothing can alter that.
    assert result["answer"] == "active"

    # But it must not have published it: the next caller reads afresh.
    assert cache.read_through("a", lambda: "suspended") == "suspended", (
        "a read that began before the invalidate published its stale answer, "
        "so the operator's change was masked for the whole cache window"
    )


def test_the_control_shows_the_generation_guard_is_what_does_it():
    """Without an invalidate in the middle, the same slow read *is* published.

    Otherwise the test above would pass on a cache that never stored anything.
    """
    cache = GateCache(ttl_seconds=60)

    entered = threading.Event()
    release = threading.Event()

    def slow_read():
        entered.set()
        assert release.wait(timeout=10), "the test deadlocked"
        return "active"

    def reader():
        cache.read_through("a", slow_read)

    thread = threading.Thread(target=reader)
    thread.start()

    assert entered.wait(timeout=10)
    release.set()
    thread.join(timeout=10)

    assert cache.read_through("a", lambda: "suspended") == "active", (
        "the cache stored nothing at all, so the invalidate test proves nothing"
    )


def test_a_mutable_answer_is_copied_in_and_out():
    """`module_gate` caches a dict of module states.

    Without a copy, a caller that edits what it was handed edits what every
    later caller is told — one company's screen turning a module off for the
    process.
    """
    cache = GateCache(ttl_seconds=60)

    source = {"knowledge": True}
    first = cache.read_through("a", lambda: source, copy=dict)

    first["knowledge"] = False
    source["knowledge"] = False

    second = cache.read_through("a", lambda: {"knowledge": True}, copy=dict)

    assert second == {"knowledge": True}, (
        "the cached answer was mutated through a caller's reference"
    )


def test_an_expired_entry_is_read_again():
    cache = GateCache(ttl_seconds=0)
    calls = []

    def read():
        calls.append(1)
        return "active"

    cache.read_through("a", read)
    cache.read_through("a", read)

    assert len(calls) == 2, "a zero-second entry was served from the cache"


@pytest.mark.parametrize("gate_module, attribute", [
    ("backend.services.company_gate", "company_gate"),
    ("backend.services.subscription_gate", "subscription_gate"),
    ("backend.services.module_gate", "module_gate"),
])
def test_every_gate_uses_the_shared_cache(gate_module, attribute):
    """The behaviour above is worth nothing if a gate keeps its own copy.

    All three had the same hole, because all three had the same hand-written
    cache. This is what stops the fourth one being written the old way.
    """
    import importlib

    gate = getattr(importlib.import_module(gate_module), attribute)

    assert isinstance(getattr(gate, "_cache", None), GateCache), (
        f"{attribute} does not use GateCache, so it does not have the "
        "generation guard"
    )
