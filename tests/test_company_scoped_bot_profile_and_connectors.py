"""
Regression tests for the "every company shares one global bot brain" bug:

core/profile_loader.py and core/business_connectors.py used to read a
single static file (config/bot_profile.json, config/business_connectors.json)
shared by every company, ignoring the company-scoped `bot_profiles` and
`business_connectors` DB tables entirely -- so an admin changing AI
behavior / company name / connector config in Company Settings had zero
effect on the live bot for their company.

This file proves two things for both modules:
  (a) a company with no DB-configured row gets exactly today's
      static-file-default behavior (critical regression guard -- nobody
      loses bot functionality just because they haven't configured
      anything in the DB yet).
  (b) a company WITH a DB-configured row gets its own distinct behavior,
      different from another company that has none / has a different row.

Run with: python3 -m pytest tests/test_company_scoped_bot_profile_and_connectors.py -v
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

    Mirrors the pattern used by tests/test_conversation_ownership.py --
    mutating the existing singleton's db_path is the reliable way to
    isolate tests against this codebase's module layout (reimporting
    modules breaks under pytest's assertion-rewrite import hook).
    """
    from pathlib import Path
    from database.database import db

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()  # seeds workspace id=1 + company id=1

    with db.connect() as conn:
        # A second company, fully independent of company id=1, to prove
        # per-company isolation.
        conn.execute(
            """
            INSERT INTO companies (
                id, workspace_id, name, slug, country,
                currency, timezone, default_language, status
            ) VALUES (2, 1, 'Company Two', 'company-two', 'Lebanon',
                      'USD', 'Asia/Beirut', 'ar', 'active')
            """
        )
        conn.commit()

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


# ---------------------------------------------------------------------------
# core/profile_loader.py
# ---------------------------------------------------------------------------

def test_profile_loader_no_db_row_matches_static_file_default(fresh_db):
    """Critical regression guard: a company with nothing in `bot_profiles`
    (including no company_id at all, and company_id=1's default seed row
    which never inserts into bot_profiles) must see exactly the same
    values as before this fix -- straight from config/bot_profile.json."""
    from core.profile_loader import ProfileLoader

    loader = ProfileLoader()

    static_company = loader.get_company()
    static_ai_style = loader.get_ai_style()

    assert loader.get_company(company_id=1) == static_company
    assert loader.get_company(company_id=999) == static_company
    assert loader.get_company(company_id=None) == static_company

    assert loader.get_ai_style(company_id=1) == static_ai_style
    assert loader.get_channel_role("telegram", company_id=1) == (
        loader.get_channel_role("telegram")
    )


def test_profile_loader_uses_db_row_when_configured(fresh_db):
    """A company WITH a bot_profiles row gets its own name/AI style,
    distinct from the static-file default and from another company."""
    from core.profile_loader import ProfileLoader

    with fresh_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO bot_profiles (
                company_id, channel_account_id, name,
                ai_model, ai_reply_mode, system_prompt, status
            ) VALUES (1, NULL, 'Acme Support Bot',
                      'gpt-4o-mini', 'grounded_ai', 'Be extra formal.', 'active')
            """
        )
        conn.commit()

    loader = ProfileLoader()

    configured = loader.get_company(company_id=1)
    default = loader.get_company()  # no company_id -> static default
    other_company = loader.get_company(company_id=2)  # no row for company 2

    assert configured["name"] == "Acme Support Bot"
    assert configured["name"] != default["name"]
    assert other_company == default  # company 2 still gets the safe default

    ai_style = loader.get_ai_style(company_id=1)
    assert ai_style["model"] == "gpt-4o-mini"
    assert ai_style["system_prompt"] == "Be extra formal."
    assert loader.get_ai_style(company_id=2) == loader.get_ai_style()


def test_profile_loader_two_companies_get_distinct_names(fresh_db):
    from core.profile_loader import ProfileLoader

    with fresh_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO bot_profiles (company_id, channel_account_id, name, status)
            VALUES (1, NULL, 'Company One Bot', 'active')
            """
        )
        conn.execute(
            """
            INSERT INTO bot_profiles (company_id, channel_account_id, name, status)
            VALUES (2, NULL, 'Company Two Bot', 'active')
            """
        )
        conn.commit()

    loader = ProfileLoader()

    name_1 = loader.get_company(company_id=1)["name"]
    name_2 = loader.get_company(company_id=2)["name"]

    assert name_1 == "Company One Bot"
    assert name_2 == "Company Two Bot"
    assert name_1 != name_2


def test_prompt_builder_embeds_company_specific_name(fresh_db):
    """End-to-end: build_system_prompt() must reflect the DB-configured
    company name for the company that owns the request, not the shared
    static default, and different companies must get different prompts."""
    from core.profile_loader import ProfileLoader
    import core.prompt_builder as prompt_builder_module

    with fresh_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO bot_profiles (company_id, channel_account_id, name, status)
            VALUES (1, NULL, 'Acme Support Bot', 'active')
            """
        )
        conn.commit()

    # Use a fresh ProfileLoader instance wired the same way the module-level
    # singleton is, so the DB lookups above are visible.
    prompt_builder_module.profile_loader = ProfileLoader()

    prompt_for_company_1 = prompt_builder_module.prompt_builder.build_system_prompt(
        "telegram", company_id=1
    )
    prompt_for_company_2 = prompt_builder_module.prompt_builder.build_system_prompt(
        "telegram", company_id=2
    )
    prompt_with_no_company = prompt_builder_module.prompt_builder.build_system_prompt(
        "telegram"
    )

    assert "Acme Support Bot" in prompt_for_company_1
    assert "Acme Support Bot" not in prompt_for_company_2
    assert "Acme Support Bot" not in prompt_with_no_company
    assert prompt_for_company_1 != prompt_for_company_2


# ---------------------------------------------------------------------------
# core/business_connectors.py
# ---------------------------------------------------------------------------

def test_connectors_no_db_row_matches_static_file_default(fresh_db):
    """Critical regression guard: a company with nothing in
    `business_connectors` must see exactly the static file's enabled/
    disabled flags, same as before this fix."""
    from core.business_connectors import BusinessConnectors

    connectors = BusinessConnectors()
    static_config = connectors.load_config()

    for name in ["products", "accounting", "orders"]:
        expected = bool(static_config.get(name, {}).get("enabled", False))

        assert connectors.is_enabled(name) == expected
        assert connectors.is_enabled(name, company_id=1) == expected
        assert connectors.is_enabled(name, company_id=999) == expected

    # Static default today is "disabled" for every connector -> lookups
    # must report "not connected", not silently succeed.
    assert connectors.get_product_info("iphone", company_id=1)["connected"] is False
    assert connectors.get_customer_balance(company_id=1)["connected"] is False
    assert connectors.get_order_status(company_id=1)["connected"] is False


def test_connectors_uses_db_row_when_configured(fresh_db):
    """A company WITH an active business_connectors row for 'products'
    is treated as enabled, distinct from the static-file default and
    from a company with no row."""
    from core.business_connectors import BusinessConnectors

    with fresh_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO business_connectors (
                company_id, connector_type, provider, name, status
            ) VALUES (1, 'products', 'custom_erp', 'Products Feed', 'active')
            """
        )
        conn.commit()

    connectors = BusinessConnectors()

    assert connectors.is_enabled("products", company_id=1) is True
    assert connectors.is_enabled("products", company_id=2) is False  # no row -> default
    assert connectors.get_product_info("iphone", company_id=1)["connected"] is True
    assert connectors.get_product_info("iphone", company_id=2)["connected"] is False


def test_connectors_inactive_db_row_is_treated_as_disabled(fresh_db):
    from core.business_connectors import BusinessConnectors

    with fresh_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO business_connectors (
                company_id, connector_type, provider, name, status
            ) VALUES (1, 'accounting', 'custom_erp', 'Accounting Feed', 'inactive')
            """
        )
        conn.commit()

    connectors = BusinessConnectors()

    assert connectors.is_enabled("accounting", company_id=1) is False


def test_connectors_two_companies_get_distinct_states(fresh_db):
    from core.business_connectors import BusinessConnectors

    with fresh_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO business_connectors (
                company_id, connector_type, provider, name, status
            ) VALUES (1, 'orders', 'shopify', 'Orders Feed', 'active')
            """
        )
        conn.execute(
            """
            INSERT INTO business_connectors (
                company_id, connector_type, provider, name, status
            ) VALUES (2, 'orders', 'shopify', 'Orders Feed', 'inactive')
            """
        )
        conn.commit()

    connectors = BusinessConnectors()

    assert connectors.is_enabled("orders", company_id=1) is True
    assert connectors.is_enabled("orders", company_id=2) is False


def test_engine_threads_company_id_into_connector_results(fresh_db):
    """End-to-end: Engine.collect_connector_results() must pass the
    request's company_id through to business_connectors, so results
    differ per company for the exact same message."""
    from core.engine import Engine
    from core.business_connectors import BusinessConnectors
    import core.engine as engine_module

    with fresh_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO business_connectors (
                company_id, connector_type, provider, name, status
            ) VALUES (1, 'orders', 'shopify', 'Orders Feed', 'active')
            """
        )
        conn.commit()

    engine_module.business_connectors = BusinessConnectors()
    engine = Engine()

    results_company_1 = engine.collect_connector_results(
        message="wein order status",
        language="en",
        department="orders",
        company_id=1,
    )
    results_company_2 = engine.collect_connector_results(
        message="wein order status",
        language="en",
        department="orders",
        company_id=2,
    )

    order_result_1 = next(r for r in results_company_1 if r["connector"] == "orders")
    order_result_2 = next(r for r in results_company_2 if r["connector"] == "orders")

    assert order_result_1["connected"] is True
    assert order_result_2["connected"] is False
