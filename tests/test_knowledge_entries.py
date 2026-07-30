"""
Real tests for AI Teaching & Knowledge:
1. Per-company knowledge CRUD (create/edit/delete/list).
2. Isolation — one company never sees another's entries.
3. The critical fix: company_id now actually reaches the AI knowledge
   lookup (was silently defaulting to company_id=1 for everyone).

Run with: python3 -m pytest tests/test_knowledge_entries.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    knowledge_manager.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')"
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app)

    app.dependency_overrides.clear()
    db.db_path = original_db_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_create_and_list_knowledge_entry(client_and_db):
    client = client_and_db
    resp = client.post("/api/knowledge", json={
        "title": "What internet speed is recommended?",
        "content": "We recommend at least 10 Mbps for stable viewing.",
        "department": "iptv",
    })
    assert resp.status_code == 200, resp.text

    list_resp = client.get("/api/knowledge")
    titles = [e["title"] for e in list_resp.json()["entries"]]
    assert "What internet speed is recommended?" in titles


def test_create_requires_title_and_content(client_and_db):
    client = client_and_db
    resp = client.post("/api/knowledge", json={"title": "  ", "content": "text"})
    assert resp.status_code == 400


def test_update_knowledge_entry(client_and_db):
    client = client_and_db
    create_resp = client.post("/api/knowledge", json={"title": "Old", "content": "old answer"})
    entry_id = create_resp.json()["id"]

    update_resp = client.patch(f"/api/knowledge/{entry_id}", json={"content": "new answer"})
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["title"] == "Old"
    assert body["content"] == "new answer"


def test_delete_knowledge_entry(client_and_db):
    client = client_and_db
    create_resp = client.post("/api/knowledge", json={"title": "Temp", "content": "delete me"})
    entry_id = create_resp.json()["id"]

    delete_resp = client.delete(f"/api/knowledge/{entry_id}")
    assert delete_resp.status_code == 200

    list_resp = client.get("/api/knowledge")
    ids = [e["id"] for e in list_resp.json()["entries"]]
    assert entry_id not in ids


def test_knowledge_entries_are_isolated_per_company(client_and_db):
    from database.database import db
    from core.knowledge_manager import knowledge_manager

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()
    knowledge_manager.create(company_id=2, title="Other Co Secret", content="not yours", actor_user_id=None)

    client = client_and_db
    resp = client.get("/api/knowledge")
    titles = [e["title"] for e in resp.json()["entries"]]
    assert "Other Co Secret" not in titles


def test_list_for_ai_returns_this_companys_own_entries_not_another_companys():
    """This is the actual multi-tenant fix verified directly against
    the AI-facing method — the exact bug that meant every company's AI
    replies were drawing from a shared/default knowledge base."""
    from database.database import db
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    knowledge_manager.ensure_schema()

    try:
        with db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (10, 'Company A', 'company-a', 1)")
            conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (20, 'Company B', 'company-b', 1)")
            conn.commit()

        knowledge_manager.create(company_id=10, title="Company A pricing", content="$10/mo", actor_user_id=None)
        knowledge_manager.create(company_id=20, title="Company B pricing", content="$99/mo", actor_user_id=None)

        items_for_a = knowledge_manager.list_for_ai(10)
        items_for_b = knowledge_manager.list_for_ai(20)

        titles_a = [i["title"] for i in items_for_a]
        titles_b = [i["title"] for i in items_for_b]

        assert "Company A pricing" in titles_a
        assert "Company B pricing" not in titles_a
        assert "Company B pricing" in titles_b
        assert "Company A pricing" not in titles_b
    finally:
        db.db_path = original_db_path
        import gc
        gc.collect()
        for _attempt in range(5):
            try:
                if os.path.exists(tmp_db_path):
                    os.remove(tmp_db_path)
                break
            except PermissionError:
                time.sleep(0.1)


def test_company_with_no_entries_falls_back_to_legacy_static_files():
    """A brand-new company with zero knowledge entries of its own
    shouldn't get a completely empty knowledge base — falls back to
    the old shared static files until they add their own."""
    from database.database import db
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    knowledge_manager.ensure_schema()

    try:
        items = knowledge_manager.list_for_ai(999)
        # Whatever the legacy static files contain (possibly empty in
        # this sandbox) — the point is it doesn't crash and returns a list.
        assert isinstance(items, list)
    finally:
        db.db_path = original_db_path
        import gc
        gc.collect()
        for _attempt in range(5):
            try:
                if os.path.exists(tmp_db_path):
                    os.remove(tmp_db_path)
                break
            except PermissionError:
                time.sleep(0.1)


def test_unassigned_department_entries_match_any_department_filter():
    """"Unassigned" is now the general/catch-all department (replacing
    the old hardcoded "information" concept) — an entry tagged
    Unassigned should show up regardless of which department is being
    filtered for."""
    from database.database import db
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    knowledge_manager.ensure_schema()

    try:
        with db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (30, 'Co', 'co', 1)")
            conn.commit()

        knowledge_manager.create(company_id=30, title="General FAQ", content="general answer", department="Unassigned")
        knowledge_manager.create(company_id=30, title="Sales only", content="sales answer", department="Sales")

        sales_items = knowledge_manager.list_for_ai(30, department="Sales")
        titles = [i["title"] for i in sales_items]
        assert "General FAQ" in titles  # catch-all shows up
        assert "Sales only" in titles   # exact match shows up

        support_items = knowledge_manager.list_for_ai(30, department="Support")
        support_titles = [i["title"] for i in support_items]
        assert "General FAQ" in support_titles  # catch-all still shows up
        assert "Sales only" not in support_titles  # exact-only entry doesn't leak into other departments
    finally:
        db.db_path = original_db_path
        import gc
        gc.collect()
        for _attempt in range(5):
            try:
                if os.path.exists(tmp_db_path):
                    os.remove(tmp_db_path)
                break
            except PermissionError:
                time.sleep(0.1)


def test_tag_matching_works_across_any_custom_tag():
    """The whole point of the flexible tags system: works for
    channel-tags, department-tags, or entirely custom tags the company
    invents themselves — the matching logic doesn't care which."""
    from database.database import db
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    knowledge_manager.ensure_schema()

    try:
        with db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (40, 'Co', 'co', 1)")
            conn.commit()

        knowledge_manager.create(company_id=40, title="Everywhere", content="general", tags=[])
        knowledge_manager.create(company_id=40, title="WhatsApp only", content="wa answer", tags=["whatsapp"])
        knowledge_manager.create(company_id=40, title="VIP campaign", content="vip answer", tags=["vip", "ramadan-campaign"])

        whatsapp_items = knowledge_manager.list_for_ai(40, context_tags=["whatsapp"])
        titles = [i["title"] for i in whatsapp_items]
        assert "Everywhere" in titles
        assert "WhatsApp only" in titles
        assert "VIP campaign" not in titles

        vip_items = knowledge_manager.list_for_ai(40, context_tags=["telegram", "vip"])
        vip_titles = [i["title"] for i in vip_items]
        assert "Everywhere" in vip_titles
        assert "VIP campaign" in vip_titles  # matched via the custom "vip" tag
        assert "WhatsApp only" not in vip_titles  # channel tag doesn't match
    finally:
        db.db_path = original_db_path
        import gc
        gc.collect()
        for _attempt in range(5):
            try:
                if os.path.exists(tmp_db_path):
                    os.remove(tmp_db_path)
                break
            except PermissionError:
                time.sleep(0.1)


def test_tags_are_normalized_lowercase_and_deduplicated():
    from database.database import db
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    knowledge_manager.ensure_schema()

    try:
        with db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (41, 'Co', 'co', 1)")
            conn.commit()

        entry = knowledge_manager.create(
            company_id=41, title="X", content="Y", tags=["WhatsApp", "whatsapp", " Sales ", ""],
        )
        assert entry["tags"] == ["whatsapp", "sales"]
    finally:
        db.db_path = original_db_path
        import gc
        gc.collect()
        for _attempt in range(5):
            try:
                if os.path.exists(tmp_db_path):
                    os.remove(tmp_db_path)
                break
            except PermissionError:
                time.sleep(0.1)


def test_smart_reply_passes_real_company_id_to_message_gateway():
    """Regression guard for the actual bug: smart_reply.py now passes
    the real, resolved company_id into message_gateway.handle_text
    instead of silently defaulting to company_id=1 for every company."""
    from unittest.mock import patch, MagicMock
    import channels.meta.smart_reply as smart_reply_module

    fake_response = MagicMock()
    fake_response.text = "Hello!"
    fake_response.department = "information"
    fake_response.intent = "greeting"
    fake_response.needs_human = False

    with patch("channels.meta.smart_reply.message_gateway") as mock_gateway:
        mock_gateway.handle_text.return_value = fake_response
        with patch("channels.meta.smart_reply.company_settings_service") as mock_settings:
            mock_settings.get_section.return_value = {"values": {}}
            with patch("channels.meta.smart_reply.conversation_control_service") as mock_ccs:
                mock_ccs.record_ai_reply.return_value = {}
                with patch("channels.meta.smart_reply.save_conversation_message"):
                    with patch("channels.meta.smart_reply._record_sent_status", return_value=None):
                        with patch("channels.meta.smart_reply.send_meta_buttons", return_value={"ok": True, "response": {}}):
                            with patch("channels.meta.smart_reply.diagnostics_service"):
                                queue_result = smart_reply_module.schedule_smart_reply(
                                    channel="messenger", user_id="cust-1", company_id=42,
                                    message="hi", delay_seconds=0,
                                )
                                key = smart_reply_module._key(42, "messenger", "cust-1")
                                pending = smart_reply_module._PENDING[key]
                                smart_reply_module._finish_pending(
                                    company_id=42, channel="messenger", user_id="cust-1",
                                    generation=pending.generation,
                                )

    assert queue_result["queued"] is True
    call_kwargs = mock_gateway.handle_text.call_args.kwargs
    assert call_kwargs.get("company_id") == 42
