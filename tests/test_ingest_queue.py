"""
Tests for the fast-ack ingestion queue (burst absorption).

  * disabled by default: submit() returns False so callers process inline
  * enabled: submit() enqueues and a worker runs the callable
  * backpressure: when the queue is full, submit() returns False (caller must
    run inline) — never drops

Run with: python -m pytest tests/test_ingest_queue.py -v
"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _fresh_queue():
    # A fresh instance per test so start() state doesn't leak.
    from core.ingest_queue import IngestQueue
    return IngestQueue()


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr("config.settings.config.INGEST_ASYNC", False)
    q = _fresh_queue()
    q.start()
    assert q.enabled is False
    assert q.submit(lambda: None) is False  # caller must run inline


def test_enabled_processes_in_background(monkeypatch):
    monkeypatch.setattr("config.settings.config.INGEST_ASYNC", True)
    monkeypatch.setattr("config.settings.config.INGEST_QUEUE_MAX", 100)
    monkeypatch.setattr("config.settings.config.INGEST_WORKERS", 2)
    q = _fresh_queue()
    q.start()

    seen = []
    done = threading.Event()

    def work(*, n):
        seen.append(n)
        if len(seen) >= 5:
            done.set()

    for i in range(5):
        assert q.submit(work, n=i) is True

    assert done.wait(timeout=5), "workers did not drain the queue"
    assert sorted(seen) == [0, 1, 2, 3, 4]


def test_backpressure_when_full_returns_false(monkeypatch):
    monkeypatch.setattr("config.settings.config.INGEST_ASYNC", True)
    monkeypatch.setattr("config.settings.config.INGEST_QUEUE_MAX", 2)
    monkeypatch.setattr("config.settings.config.INGEST_WORKERS", 1)
    q = _fresh_queue()
    q.start()

    # Block the single worker so the queue fills and stays full.
    release = threading.Event()

    def blocker(**_):
        release.wait(timeout=5)

    # First item is picked up by the worker (blocks); next two fill the queue
    # (maxsize=2); the fourth must be rejected with False (backpressure).
    assert q.submit(blocker) is True
    time.sleep(0.2)
    results = [q.submit(blocker) for _ in range(4)]
    assert False in results, "a full queue must reject with False, not block/drop"
    release.set()


def test_worker_failure_does_not_kill_pool(monkeypatch):
    monkeypatch.setattr("config.settings.config.INGEST_ASYNC", True)
    monkeypatch.setattr("config.settings.config.INGEST_QUEUE_MAX", 100)
    monkeypatch.setattr("config.settings.config.INGEST_WORKERS", 1)
    q = _fresh_queue()
    q.start()

    ok = threading.Event()

    def boom(**_):
        raise RuntimeError("worker boom")

    def good(**_):
        ok.set()

    q.submit(boom)
    q.submit(good)
    assert ok.wait(timeout=5), "worker died after an exception (pool not resilient)"
