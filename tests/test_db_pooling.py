"""
Tests for the thread-local pooled DB connection (database.py):
  * sequential top-level `with db.connect()` blocks reuse one connection and
    each commits independently
  * a nested `with` shares the connection and defers commit to the outer scope
    (nested atomicity)
  * a rollback() while nested FAILS FAST (never silently discards the outer
    transaction)
  * swapping db_path while a transaction is open on the thread is refused

Run with: python -m pytest tests/test_db_pooling.py -v
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture()
def fresh_db():
    from database.database import db
    tmp = tempfile.mktemp(suffix="_pool.db")
    original = db.db_path
    db.db_path = Path(tmp)
    with db.connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.commit()
    yield db
    db.db_path = original
    import gc
    gc.collect()
    try:
        os.remove(tmp)
    except OSError:
        pass


def test_same_thread_reuses_one_connection(fresh_db):
    db = fresh_db
    a = db.connect()
    b = db.connect()
    assert a is b  # one pooled proxy per thread


def test_sequential_blocks_commit_independently(fresh_db):
    db = fresh_db
    with db.connect() as conn:
        conn.execute("INSERT INTO t (v) VALUES ('a')")
        conn.commit()
    with db.connect() as conn:
        conn.execute("INSERT INTO t (v) VALUES ('b')")
        conn.commit()
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert n == 2


def test_nested_defers_commit_to_outer(fresh_db):
    db = fresh_db
    # Inner block "commits" but it must defer to the outer scope; if the outer
    # raises, the inner write must roll back too (nested atomicity).
    with pytest.raises(ValueError):
        with db.connect() as outer:
            outer.execute("INSERT INTO t (v) VALUES ('outer')")
            with db.connect() as inner:
                inner.execute("INSERT INTO t (v) VALUES ('inner')")
                inner.commit()  # deferred to outer
            raise ValueError("boom")  # outer fails -> everything rolls back
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert n == 0  # neither inner nor outer persisted


def test_nested_rollback_fails_fast(fresh_db):
    db = fresh_db
    with pytest.raises(RuntimeError, match="nested"):
        with db.connect() as outer:
            outer.execute("INSERT INTO t (v) VALUES ('x')")
            with db.connect() as inner:
                inner.rollback()  # must raise, not silently nuke the outer txn


def test_db_path_swap_while_open_is_refused(fresh_db):
    db = fresh_db
    with pytest.raises(RuntimeError, match="db_path changed"):
        with db.connect():
            db.db_path = Path(tempfile.mktemp(suffix="_other.db"))
            db.connect()  # rebind attempt at depth > 0 -> refused
