"""
Regression test: two near-simultaneous first-ever messages from the SAME
brand-new customer identity (a duplicate webhook delivery, or two channels
racing) used to make the loser's upsert_from_channel() raise
sqlite3.IntegrityError (customer_identities' UNIQUE(company_id, channel,
external_user_id) constraint) straight into the webhook handler. Found via
a 10,000-op/100-company mega stress test hitting the real Reply Flow engine
concurrently. Fixed with a retry-once: on conflict, re-run once so the
retry's SELECT finds the winner's now-committed row and takes the UPDATE
path instead of raising.

Run with: python -m pytest tests/test_customer_identity_race.py -v
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1


@pytest.fixture()
def fresh_db():
    from database.database import db
    from backend.services.customer_service import customer_service

    tmp = tempfile.mktemp(suffix="_race.db")
    original = db.db_path
    db.db_path = Path(tmp)
    db.create_tables()
    customer_service.ensure_schema()
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.commit()
    yield
    db.db_path = original
    import gc
    gc.collect()
    try:
        os.remove(tmp)
    except OSError:
        pass


def test_concurrent_first_message_from_same_new_customer_never_raises(fresh_db):
    """Many threads racing to upsert the SAME brand-new (channel, external_user_id)
    identity must all succeed and end up pointing at exactly ONE customer row —
    never an uncaught IntegrityError, never a duplicate customer."""
    from backend.services.customer_service import customer_service

    errors = []
    results = []
    lock = threading.Lock()

    def race():
        try:
            result = customer_service.upsert_from_channel(
                company_id=COMPANY_ID, channel="whatsapp", external_user_id="96170000001",
                display_name="Race Customer",
            )
            with lock:
                results.append(result["id"])
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=race) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "a racing thread did not finish"

    assert not errors, f"upsert_from_channel raised under concurrency: {errors}"
    assert len(results) == 20
    assert len(set(results)) == 1, "the race must converge on exactly one customer row, not duplicates"

    from database.database import db
    with db.connect() as conn:
        identity_count = conn.execute(
            "SELECT COUNT(*) FROM customer_identities WHERE company_id=? AND channel='whatsapp' AND external_user_id='96170000001'",
            (COMPANY_ID,),
        ).fetchone()[0]
        customer_count = conn.execute("SELECT COUNT(*) FROM customers WHERE company_id=?", (COMPANY_ID,)).fetchone()[0]
    assert identity_count == 1
    assert customer_count == 1
