import pytest


class _InertTimer:
    """Drop-in replacement for threading.Timer that never actually
    starts a background thread. Tests only need to verify that a reply
    was *scheduled* (queued: True) — they don't need it to fire on its
    own, and having it fire autonomously is exactly what caused
    cross-test flakiness (a timer from test A firing during test B's
    execution window, against test B's torn-down/different database).
    """

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = True
        self._started = False
        self._cancelled = False

    def start(self):
        self._started = True

    def cancel(self):
        self._cancelled = True

    def is_alive(self):
        return self._started and not self._cancelled


@pytest.fixture(autouse=True)
def _no_real_background_timers(monkeypatch):
    """Applies to every test in the suite automatically."""
    monkeypatch.setattr("channels.meta.smart_reply.Timer", _InertTimer)
    yield
    try:
        from channels.meta.smart_reply import cancel_all_pending
        cancel_all_pending()
    except ImportError:
        pass
