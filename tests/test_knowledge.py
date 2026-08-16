"""The per-company knowledge base and the path it takes into the assistant.

Every test here runs the real service against a real, freshly provisioned,
encrypted per-company database from ``tests/conftest.py``. The properties worth
proving — that one company's knowledge never reaches another company's customer,
and that the assistant is fed the knowledge of the company the message actually
belongs to — only exist at the storage layer and in the engine's wiring, so
nothing below is mocked except the outbound OpenAI calls.
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module
from backend.services.knowledge_service import knowledge_service
from core.ai_knowledge_matcher import ai_knowledge_matcher
from core.request import Request


@pytest.fixture()
def service(platform, monkeypatch):
    """Point every copy of the ``database_manager`` singleton at the test platform.

    Services do ``from database.manager import database_manager``, so each module
    holds its *own* reference to the singleton. Patching only
    ``database.manager.database_manager`` would leave the knowledge service
    talking to the process-wide manager rooted at the real data directory, and
    these tests would pass while exercising nothing.
    """
    original = manager_module.database_manager
    manager = platform["manager"]

    # Import the modules under test so they are present in sys.modules and get
    # swept below, whatever import order the rest of the suite happened to use.
    import backend.services.knowledge_service  # noqa: F401
    import core.engine  # noqa: F401

    monkeypatch.setattr(manager_module, "database_manager", manager)

    patched: set[str] = set()
    for name, module in list(sys.modules.items()):
        if module is None or module is manager_module:
            continue
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", manager)
            patched.add(name)

    assert "backend.services.knowledge_service" in patched

    return knowledge_service


def _add(service, company: dict, **overrides) -> dict:
    """Create one knowledge item for a company, with workable defaults."""
    data = {
        "title": "Do you repair laptop screens?",
        "content_ar": "منعمل تصليح شاشات لابتوب.",
        "content_en": "Yes, we repair laptop screens.",
        "department": "maintenance",
        "keywords": "screen, repair",
        "status": "active",
    }
    data.update(overrides)

    return service.create_item(company_id=company["id"], data=data)


# ----------------------------------------------------------------------
# Isolation between companies
# ----------------------------------------------------------------------


def test_one_company_cannot_see_another_companys_knowledge_items(service, alpha, beta):
    """The knowledge base used to be two JSON files shared by the whole platform,
    so every company answered its customers out of one company's knowledge and
    could read it in full. Items now belong to one company and are invisible from
    any other."""
    _add(service, alpha, title="Alpha private pricing rule")

    listed = service.list_items(company_id=beta["id"])

    assert listed["total"] == 0
    assert listed["items"] == []

    # And company A's own database really does hold it, so the emptiness above
    # is isolation rather than a write that silently went nowhere.
    assert service.list_items(company_id=alpha["id"])["total"] == 1


def test_reading_another_companys_item_by_id_returns_nothing(service, alpha, beta):
    """Guessing an id is the cheapest attack there is. The old router took an id
    and read it with no company predicate at all, so company B could fetch
    company A's entry by number."""
    item = _add(service, alpha, title="Alpha internal escalation policy")

    assert service.get_item(company_id=beta["id"], item_id=item["id"]) is None
    assert service.get_item(company_id=alpha["id"], item_id=item["id"]) is not None


def test_writes_and_deletes_never_cross_company_boundaries(service, alpha, beta):
    """The old router's POST and DELETE routes had no authentication and no
    company scope, so anyone could rewrite or erase what another company's
    assistant tells its customers."""
    item = _add(service, alpha, title="Alpha warranty terms")

    assert (
        service.update_item(
            company_id=beta["id"],
            item_id=item["id"],
            values={"title": "Rewritten by another company"},
        )
        is None
    )
    assert service.delete_item(company_id=beta["id"], item_id=item["id"]) is False

    unchanged = service.get_item(company_id=alpha["id"], item_id=item["id"])
    assert unchanged["title"] == "Alpha warranty terms"

    # The owning company can still do both.
    assert service.delete_item(company_id=alpha["id"], item_id=item["id"]) is True


# ----------------------------------------------------------------------
# What reaches the assistant
# ----------------------------------------------------------------------


def test_assistant_matcher_receives_only_the_owning_companys_items(
    service, alpha, beta, monkeypatch
):
    """The engine used to load ``config/knowledge_base.json`` for every message
    regardless of who sent it, so company B's customer was answered from company
    A's knowledge. The matcher must now be handed exactly the knowledge of the
    company the message belongs to."""
    import core.engine as engine_module

    _add(service, alpha, external_id="alpha-only", title="Alpha repair policy")
    _add(service, beta, external_id="beta-only", title="Beta repair policy")

    captured: dict[str, list[dict]] = {}

    def fake_match(*, message, language, items, context=None, max_results=3):
        captured["items"] = items
        return ai_knowledge_matcher.empty_result()

    monkeypatch.setattr(engine_module.ai_knowledge_matcher, "match", fake_match)
    # No network from a test, and no dependency on the deployment's policy file.
    monkeypatch.setattr(engine_module.ai_router, "route", lambda **kwargs: None)
    monkeypatch.setattr(
        engine_module.automation_policy,
        "should_auto_reply_with_ai",
        lambda channel: True,
    )

    for company, expected_id in ((beta, "beta-only"), (alpha, "alpha-only")):
        captured.clear()

        engine_module.engine.handle_ai(
            Request(
                channel="messenger",
                user_id=f"customer-{company['id']}",
                company_id=company["id"],
                message="do you repair laptop screens?",
            ),
            "en",
            None,
            None,
        )

        assert [item["id"] for item in captured["items"]] == [expected_id]


def test_a_message_with_no_company_gets_no_knowledge_at_all():
    """Falling back to the old shared files when a message cannot be attributed
    to a company would quietly restore the leak this replaces. No company means
    no knowledge, and the router's guardrails escalate to a human."""
    import core.engine as engine_module

    request = Request(
        channel="messenger",
        user_id="customer-unknown",
        company_id=None,
        message="what are your prices?",
    )

    assert engine_module.engine.load_company_knowledge(request) == []


def test_an_unreadable_knowledge_database_does_not_break_the_reply(monkeypatch):
    """A knowledge lookup that raises used to propagate out of ``handle_ai`` and
    abandon the customer mid-conversation. A failure here has to degrade to no
    knowledge, not to no reply."""
    import core.engine as engine_module

    def explode(company_id, department=None):
        raise RuntimeError("tenant database is unreadable")

    monkeypatch.setattr(engine_module.knowledge_service, "for_assistant", explode)

    request = Request(
        channel="messenger",
        user_id="customer-1",
        company_id=4242,
        message="what are your prices?",
    )

    assert engine_module.engine.load_company_knowledge(request) == []


def test_the_assistant_only_sees_active_items(service, alpha):
    """Draft and archived entries exist so a company can stage or retire an
    answer. Sending them to the assistant anyway would make the status field a
    lie and let a retired price list keep answering customers."""
    _add(service, alpha, external_id="live", status="active")
    _add(service, alpha, external_id="in-progress", status="draft")
    _add(service, alpha, external_id="retired", status="archived")

    assert [item["id"] for item in service.for_assistant(alpha["id"])] == ["live"]


def test_assistant_items_carry_ids_the_matcher_can_select(service, alpha):
    """The matcher identifies items by a string ``id`` and drops any id it did
    not see in the list it was given. An item shape without that key would make
    every selection silently resolve to nothing, and the assistant would answer
    with no grounding while reporting a match."""
    _add(service, alpha, external_id="delivery-policy", title="Delivery")
    items = service.for_assistant(alpha["id"])

    normalized = ai_knowledge_matcher.normalize_result(
        {
            "matched": True,
            "confidence": 0.9,
            "selected_ids": ["delivery-policy", "not-a-real-id"],
        },
        items,
        max_results=3,
    )

    assert normalized["matched"] is True
    assert normalized["selected_ids"] == ["delivery-policy"]

    selected = ai_knowledge_matcher.select_items(normalized, items)
    assert [item["id"] for item in selected] == ["delivery-policy"]
    assert selected[0]["answer_en"] == "Yes, we repair laptop screens."


def test_an_item_without_an_external_id_still_has_a_stable_assistant_id(
    service, alpha
):
    """Items typed into the screen have no reference id. Emitting a null id would
    make the matcher discard them, so every item that has none is given one
    derived from its row."""
    item = _add(service, alpha, external_id=None)

    assert service.for_assistant(alpha["id"])[0]["id"] == f"item-{item['id']}"


def test_department_filter_keeps_general_information_items(service, alpha):
    """The file-backed loader narrowed to a department but always kept general
    information. Dropping that here would leave the assistant unable to answer
    "what do you sell" once a department had been detected."""
    _add(service, alpha, external_id="sales-item", department="sales")
    _add(service, alpha, external_id="general-item", department="information")
    _add(service, alpha, external_id="iptv-item", department="iptv")

    ids = {item["id"] for item in service.for_assistant(alpha["id"], "sales")}

    assert ids == {"sales-item", "general-item"}


def test_deleting_an_item_removes_it_from_the_assistant(service, alpha):
    """Removing an answer from the screen has to remove it from the replies. A
    cache or a soft delete that kept feeding it to the model would leave a
    withdrawn promise in production with nothing in the interface to explain
    it."""
    item = _add(service, alpha, external_id="old-offer")
    assert service.for_assistant(alpha["id"])

    assert service.delete_item(company_id=alpha["id"], item_id=item["id"]) is True
    assert service.for_assistant(alpha["id"]) == []


# ----------------------------------------------------------------------
# Validation, search and categories
# ----------------------------------------------------------------------


def test_an_item_with_no_content_in_either_language_is_refused(service, alpha):
    """A title-only entry teaches the assistant nothing, but it does occupy a
    slot in every prompt and reads as coverage on the screen. It is refused on
    the way in rather than discovered when a customer gets an empty answer."""
    with pytest.raises(ValueError):
        service.create_item(
            company_id=alpha["id"],
            data={"title": "Refund window", "content_ar": "  ", "content_en": ""},
        )

    item = _add(service, alpha)

    with pytest.raises(ValueError):
        service.update_item(
            company_id=alpha["id"],
            item_id=item["id"],
            values={"content_ar": "", "content_en": ""},
        )


def test_an_unknown_status_is_refused(service, alpha):
    """Statuses decide what the assistant may say. A typo that stored
    ``activee`` would silently take an item out of every reply while the screen
    kept showing it as published."""
    with pytest.raises(ValueError):
        _add(service, alpha, status="publshed")


def test_an_item_cannot_point_at_a_category_that_does_not_exist(service, alpha):
    """``category_id`` is only a soft foreign key here — the column allows any
    integer. An unchecked id produced items that vanished from every
    category-filtered view."""
    with pytest.raises(ValueError):
        _add(service, alpha, category_id=987654)


def test_search_and_department_filters_narrow_the_list(service, alpha):
    """The list screen is unusable without them once a company has a real
    knowledge base, and a filter that is applied in the browser instead of the
    query would page through the wrong rows."""
    _add(service, alpha, title="Warranty on phones", department="sales")
    _add(service, alpha, title="IPTV renewal steps", department="iptv")
    _add(service, alpha, title="Screen repair prices", department="maintenance")

    by_search = service.list_items(company_id=alpha["id"], search="renewal")
    assert [item["title"] for item in by_search["items"]] == ["IPTV renewal steps"]

    by_department = service.list_items(company_id=alpha["id"], department="sales")
    assert by_department["total"] == 1
    assert by_department["items"][0]["title"] == "Warranty on phones"


def test_pagination_reports_the_total_beyond_the_current_page(service, alpha):
    """The pager needs the full count, not the length of the page it was handed.
    Returning the page length made every list claim it had exactly one page."""
    for index in range(5):
        _add(service, alpha, title=f"Item {index}", external_id=f"item-{index}")

    page = service.list_items(company_id=alpha["id"], limit=2, offset=2)

    assert page["total"] == 5
    assert len(page["items"]) == 2


def test_categories_are_per_company_and_names_are_not_reused(service, alpha, beta):
    """Two categories with one name inside a company make the picker ambiguous.
    Across companies the same name has to stay available, because the names are
    ordinary words like "Pricing"."""
    created = service.create_category(company_id=alpha["id"], name="Pricing")
    assert created["item_count"] == 0

    with pytest.raises(ValueError):
        service.create_category(company_id=alpha["id"], name="Pricing")

    # The same name is free for another company, and A's category is not visible
    # from B.
    service.create_category(company_id=beta["id"], name="Pricing")
    assert [row["name"] for row in service.list_categories(company_id=beta["id"])] == [
        "Pricing"
    ]

    _add(service, alpha, category_id=created["id"])
    assert service.list_categories(company_id=alpha["id"])[0]["item_count"] == 1


def test_reimporting_the_same_external_id_updates_instead_of_duplicating(
    service, alpha
):
    """``import-knowledge`` is expected to be run again after the legacy files
    are corrected. Inserting blindly would double the knowledge base on every
    run and hand the model two conflicting answers under one id."""
    first, created = service.upsert_by_external_id(
        company_id=alpha["id"],
        external_id="business_overview",
        data={"title": "What do you sell?", "content_en": "Phones and laptops."},
    )
    assert created is True

    second, created_again = service.upsert_by_external_id(
        company_id=alpha["id"],
        external_id="business_overview",
        data={"title": "What do you sell?", "content_en": "Phones, laptops and IPTV."},
    )

    assert created_again is False
    assert second["id"] == first["id"]
    assert service.list_items(company_id=alpha["id"])["total"] == 1
    assert service.for_assistant(alpha["id"])[0]["answer_en"] == (
        "Phones, laptops and IPTV."
    )
