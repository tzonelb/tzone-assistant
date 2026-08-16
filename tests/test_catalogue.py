"""The product catalogue and the path a product fact takes into the assistant.

Every test runs the real service against a real, freshly provisioned, encrypted
per-company database from ``tests/conftest.py``. Nothing at the storage layer is
mocked, because the two properties worth proving here only exist there: that one
company's catalogue is invisible to another, and that the price the assistant is
allowed to state comes out of the database of the company whose customer asked.
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module


@pytest.fixture()
def service(platform, monkeypatch):
    """Point every copy of the ``database_manager`` singleton at the test platform.

    Services do ``from database.manager import database_manager``, so each module
    holds its *own* reference to the singleton. Patching only
    ``database.manager.database_manager`` would leave the catalogue service
    talking to the process-wide manager rooted at the real data directory, and
    these tests would pass while exercising nothing at all.
    """
    # Imported before the sweep below: a module that has not been imported yet
    # holds no reference to rebind, and would later import the real singleton.
    import backend.services.catalogue_service  # noqa: F401
    import core.business_connectors  # noqa: F401

    original = manager_module.database_manager
    manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", manager)

    rebound: set[str] = set()
    for name, module in list(sys.modules.items()):
        if module is None or module is manager_module:
            continue
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", manager)
            rebound.add(name)

    assert "backend.services.catalogue_service" in rebound

    from backend.services.catalogue_service import catalogue_service

    return catalogue_service


@pytest.fixture()
def connectors(service):
    """The connector layer, sharing the rebound service above."""
    from core.business_connectors import business_connectors

    return business_connectors


def _add(service, company: dict, **overrides) -> dict:
    """Create one product for a company, with workable defaults."""
    data = {
        "name": "iPhone 15 Pro 256GB",
        "sku": "IP15P-256",
        "brand": "Apple",
        "price": 1099.0,
        "currency": "USD",
        "stock_quantity": 4,
        "in_stock": True,
        "status": "active",
    }
    data.update(overrides)

    return service.create_product(company_id=company["id"], data=data)


# ----------------------------------------------------------------------
# Isolation between companies
# ----------------------------------------------------------------------


def test_a_company_only_lists_its_own_products(service, alpha, beta):
    """Products are per-company stock lists with per-company prices. A catalogue
    screen that showed another company's rows would leak a competitor's pricing
    and let staff edit inventory that is not theirs."""
    _add(service, alpha, name="Alpha Phone", sku="ALPHA-1")
    _add(service, beta, name="Beta Phone", sku="BETA-1")

    alpha_names = [
        item["name"]
        for item in service.list_products(company_id=alpha["id"])["items"]
    ]
    beta_names = [
        item["name"]
        for item in service.list_products(company_id=beta["id"])["items"]
    ]

    assert alpha_names == ["Alpha Phone"]
    assert beta_names == ["Beta Phone"]


def test_a_company_cannot_read_another_companys_product_by_id(service, alpha, beta):
    """Product ids are sequential and guessable, so ownership is checked on the
    fetch rather than assumed from the id in the URL."""
    product = _add(service, alpha, name="Alpha Phone", sku="ALPHA-1")

    assert service.get_product(
        company_id=beta["id"], product_id=product["id"]
    ) is None


def test_a_company_cannot_edit_another_companys_product(service, alpha, beta):
    """Without an ownership check on the update, one company could reprice a
    competitor's catalogue — and the assistant would then quote that price."""
    product = _add(service, alpha, name="Alpha Phone", price=1000.0)

    assert service.update_product(
        company_id=beta["id"],
        product_id=product["id"],
        values={"price": 1.0},
    ) is None

    unchanged = service.get_product(
        company_id=alpha["id"], product_id=product["id"]
    )
    assert unchanged["price"] == 1000.0


def test_a_company_cannot_delete_another_companys_product(service, alpha, beta):
    """Same ownership rule on delete, which would otherwise let one company empty
    another's catalogue."""
    product = _add(service, alpha, name="Alpha Phone")

    assert service.delete_product(
        company_id=beta["id"], product_id=product["id"]
    ) is False
    assert service.get_product(
        company_id=alpha["id"], product_id=product["id"]
    ) is not None


def test_a_company_only_sees_its_own_categories(service, alpha, beta):
    """Category names describe what a business sells and are visible on the
    screen's filter, so they are per-company too."""
    service.create_category(company_id=alpha["id"], name="Phones")
    service.create_category(company_id=beta["id"], name="Laptops")

    assert [item["name"] for item in service.list_categories(company_id=alpha["id"])] == [
        "Phones"
    ]
    assert [item["name"] for item in service.list_categories(company_id=beta["id"])] == [
        "Laptops"
    ]


def test_a_product_cannot_be_filed_under_another_companys_category(
    service, alpha, beta
):
    """Accepting a foreign category id would file a product under a category its
    own company cannot see, hiding the row from the screen that manages it."""
    foreign = service.create_category(company_id=beta["id"], name="Beta Only")

    with pytest.raises(ValueError):
        _add(service, alpha, category_id=foreign["id"])


# ----------------------------------------------------------------------
# The assistant: a product fact, and only this company's
# ----------------------------------------------------------------------


def test_connector_returns_this_companys_price_and_stock(connectors, service, alpha):
    """The whole point of the module. Before this, a price question reached the
    router with no connector fact, so ``apply_guardrails`` forced
    ``needs_human`` and replaced the reply with "I cannot confirm the price".
    A catalogue hit now arrives as ``ok: True`` with the real numbers."""
    _add(service, alpha, name="iPhone 15 Pro", brand="Apple", price=1099.0,
         stock_quantity=4)

    result = connectors.get_product_info(
        "how much is the iPhone 15 Pro?", company_id=alpha["id"]
    )

    assert result["ok"] is True
    assert result["products"][0]["effective_price"] == 1099.0
    assert result["products"][0]["currency"] == "USD"
    assert result["products"][0]["in_stock"] is True
    assert result["products"][0]["stock_quantity"] == 4


def test_connector_never_answers_from_another_companys_catalogue(
    connectors, service, alpha, beta
):
    """Two companies sell the same phone at different prices. A lookup that was
    not scoped to the asking company would quote one company's customer the
    other's price — as a fact the guardrail then refuses to correct."""
    _add(service, alpha, name="iPhone 15 Pro", sku="ALPHA-IP15", price=1099.0)
    _add(service, beta, name="iPhone 15 Pro", sku="BETA-IP15", price=1499.0)

    alpha_result = connectors.get_product_info(
        "price of iPhone 15 Pro", company_id=alpha["id"]
    )
    beta_result = connectors.get_product_info(
        "price of iPhone 15 Pro", company_id=beta["id"]
    )

    assert [item["sku"] for item in alpha_result["products"]] == ["ALPHA-IP15"]
    assert [item["sku"] for item in beta_result["products"]] == ["BETA-IP15"]
    assert alpha_result["products"][0]["price"] == 1099.0
    assert beta_result["products"][0]["price"] == 1499.0


def test_connector_without_a_company_returns_no_facts(connectors, service, alpha):
    """A message that could not be routed to a company has no catalogue to answer
    from. Falling back to "some company's" products is exactly the leak above, so
    the connector reports no fact and the guardrail escalates instead."""
    _add(service, alpha, name="iPhone 15 Pro", price=1099.0)

    result = connectors.get_product_info("price of iPhone 15 Pro")

    assert result["ok"] is False
    assert result["products"] == []


def test_connector_reports_no_fact_for_a_product_the_company_does_not_sell(
    connectors, service, alpha
):
    """A catalogue with rows in it must not make every product question look
    answered. Asking for something not sold has to stay unconfirmed, or the model
    is free to invent a price for it."""
    _add(service, alpha, name="iPhone 15 Pro", brand="Apple")

    result = connectors.get_product_info(
        "do you have the Samsung Galaxy S24?", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert result["connected"] is True
    assert result["products"] == []


def test_connector_reports_no_fact_when_the_price_is_not_published(
    connectors, service, alpha
):
    """A row with a NULL price is not a price fact. Marking it ``ok`` would
    switch off the price guardrail while leaving the model nothing real to quote,
    which is the exact condition the guardrail exists for."""
    _add(service, alpha, name="Gaming Laptop", sku="GL-1", price=None,
         sale_price=None)

    result = connectors.get_product_info(
        "how much is the gaming laptop?", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert result["products"][0]["price_confirmed"] is False


def test_connector_confirms_stock_for_a_product_with_no_price(
    connectors, service, alpha
):
    """Availability is known even when a price is not, so a pure stock question
    about a listed product is still answerable without a human."""
    _add(service, alpha, name="Gaming Laptop", sku="GL-1", price=None,
         in_stock=False)

    result = connectors.get_product_info(
        "is the gaming laptop available?", company_id=alpha["id"]
    )

    assert result["ok"] is True
    assert result["products"][0]["in_stock"] is False


def test_connector_ignores_draft_and_archived_products(connectors, service, alpha):
    """A draft row is something the company has not published yet. Quoting its
    price to a customer is the same mistake as inventing one."""
    _add(service, alpha, name="Unreleased Phone", sku="DRAFT-1", price=999.0,
         status="draft")

    result = connectors.get_product_info(
        "price of the Unreleased Phone", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert result["products"] == []


def test_connector_result_matches_the_shape_the_guardrail_checks(
    connectors, service, alpha
):
    """``core/ai_router.py`` decides whether a price question was answered by
    looking for ``item.get("ok") is True`` in the connector results. A truthy
    value of any other type — 1, "yes" — silently fails that identity check and
    the assistant deflects with real data in hand."""
    from core.ai_router import ai_router

    _add(service, alpha, name="iPhone 15 Pro", price=1099.0)

    result = connectors.get_product_info(
        "how much is the iPhone 15 Pro?", company_id=alpha["id"]
    )

    guarded = ai_router.apply_guardrails(
        result={
            "language": "en",
            "reply": "The iPhone 15 Pro is $1099.",
            "buttons": [],
            "needs_human": False,
            "missing_information": [],
            "notes": "",
        },
        message="how much is the iPhone 15 Pro?",
        knowledge=[],
        connector_results=[result],
    )

    assert guarded["needs_human"] is False
    assert guarded["reply"] == "The iPhone 15 Pro is $1099."


def test_guardrail_still_escalates_without_a_catalogue_hit(connectors, service, alpha):
    """The guardrail this module unblocks must stay armed for everything else:
    with no matching product, a price question is still escalated and any number
    the model wrote is still stripped out of the reply."""
    from core.ai_router import ai_router

    _add(service, alpha, name="iPhone 15 Pro", price=1099.0)

    result = connectors.get_product_info(
        "how much is the Samsung Galaxy S24?", company_id=alpha["id"]
    )

    guarded = ai_router.apply_guardrails(
        result={
            "language": "en",
            "reply": "The Galaxy S24 is $899.",
            "buttons": [],
            "needs_human": False,
            "missing_information": [],
            "notes": "",
        },
        message="how much is the Samsung Galaxy S24?",
        knowledge=[],
        connector_results=[result],
    )

    assert guarded["needs_human"] is True
    assert "$899" not in guarded["reply"]


def test_lookup_matches_arabic_price_questions(connectors, service, alpha):
    """Most customers on these channels write Arabic. A tokenizer that only split
    on ASCII would find nothing and every Arabic price question would escalate."""
    _add(service, alpha, name="آيفون 15 برو", sku="IP15P", price=1099.0)

    result = connectors.get_product_info(
        "قديش سعر آيفون 15 برو؟", company_id=alpha["id"]
    )

    assert result["ok"] is True
    assert result["products"][0]["sku"] == "IP15P"


def test_lookup_is_not_triggered_by_filler_words_alone(connectors, service, alpha):
    """"Do you have anything available?" names no product. Matching on those
    words would return an arbitrary catalogue row and the assistant would answer
    about a product the customer never asked about."""
    _add(service, alpha, name="iPhone 15 Pro", price=1099.0)

    result = connectors.get_product_info(
        "hello, do you have anything available?", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert result["products"] == []


def test_best_match_is_ranked_first(connectors, service, alpha):
    """A shop stocks many phones of one brand. Returning them in insertion order
    would put an unrelated Samsung ahead of the exact model asked for, and the
    assistant quotes the first fact it is given."""
    _add(service, alpha, name="Samsung Galaxy A54", sku="SGA54", brand="Samsung",
         price=299.0)
    _add(service, alpha, name="Samsung Galaxy S24 Ultra", sku="SGS24U",
         brand="Samsung", price=1299.0)

    result = connectors.get_product_info(
        "price of Samsung Galaxy S24 Ultra", company_id=alpha["id"]
    )

    assert result["products"][0]["sku"] == "SGS24U"


def test_sale_price_is_the_price_the_assistant_states(connectors, service, alpha):
    """A product on offer has two prices in the row. Quoting the pre-sale one
    tells the customer a number the shop will not honour."""
    _add(service, alpha, name="iPhone 15 Pro", price=1099.0, sale_price=949.0)

    result = connectors.get_product_info(
        "how much is the iPhone 15 Pro?", company_id=alpha["id"]
    )

    assert result["products"][0]["effective_price"] == 949.0


def test_assistant_facts_carry_no_internal_columns(connectors, service, alpha):
    """Only what a customer may be told goes into the prompt. Internal bookkeeping
    reaching the model is one paraphrase away from reaching the customer."""
    _add(service, alpha, name="iPhone 15 Pro", price=1099.0)

    fact = connectors.get_product_info(
        "price of iPhone 15 Pro", company_id=alpha["id"]
    )["products"][0]

    assert "company_id" not in fact
    assert "created_at" not in fact
    assert "updated_at" not in fact
    assert "status" not in fact


# ----------------------------------------------------------------------
# Search, filters and validation on the screen
# ----------------------------------------------------------------------


def test_search_matches_name_sku_and_brand(service, alpha):
    """Staff look products up by whichever identifier is in front of them — the
    label on the box, the code in the system, or the maker."""
    _add(service, alpha, name="Galaxy A54", sku="SGA54", brand="Samsung")
    _add(service, alpha, name="iPhone 15", sku="IP15", brand="Apple")

    by_name = service.list_products(company_id=alpha["id"], search="Galaxy")
    by_sku = service.list_products(company_id=alpha["id"], search="IP15")
    by_brand = service.list_products(company_id=alpha["id"], search="Samsung")

    assert [item["sku"] for item in by_name["items"]] == ["SGA54"]
    assert [item["sku"] for item in by_sku["items"]] == ["IP15"]
    assert [item["sku"] for item in by_brand["items"]] == ["SGA54"]


def test_category_and_stock_filters_narrow_the_list(service, alpha):
    """The screen's filters have to filter. A filter that is accepted and ignored
    tells staff a category is empty when it is not."""
    phones = service.create_category(company_id=alpha["id"], name="Phones")

    _add(service, alpha, name="In Stock Phone", sku="A", category_id=phones["id"],
         in_stock=True)
    _add(service, alpha, name="Sold Out Phone", sku="B", category_id=phones["id"],
         in_stock=False)
    _add(service, alpha, name="Uncategorised", sku="C")

    in_category = service.list_products(
        company_id=alpha["id"], category_id=phones["id"]
    )
    out_of_stock = service.list_products(
        company_id=alpha["id"], stock="out_of_stock"
    )

    assert sorted(item["sku"] for item in in_category["items"]) == ["A", "B"]
    assert [item["sku"] for item in out_of_stock["items"]] == ["B"]


def test_pagination_reports_the_full_total(service, alpha):
    """The pager needs the number of matching rows, not the number returned. A
    total equal to the page size hides every product past the first page."""
    for index in range(5):
        _add(service, alpha, name=f"Product {index}", sku=f"SKU-{index}")

    page = service.list_products(company_id=alpha["id"], limit=2, offset=0)

    assert len(page["items"]) == 2
    assert page["total"] == 5


def test_duplicate_sku_is_refused_within_a_company(service, alpha):
    """A SKU identifies one product. Two rows sharing one would make the lookup's
    answer depend on row order — including the price it quotes."""
    _add(service, alpha, sku="DUP-1")

    with pytest.raises(ValueError):
        _add(service, alpha, name="Different product", sku="DUP-1")


def test_the_same_sku_is_free_in_another_company(service, alpha, beta):
    """SKUs are a company's own numbering. One company claiming "A-100" must not
    stop every other company on the platform from using it."""
    _add(service, alpha, sku="A-100")

    assert _add(service, beta, sku="A-100")["sku"] == "A-100"


def test_a_product_needs_a_name(service, alpha):
    """A nameless row cannot be found by staff or matched for a customer, so it
    is silently dead stock."""
    with pytest.raises(ValueError):
        service.create_product(company_id=alpha["id"], data={"name": "   "})


def test_negative_price_is_refused(service, alpha):
    """A negative price would be handed to a customer as a verified fact."""
    with pytest.raises(ValueError):
        _add(service, alpha, price=-5)


def test_partial_update_keeps_the_untouched_fields(service, alpha):
    """The form sends only what changed. Treating absent fields as blank would
    wipe a product's price the moment somebody fixed a typo in its name."""
    product = _add(service, alpha, name="Typo Phone", price=500.0, brand="Apple")

    updated = service.update_product(
        company_id=alpha["id"],
        product_id=product["id"],
        values={"name": "Fixed Phone"},
    )

    assert updated["name"] == "Fixed Phone"
    assert updated["price"] == 500.0
    assert updated["brand"] == "Apple"


def test_duplicate_category_name_is_refused(service, alpha):
    """Two categories with one name make the screen's filter ambiguous, and the
    table's own uniqueness constraint would raise a raw database error instead of
    a message the screen can show."""
    service.create_category(company_id=alpha["id"], name="Phones")

    with pytest.raises(ValueError):
        service.create_category(company_id=alpha["id"], name="Phones")


def test_deleting_a_category_keeps_its_products(service, alpha):
    """Tidying up the category list must not delete stock. Those rows are what
    the assistant quotes prices from."""
    category = service.create_category(company_id=alpha["id"], name="Phones")
    product = _add(service, alpha, category_id=category["id"])

    assert service.delete_category(
        company_id=alpha["id"], category_id=category["id"]
    ) is True

    survivor = service.get_product(
        company_id=alpha["id"], product_id=product["id"]
    )
    assert survivor is not None
    assert survivor["category_id"] is None
