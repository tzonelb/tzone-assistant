"""Real, end-to-end tests for the shared root cause behind the
automation_policy and knowledge_manager per-company fixes:

    core/request.py's Request defaults company_id=1 in its constructor.
    gateway/message_gateway.py's MessageGateway.handle_text had (until the
    Meta inbound-routing fix) no company_id parameter at all, so every
    Request it built silently used the literal default 1 -- even
    channels/meta/smart_reply.py, which had already correctly resolved the
    real company_id earlier in its own call chain, dropped it before
    calling handle_text. This meant company_id never actually reached
    core/engine.py's handle_ai() with a real, non-default value in any live
    message path, defeating the purpose of both the automation_policy and
    knowledge_manager per-company wiring.

Unlike tests/test_automation_policy_company_scope.py and
tests/test_knowledge_company_scoping.py (which mostly call
automation_policy / knowledge_manager directly, or build a Request()
object by hand), every test in this file drives the REAL pipeline entry
point a channel actually calls:

    gateway.message_gateway.message_gateway.handle_text(company_id=...)
        -> core.request.Request(company_id=...)
        -> core.engine.engine.handle(request)
        -> core.engine.Engine.handle_ai(request, ...)
        -> core.knowledge_manager.knowledge_manager.list_for_ai(company_id=...)

This is exactly the gap the earlier failed review flagged as missing.

Run with: python3 -m pytest tests/test_company_id_pipeline.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import engine  # noqa: E402
from core.knowledge_manager import knowledge_manager  # noqa: E402
from core.session import session  # noqa: E402
from gateway.message_gateway import message_gateway  # noqa: E402


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Same approach as tests/test_knowledge_company_scoping.py /
    tests/test_automation_policy_company_scope.py: mutating the existing
    singleton's db_path (rather than reimporting modules) is the reliable
    way to isolate tests against this codebase's module layout.
    """
    from database.database import db

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()

    yield db

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


def _insert_company(db, company_id: int, name: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO companies (
                id, workspace_id, name, slug, status
            ) VALUES (?, 1, ?, ?, 'active')
            """,
            (company_id, name, f"slug-{company_id}"),
        )
        conn.commit()


def _insert_knowledge_item(
    db,
    company_id: int,
    external_id: str,
    title: str,
    content_ar: str,
    content_en: str,
    department: str = "sales",
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items (
                company_id, external_id, title,
                content_ar, content_en, department, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (company_id, external_id, title, content_ar, content_en, department),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Part 1.4: prove company_id actually reaches Engine.handle_ai() through the
# real pipeline, not just via a hand-built Request().
# ---------------------------------------------------------------------------


def test_handle_text_company_id_reaches_engine_handle_ai(fresh_db, monkeypatch):
    """The single most important test in this repair: a company_id passed
    into MessageGateway.handle_text must reach core/engine.py's
    Engine.handle_ai() as request.company_id, unchanged, via the REAL
    pipeline (gateway.handle_text -> Request -> engine.handle ->
    handle_ai) -- not a hand-constructed Request() and not a direct call
    to handle_ai()."""
    db = fresh_db
    _insert_company(db, 2, "Second Co")
    session.sessions.pop("plumbing_test_user", None)

    seen_company_ids = []
    original_handle_ai = engine.handle_ai

    def _spy_handle_ai(request, *args, **kwargs):
        seen_company_ids.append(request.company_id)
        return original_handle_ai(request, *args, **kwargs)

    monkeypatch.setattr(engine, "handle_ai", _spy_handle_ai)

    response = message_gateway.handle_text(
        channel="website_chat",
        user_id="plumbing_test_user",
        message="hi",
        company_id=2,
    )

    assert seen_company_ids == [2]
    assert response is not None


def test_handle_text_without_company_id_keeps_request_default(fresh_db, monkeypatch):
    """Regression guard: channels that don't pass company_id yet (WhatsApp,
    Telegram, the test_whatsapp route today) must be completely
    unaffected -- Request's own default (company_id=1) still applies
    exactly as before this plumbing existed."""
    session.sessions.pop("plumbing_test_default_user", None)

    seen_company_ids = []
    original_handle_ai = engine.handle_ai

    def _spy_handle_ai(request, *args, **kwargs):
        seen_company_ids.append(request.company_id)
        return original_handle_ai(request, *args, **kwargs)

    monkeypatch.setattr(engine, "handle_ai", _spy_handle_ai)

    message_gateway.handle_text(
        channel="website_chat",
        user_id="plumbing_test_default_user",
        message="hi",
    )

    assert seen_company_ids == [1]


def test_handle_text_company_id_two_distinct_companies_never_cross(fresh_db, monkeypatch):
    """Two different real company_ids sent through the same gateway method
    in sequence must each reach handle_ai with their own value -- no
    leakage/caching across calls."""
    db = fresh_db
    _insert_company(db, 5, "Company Five")
    _insert_company(db, 7, "Company Seven")
    session.sessions.pop("plumbing_multi_user_a", None)
    session.sessions.pop("plumbing_multi_user_b", None)

    seen_company_ids = []
    original_handle_ai = engine.handle_ai

    def _spy_handle_ai(request, *args, **kwargs):
        seen_company_ids.append(request.company_id)
        return original_handle_ai(request, *args, **kwargs)

    monkeypatch.setattr(engine, "handle_ai", _spy_handle_ai)

    message_gateway.handle_text(
        channel="website_chat", user_id="plumbing_multi_user_a", message="hi", company_id=5
    )
    message_gateway.handle_text(
        channel="website_chat", user_id="plumbing_multi_user_b", message="hi", company_id=7
    )

    assert seen_company_ids == [5, 7]


# ---------------------------------------------------------------------------
# Part 3: re-verify knowledge_manager's company-scoped lookup is actually
# exercised with a real, non-default company_id through the FULL live
# pipeline (not just direct load_items/list_for_ai calls in isolation).
# ---------------------------------------------------------------------------


def test_full_pipeline_knowledge_lookup_uses_real_company_id(fresh_db, monkeypatch):
    """End-to-end proof that core/knowledge_manager.py's company-scoped
    lookup gets exercised with a real, non-default company_id when driven
    through the actual message pipeline: message_gateway.handle_text(...,
    company_id=90) -> Request -> engine.handle -> handle_ai ->
    knowledge_manager.list_for_ai(company_id=90). The company's own
    DB-configured knowledge item must come back, not the shared static
    files."""
    db = fresh_db
    _insert_company(db, 90, "Pipeline Co")
    _insert_knowledge_item(
        db,
        company_id=90,
        external_id="pipeline_item",
        title="Pipeline Item",
        content_ar="بند مخصص لشركة الأنابيب",
        content_en="Pipeline-specific stock answer",
        department="sales",
    )

    session.sessions.pop("pipeline_test_user", None)

    seen_calls = []
    original_list_for_ai = knowledge_manager.list_for_ai

    def _spy_list_for_ai(department=None, company_id=None):
        items = original_list_for_ai(department, company_id=company_id)
        seen_calls.append((company_id, [item["id"] for item in items]))
        return items

    monkeypatch.setattr(knowledge_manager, "list_for_ai", _spy_list_for_ai)

    message_gateway.handle_text(
        channel="website_chat",
        user_id="pipeline_test_user",
        message="Do you have iPhone 15 in stock?",
        company_id=90,
    )

    assert len(seen_calls) == 1
    called_company_id, item_ids = seen_calls[0]
    assert called_company_id == 90
    assert item_ids == ["pipeline_item"]


def test_full_pipeline_knowledge_lookup_distinct_per_company(fresh_db, monkeypatch):
    """Two companies with their own DB-configured knowledge, driven through
    the same real pipeline in sequence, must each see only their own
    knowledge item -- never the other company's and never a mix."""
    db = fresh_db
    _insert_company(db, 91, "Pipeline Co A")
    _insert_company(db, 92, "Pipeline Co B")
    _insert_knowledge_item(
        db,
        company_id=91,
        external_id="pipeline_item_a",
        title="A Item",
        content_ar="أ",
        content_en="Company A stock answer",
        department="sales",
    )
    _insert_knowledge_item(
        db,
        company_id=92,
        external_id="pipeline_item_b",
        title="B Item",
        content_ar="ب",
        content_en="Company B stock answer",
        department="sales",
    )

    session.sessions.pop("pipeline_test_user_a", None)
    session.sessions.pop("pipeline_test_user_b", None)

    seen_calls = []
    original_list_for_ai = knowledge_manager.list_for_ai

    def _spy_list_for_ai(department=None, company_id=None):
        items = original_list_for_ai(department, company_id=company_id)
        seen_calls.append((company_id, [item["id"] for item in items]))
        return items

    monkeypatch.setattr(knowledge_manager, "list_for_ai", _spy_list_for_ai)

    message_gateway.handle_text(
        channel="website_chat",
        user_id="pipeline_test_user_a",
        message="Do you have iPhone 15 in stock?",
        company_id=91,
    )
    message_gateway.handle_text(
        channel="website_chat",
        user_id="pipeline_test_user_b",
        message="Do you have iPhone 15 in stock?",
        company_id=92,
    )

    assert seen_calls == [
        (91, ["pipeline_item_a"]),
        (92, ["pipeline_item_b"]),
    ]


def test_full_pipeline_unconfigured_company_falls_back_to_static_knowledge(
    fresh_db, monkeypatch
):
    """Regression guard: a real, non-default company_id with zero
    DB-configured knowledge must still reach knowledge_manager.list_for_ai
    with its own company_id (proving the plumbing is real) while getting
    the same static-file fallback as before this integration existed."""
    db = fresh_db
    _insert_company(db, 93, "Unconfigured Pipeline Co")

    session.sessions.pop("pipeline_test_user_unconfigured", None)

    seen_calls = []
    original_list_for_ai = knowledge_manager.list_for_ai

    def _spy_list_for_ai(department=None, company_id=None):
        items = original_list_for_ai(department, company_id=company_id)
        seen_calls.append((company_id, items))
        return items

    monkeypatch.setattr(knowledge_manager, "list_for_ai", _spy_list_for_ai)

    message_gateway.handle_text(
        channel="website_chat",
        user_id="pipeline_test_user_unconfigured",
        message="Do you have iPhone 15 in stock?",
        company_id=93,
    )

    assert len(seen_calls) == 1
    called_company_id, items = seen_calls[0]
    assert called_company_id == 93

    # engine.py calls list_for_ai(None, company_id=...) -- no department
    # filter -- so an unconfigured company must get back exactly the same
    # static items as the pre-existing, company-agnostic behavior.
    static_items = knowledge_manager.load_static_items()
    assert items == static_items
