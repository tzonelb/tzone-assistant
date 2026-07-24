"""
Real test for the "Clear shown" fix in backend/services/notification_service.py.

clear_visible() used to hard-DELETE notification rows, which also erased
them from the Notification Center (same underlying table/list). It
should only mark them read, clearing them from the bell's unread list
while keeping them visible in the Notification Center's history.

Run with: python3 -m pytest tests/test_notification_clear_shown.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
USER_ID = 101


@pytest.fixture()
def fresh_db():
    from database.database import db
    from backend.services.notification_service import notification_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    notification_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) "
            "VALUES (?, 'notif_test@test.local', 'Notif Test', 'active')",
            (USER_ID,),
        )
        conn.commit()

    yield notification_service

    db.db_path = original_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_clear_shown_marks_read_instead_of_deleting(fresh_db):
    svc = fresh_db
    notif = svc.create(
        company_id=COMPANY_ID,
        notification_type="new_message",
        title="Test notification",
        recipient_user_id=USER_ID,
    )

    cleared = svc.clear_visible(
        notification_ids=[notif["id"]],
        company_id=COMPANY_ID,
        user_id=USER_ID,
    )
    assert cleared == 1

    # Still exists (this is the actual regression: it used to be gone).
    remaining = svc.list_for_user(company_id=COMPANY_ID, user_id=USER_ID)
    matching = [n for n in remaining if n["id"] == notif["id"]]
    assert len(matching) == 1
    assert matching[0]["is_read"] is True
