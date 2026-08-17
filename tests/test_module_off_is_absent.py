"""Tests for what a switched-off module actually does.

The owner's rule: **a module that is off is off as if it had never been
installed.** Not hidden from the team while it goes on working — absent.

Before this, the switch was enforced in exactly one place: a FastAPI dependency
on the customer routers. That closed the API and left the half a customer sees
wide open. A company that turned Catalogue off got the screen hidden from its
own team and an assistant that went on quoting prices out of the catalogue
behind it — from rows nobody on that team could open to correct. Tasks off
still opened tickets into a table the team could not read. Knowledge off still
answered out of the base.

So the switch said off and the behaviour stayed on. These tests are the
difference, one per module, asserted on the reply path rather than on the HTTP
layer that was already covered.

The last test in the file is the one that matters most in production: when the
control plane cannot be read, every module reports **on**. A blip in one
database must never silently strip a thousand companies of their knowledge and
their catalogue mid-conversation.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    """Bind every already-imported module to this test's database manager."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep below, not after. The sweep rebinds every
    # module that already holds the real manager; a module imported later
    # binds the real one at import time and is missed. That failure is not
    # loud — the module simply reads an empty production database, so a test
    # asserting "nothing was stored" passes for the wrong reason.
    import backend.services.module_gate  # noqa: F401
    import backend.services.platform_service  # noqa: F401
    import channels.inbound  # noqa: F401
    import channels.meta.webhook  # noqa: F401
    import channels.post_publisher  # noqa: F401
    import core.business_connectors  # noqa: F401
    import core.engine  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    # Asserted rather than assumed: without this, a rename or a moved import
    # turns every test in this file into one that cannot fail.
    for required in (
        "backend.services.platform_service",
        "channels.meta.webhook",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.module_gate import module_gate

    # The gate caches by company id, and every test in this file provisions its
    # companies with the same ids. A stale entry from the previous test would
    # make these pass or fail for the wrong reason.
    module_gate.invalidate()
    yield module_gate
    module_gate.invalidate()


def _switch(company, key: str, on: bool) -> None:
    from backend.services.platform_service import platform_service

    platform_service.update_platform_config(company["id"], modules={key: on})


class _Request:
    """The shape the engine reads off an inbound message."""

    def __init__(self, company_id: int, user_id: str = "cust-1"):
        self.company_id = company_id
        self.user_id = user_id
        self.channel = "messenger"
        self.message = "how much is the blue one"
        self.channel_account_id = None


# --------------------------------------------------------------------- gate


def test_a_module_is_on_until_somebody_turns_it_off(wired, alpha):
    """Absent from the stored config means on. Defaulting to off would mean a
    release that adds a module silently disables it for every existing
    company."""
    assert wired.enabled(alpha["id"], "catalogue") is True
    assert wired.enabled(alpha["id"], "knowledge") is True


def test_turning_one_module_off_leaves_the_others_alone(wired, alpha):
    _switch(alpha, "catalogue", False)

    assert wired.enabled(alpha["id"], "catalogue") is False
    assert wired.enabled(alpha["id"], "knowledge") is True


def test_one_company_switch_does_not_reach_another_company(wired, alpha, beta):
    _switch(alpha, "catalogue", False)

    assert wired.enabled(alpha["id"], "catalogue") is False
    assert wired.enabled(beta["id"], "catalogue") is True, (
        "one company's switch changed another company's platform"
    )


def test_a_switch_applies_to_the_next_message_not_the_next_minute(wired, alpha):
    """The gate caches per company. A write has to drop that cache, or an
    operator would flip a switch, watch the screen change, and see the
    assistant keep using the module — and conclude the switch is broken."""
    assert wired.enabled(alpha["id"], "catalogue") is True  # populates the cache

    _switch(alpha, "catalogue", False)

    assert wired.enabled(alpha["id"], "catalogue") is False


def test_turning_a_module_back_on_restores_it(wired, alpha):
    _switch(alpha, "catalogue", False)
    assert wired.enabled(alpha["id"], "catalogue") is False

    _switch(alpha, "catalogue", True)
    assert wired.enabled(alpha["id"], "catalogue") is True


def test_an_unknown_module_key_is_refused_rather_than_guessed(wired, alpha):
    """A typo must not read as "off" — that would remove a feature nobody
    switched off — nor as "on", which would leave one that cannot be switched
    off at all."""
    from backend.services.module_gate import UnknownModule

    with pytest.raises(UnknownModule):
        wired.enabled(alpha["id"], "catalog")  # missing the "ue"


def test_the_http_layer_and_the_assistant_read_the_same_switch(wired, alpha):
    """Two sources of truth would eventually disagree, and the disagreement
    would be invisible: the screen off and the assistant on."""
    from backend.services import module_access

    _switch(alpha, "knowledge", False)

    assert module_access.module_enabled(alpha["id"], "knowledge") is False
    assert wired.enabled(alpha["id"], "knowledge") is False


# ------------------------------------------------------------ the reply path


def _seed_knowledge(platform, company) -> None:
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(company["id"]) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items (
                company_id, title, content_ar, content_en, status,
                created_at, updated_at
            )
            VALUES (?, 'Opening hours', 'من 9 لـ 6', 'Nine to six', 'active', ?, ?)
            """,
            (company["id"], now, now),
        )
        conn.commit()


def _seed_product(platform, company) -> None:
    from backend.services.catalogue_service import catalogue_service

    catalogue_service.create_product(
        company_id=company["id"],
        data={
            "name": "Blue widget",
            "price": 25,
            "status": "active",
            "in_stock": True,
        },
    )


def test_knowledge_on_reads_the_base(wired, platform, alpha):
    """Asserted first, and against seeded data.

    Without this the "off" test below would pass on an empty company — an empty
    list means nothing unless a full one is reachable in the same setup.
    """
    from core.engine import Engine

    _seed_knowledge(platform, alpha)

    assert Engine().load_company_knowledge(_Request(alpha["id"])) != []


def test_knowledge_off_means_the_assistant_has_no_knowledge(wired, platform, alpha):
    from core.engine import Engine

    _seed_knowledge(platform, alpha)
    _switch(alpha, "knowledge", False)

    assert Engine().load_company_knowledge(_Request(alpha["id"])) == []


def test_catalogue_on_quotes_a_seeded_product(wired, platform, alpha):
    from core.business_connectors import business_connectors

    _seed_product(platform, alpha)

    result = business_connectors.get_product_info(
        "how much is the blue widget", company_id=alpha["id"]
    )

    assert result["ok"] is True, "the catalogue was not consulted while switched on"


def test_catalogue_off_means_no_product_facts(wired, platform, alpha):
    from core.business_connectors import business_connectors

    _seed_product(platform, alpha)
    _switch(alpha, "catalogue", False)

    result = business_connectors.get_product_info(
        "how much is the blue widget", company_id=alpha["id"]
    )

    assert result["ok"] is False, "the assistant was handed a price to quote"


def test_catalogue_off_does_not_open_the_company_database(wired, alpha, monkeypatch):
    """Off means the module is not consulted at all, not that its answer is
    discarded afterwards.

    Recorded rather than raised: `get_product_info` wraps the lookup in a broad
    `except Exception`, which swallows an `AssertionError` and makes a failing
    assertion look like a passing test. A flag cannot be swallowed.
    """
    import core.business_connectors as connectors_module

    calls = []

    monkeypatch.setattr(
        connectors_module.catalogue_service,
        "search_for_assistant",
        lambda **kwargs: calls.append(kwargs) or [],
        raising=True,
    )

    _switch(alpha, "catalogue", False)

    connectors_module.business_connectors.get_product_info(
        "how much is the blue widget", company_id=alpha["id"]
    )

    assert calls == [], "the catalogue was read for a company that turned it off"


def test_catalogue_on_is_still_consulted(wired, alpha, monkeypatch):
    import core.business_connectors as connectors_module

    calls = []

    monkeypatch.setattr(
        connectors_module.catalogue_service,
        "search_for_assistant",
        lambda **kwargs: calls.append(kwargs) or [],
        raising=True,
    )

    connectors_module.business_connectors.get_product_info(
        "how much is the blue widget", company_id=alpha["id"]
    )

    assert [call["company_id"] for call in calls] == [alpha["id"]]


def test_tasks_off_opens_no_ticket(wired, alpha, monkeypatch):
    """A ticket written into a module the team cannot open buries the
    customer's problem — worse than not recording it, because the flow tells
    the customer a ticket exists."""
    import core.engine as engine_module

    def explode(*args, **kwargs):
        raise AssertionError("a ticket was opened for a company with Tasks off")

    monkeypatch.setattr(engine_module.ticket_service, "create", explode, raising=True)

    _switch(alpha, "tasks", False)

    engine_module.Engine().create_ticket(_Request(alpha["id"]))


def test_tasks_on_still_opens_a_ticket(wired, alpha, monkeypatch):
    import core.engine as engine_module

    seen = {}

    def fake(*, company_id, data):
        seen["company_id"] = company_id
        return 7

    monkeypatch.setattr(engine_module.ticket_service, "create", fake, raising=True)

    engine_module.Engine().create_ticket(_Request(alpha["id"]))

    assert seen["company_id"] == alpha["id"]


def test_notifications_off_writes_no_bell_entry(wired, alpha, monkeypatch):
    import channels.inbound as inbound_module

    def explode(*args, **kwargs):
        raise AssertionError("a notification was written with the module off")

    monkeypatch.setattr(
        inbound_module.notification_service, "create", explode, raising=True
    )

    _switch(alpha, "notifications", False)

    inbound_module._notify(
        company_id=alpha["id"],
        notification_type="customer_message",
        title="New message",
        body="hello",
    )


def test_notifications_on_still_writes_one(wired, alpha, monkeypatch):
    import channels.inbound as inbound_module

    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        inbound_module.notification_service, "create", fake, raising=True
    )

    inbound_module._notify(
        company_id=alpha["id"],
        notification_type="customer_message",
        title="New message",
        body="hello",
    )

    assert seen["company_id"] == alpha["id"]
    assert seen["title"] == "New message"


def test_scheduler_off_publishes_nothing_and_claims_nothing(
    wired, alpha, monkeypatch
):
    """The one gate whose consequence is public. A post going out to a
    company's followers from a module its team can no longer open — and cannot
    cancel from inside the platform — is the company posting to its own
    audience with no operator.

    Nothing may be claimed either, or the queue would be consumed and the posts
    lost if the module comes back on.
    """
    import channels.post_publisher as publisher_module

    def explode(*args, **kwargs):
        raise AssertionError("the scheduler queue was claimed with the module off")

    monkeypatch.setattr(
        publisher_module.scheduler_service, "claim_due", explode, raising=True
    )

    _switch(alpha, "scheduler", False)

    assert publisher_module.publish_due_posts(alpha["id"]) == 0


def test_scheduler_on_still_claims_its_queue(wired, alpha, monkeypatch):
    import channels.post_publisher as publisher_module

    seen = {}

    def fake(company_id):
        seen["company_id"] = company_id
        return []

    monkeypatch.setattr(
        publisher_module.scheduler_service, "claim_due", fake, raising=True
    )

    publisher_module.publish_due_posts(alpha["id"])

    assert seen["company_id"] == alpha["id"]


def _comment_event(page_id: str) -> dict:
    return {
        "channel": "messenger",
        "page_id": page_id,
        "comment_id": "c-1",
        "message": "is this still available?",
        "post_id": "p-1",
        "author_external_id": "u-1",
        "author_name": "A Customer",
    }


def _connect_page(platform, company, page_id: str) -> None:
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id, external_account_id,
                status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'Page', ?, ?, 'active', ?, ?)
            """,
            (company["id"], page_id, page_id, now, now),
        )
        conn.commit()


def test_comments_off_stores_nothing(wired, platform, alpha, monkeypatch):
    """A comment stored into a module the team cannot open is an unanswered
    customer nobody can see. The rows would pile up invisibly while the company
    believed it had switched comment handling off."""
    import channels.meta.webhook as webhook_module

    _connect_page(platform, alpha, "PAGE_OFF")

    calls = []
    monkeypatch.setattr(
        webhook_module.comment_service,
        "record_incoming",
        lambda **kwargs: calls.append(kwargs) or {"duplicate": False, "id": 1},
        raising=True,
    )

    _switch(alpha, "comments", False)

    results = webhook_module._process_comments([_comment_event("PAGE_OFF")])

    assert calls == [], "a comment was stored with the module off"
    assert results[0]["reason"] == "module_disabled"


def test_comments_on_still_stores(wired, platform, alpha, monkeypatch):
    import channels.meta.webhook as webhook_module

    _connect_page(platform, alpha, "PAGE_ON")

    calls = []
    monkeypatch.setattr(
        webhook_module.comment_service,
        "record_incoming",
        lambda **kwargs: calls.append(kwargs) or {"duplicate": False, "id": 1},
        raising=True,
    )

    webhook_module._process_comments([_comment_event("PAGE_ON")])

    assert [call["company_id"] for call in calls] == [alpha["id"]]


# ------------------------------------------------------------------ failure


def test_an_unreadable_control_plane_leaves_every_module_on(
    wired, alpha, monkeypatch
):
    """The most important test here.

    Failing closed would mean one database blip silently strips every company's
    assistant of its knowledge and its catalogue mid-conversation — a thousand
    companies answering customers worse, with nothing in the switch to explain
    it. Late is better than wrong.
    """
    from database.manager import DatabaseError

    import backend.services.module_gate as gate_module

    _switch(alpha, "catalogue", False)
    assert wired.enabled(alpha["id"], "catalogue") is False

    wired.invalidate()

    def explode(company_id):
        raise DatabaseError("control plane is unavailable")

    monkeypatch.setattr(
        gate_module.platform_service, "get_platform_config", explode, raising=True
    )

    assert wired.enabled(alpha["id"], "catalogue") is True
    assert wired.enabled(alpha["id"], "knowledge") is True


def test_a_message_with_no_company_does_not_crash_the_gate(wired):
    """The engine calls this with whatever is on the request. A message that
    lost its company must degrade, not raise inside a reply."""
    assert wired.enabled(None, "catalogue") is True
    assert wired.states(None)["catalogue"] is True
