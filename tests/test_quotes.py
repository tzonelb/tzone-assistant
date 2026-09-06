"""Quotes raised from a conversation: real, per-company, and never crossed.

"Create quote" in the chat panel needed somewhere real to land. This covers
the service's arithmetic (a flat amount becomes one line item, and the total
is what it says), the validation that stops an empty or zero-amount quote, and
the isolation that matters most on a multi-tenant platform: one company's
quote is invisible to another's list and get.
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module


@pytest.fixture()
def bound(platform, monkeypatch):
    import backend.services.quote_service as quote_module

    original = manager_module.database_manager
    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
    monkeypatch.setattr(quote_module, "database_manager", test_manager)
    return test_manager


def test_a_flat_amount_becomes_one_line_item(bound, alpha):
    from backend.services.quote_service import quote_service

    quote = quote_service.create(
        company_id=alpha["id"], title="Screen repair", amount=49.99
    )

    assert quote["total"] == 49.99
    assert quote["items"] == [{"name": "Screen repair", "quantity": 1, "price": 49.99}]
    assert quote["status"] == "draft"
    assert quote["currency"] == "USD"


def test_a_zero_or_missing_amount_is_refused(bound, alpha):
    from backend.services.quote_service import QuoteError, quote_service

    with pytest.raises(QuoteError):
        quote_service.create(company_id=alpha["id"], title="Nothing", amount=0)
    with pytest.raises(QuoteError):
        quote_service.create(company_id=alpha["id"], title="Nothing", amount=None)


def test_an_empty_title_is_refused(bound, alpha):
    from backend.services.quote_service import QuoteError, quote_service

    with pytest.raises(QuoteError):
        quote_service.create(company_id=alpha["id"], title="   ", amount=10)


def test_explicit_line_items_are_totalled(bound, alpha):
    from backend.services.quote_service import quote_service

    quote = quote_service.create(
        company_id=alpha["id"],
        title="Repair job",
        items=[
            {"name": "Part", "quantity": 2, "price": 15},
            {"name": "Labour", "quantity": 1, "price": 30},
        ],
    )

    assert quote["total"] == 60.0
    assert len(quote["items"]) == 2


def test_one_companys_quote_is_invisible_to_another(bound, alpha, beta):
    from backend.services.quote_service import quote_service

    quote = quote_service.create(company_id=alpha["id"], title="Alpha quote", amount=10)

    assert quote_service.get(beta["id"], quote["id"]) is None
    assert quote_service.list(beta["id"]) == []
    assert quote_service.list(alpha["id"])[0]["title"] == "Alpha quote"


def test_status_transitions_and_rejects_an_unknown_status(bound, alpha):
    from backend.services.quote_service import QuoteError, quote_service

    quote = quote_service.create(company_id=alpha["id"], title="Q", amount=5)

    updated = quote_service.set_status(
        company_id=alpha["id"], quote_id=quote["id"], status="sent"
    )
    assert updated["status"] == "sent"

    with pytest.raises(QuoteError):
        quote_service.set_status(
            company_id=alpha["id"], quote_id=quote["id"], status="bogus"
        )


def test_listing_filters_by_conversation(bound, alpha):
    from backend.services.quote_service import quote_service

    quote_service.create(company_id=alpha["id"], title="For convo 1", amount=5, conversation_id=1)
    quote_service.create(company_id=alpha["id"], title="For convo 2", amount=5, conversation_id=2)

    only_one = quote_service.list(alpha["id"], conversation_id=1)
    assert len(only_one) == 1
    assert only_one[0]["title"] == "For convo 1"
