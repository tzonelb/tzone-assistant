"""
Regression tests for the knowledge_manager.py company-scoping fix.

Before this fix, core/knowledge_manager.py read one static pair of JSON
files (config/knowledge_base.json, config/training_knowledge.json) shared
by every company, completely ignoring the company-scoped knowledge_items /
knowledge_categories tables that Company Settings already writes to. Every
company's bot shared the exact same "global bot brain" for AI knowledge
lookups regardless of what an admin configured.

This file proves two things:
  (a) a company with no DB-configured knowledge gets EXACTLY the same
      behavior as today's static-file default (critical regression guard —
      nobody's bot should go dark because the DB has no rows for them yet).
  (b) a company WITH DB-configured knowledge gets its own distinct data,
      different from another company's DB-configured data, and different
      from the static-file fallback.

Run with: python3 -m pytest tests/test_knowledge_company_scoping.py -v
"""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Same approach as tests/test_conversation_ownership.py: mutating the
    existing singleton's db_path (rather than reimporting modules) is the
    reliable way to isolate tests against this codebase's module layout.
    """
    from pathlib import Path
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
    status: str = "active",
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items (
                company_id, external_id, title,
                content_ar, content_en, department, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, external_id, title, content_ar, content_en, department, status),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# (a) Backward-compatible default: no company_id, or a company_id with zero
#     DB rows, must behave exactly like the pre-fix static-file-only path.
# ---------------------------------------------------------------------------

def test_no_company_id_is_unchanged_static_behavior(fresh_db):
    from core.knowledge_manager import knowledge_manager

    legacy_items = knowledge_manager.load_items()
    explicit_static_items = knowledge_manager.load_static_items()

    assert legacy_items == explicit_static_items
    assert len(legacy_items) > 0


def test_company_with_no_db_rows_falls_back_to_static_files(fresh_db):
    from core.knowledge_manager import knowledge_manager

    _insert_company(fresh_db, 42, "Empty Co")

    scoped_items = knowledge_manager.load_items(company_id=42)
    static_items = knowledge_manager.load_static_items()

    assert scoped_items == static_items
    assert len(scoped_items) > 0


def test_company_with_no_db_rows_list_for_ai_matches_legacy_call(fresh_db):
    from core.knowledge_manager import knowledge_manager

    _insert_company(fresh_db, 42, "Empty Co")

    legacy = knowledge_manager.list_for_ai(None)
    scoped = knowledge_manager.list_for_ai(None, company_id=42)

    assert legacy == scoped


def test_missing_knowledge_tables_falls_back_safely(fresh_db, monkeypatch):
    """If the DB query fails for any reason (e.g. schema not migrated on
    an old deployment), the company must still get the static fallback
    instead of an empty knowledge base or a crash."""
    from core.knowledge_manager import knowledge_manager

    with fresh_db.connect() as conn:
        conn.execute("DROP TABLE knowledge_items")
        conn.commit()

    scoped_items = knowledge_manager.load_items(company_id=1)
    static_items = knowledge_manager.load_static_items()

    assert scoped_items == static_items


# ---------------------------------------------------------------------------
# (b) A company with DB-configured knowledge gets its own distinct data.
# ---------------------------------------------------------------------------

def test_company_with_db_rows_gets_its_own_configured_knowledge(fresh_db):
    from core.knowledge_manager import knowledge_manager

    _insert_company(fresh_db, 10, "Configured Co")
    _insert_knowledge_item(
        fresh_db,
        company_id=10,
        external_id="warranty_policy",
        title="Warranty Policy",
        content_ar="نص عربي مخصص للشركة 10",
        content_en="Custom warranty text for company 10",
        department="sales",
    )

    scoped_items = knowledge_manager.load_items(company_id=10)
    static_items = knowledge_manager.load_static_items()

    assert scoped_items != static_items
    assert len(scoped_items) == 1
    assert scoped_items[0]["id"] == "warranty_policy"
    assert scoped_items[0]["content_en"] == "Custom warranty text for company 10"
    assert scoped_items[0]["department"] == "sales"


def test_two_companies_get_distinct_configured_knowledge(fresh_db):
    from core.knowledge_manager import knowledge_manager

    _insert_company(fresh_db, 20, "Company A")
    _insert_company(fresh_db, 30, "Company B")

    _insert_knowledge_item(
        fresh_db,
        company_id=20,
        external_id="company_a_hours",
        title="Working Hours",
        content_ar="شركة أ",
        content_en="Company A is open 9-5",
        department="information",
    )
    _insert_knowledge_item(
        fresh_db,
        company_id=30,
        external_id="company_b_hours",
        title="Working Hours",
        content_ar="شركة ب",
        content_en="Company B is open 24/7",
        department="information",
    )

    items_a = knowledge_manager.load_items(company_id=20)
    items_b = knowledge_manager.load_items(company_id=30)

    assert len(items_a) == 1
    assert len(items_b) == 1
    assert items_a[0]["id"] == "company_a_hours"
    assert items_b[0]["id"] == "company_b_hours"
    assert items_a[0]["content_en"] != items_b[0]["content_en"]

    # Admin changes for company A must have zero effect on company B, and
    # vice versa -- this is the exact "one global bot brain" bug being fixed.
    assert items_a != items_b


def test_inactive_and_other_company_items_are_excluded(fresh_db):
    from core.knowledge_manager import knowledge_manager

    _insert_company(fresh_db, 50, "Filtered Co")
    _insert_company(fresh_db, 60, "Other Co")

    _insert_knowledge_item(
        fresh_db,
        company_id=50,
        external_id="active_item",
        title="Active",
        content_ar="نشط",
        content_en="Active item",
        department="sales",
        status="active",
    )
    _insert_knowledge_item(
        fresh_db,
        company_id=50,
        external_id="draft_item",
        title="Draft",
        content_ar="مسودة",
        content_en="Draft item",
        department="sales",
        status="draft",
    )
    _insert_knowledge_item(
        fresh_db,
        company_id=60,
        external_id="other_company_item",
        title="Other",
        content_ar="آخر",
        content_en="Belongs to another company",
        department="sales",
        status="active",
    )

    items = knowledge_manager.load_items(company_id=50)
    ids = {item["id"] for item in items}

    assert ids == {"active_item"}


def test_list_for_ai_department_filtering_still_works_for_db_backed_items(fresh_db):
    from core.knowledge_manager import knowledge_manager

    _insert_company(fresh_db, 70, "Dept Co")
    _insert_knowledge_item(
        fresh_db,
        company_id=70,
        external_id="sales_item",
        title="Sales FAQ",
        content_ar="مبيعات",
        content_en="Sales info",
        department="sales",
    )
    _insert_knowledge_item(
        fresh_db,
        company_id=70,
        external_id="maintenance_item",
        title="Maintenance FAQ",
        content_ar="صيانة",
        content_en="Maintenance info",
        department="maintenance",
    )
    _insert_knowledge_item(
        fresh_db,
        company_id=70,
        external_id="general_item",
        title="General FAQ",
        content_ar="عام",
        content_en="General info",
        department="information",
    )

    sales_only = knowledge_manager.list_for_ai("sales", company_id=70)
    sales_ids = {item["id"] for item in sales_only}

    # "information" department items are always included alongside the
    # requested department (matches the pre-existing static-file behavior).
    assert sales_ids == {"sales_item", "general_item"}


def test_category_department_used_when_item_department_is_null(fresh_db):
    from core.knowledge_manager import knowledge_manager

    _insert_company(fresh_db, 80, "Category Co")

    with fresh_db.connect() as conn:
        conn.execute(
            "INSERT INTO knowledge_categories (company_id, name, department, status) "
            "VALUES (?, ?, ?, 'active')",
            (80, "Repairs", "maintenance"),
        )
        category_id = conn.execute(
            "SELECT id FROM knowledge_categories WHERE company_id = ? AND name = ?",
            (80, "Repairs"),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO knowledge_items (
                company_id, category_id, external_id, title,
                content_ar, content_en, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (80, category_id, "repair_item", "Repair Info", "إصلاح", "Repair info"),
        )
        conn.commit()

    items = knowledge_manager.load_items(company_id=80)

    assert len(items) == 1
    assert items[0]["department"] == "maintenance"
