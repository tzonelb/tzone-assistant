"""The identity chain a message carries: company, account, department, employee.

The owner's rule is one sentence: *the message arrives bound to the company
code, then inside the company to the channel code, then inside the channel to
the department code, then when an employee takes it, to their code.* Four of
those five links were broken.

* ``channel_account_id`` never left the webhook. ``Request`` carried the field
  and every caller left it ``None``, so by the time the engine ran, a company
  with three Instagram accounts pointed at three departments looked exactly like
  a company with one.
* Six department vocabularies existed in three casings. The model was validated
  against a hardcoded list of nine — one company's sections — so a code a
  company had actually defined came back as ``"unknown"``. The inbox validated
  against eight Title-Case English names that matched none of them. The
  keyword table that guessed a department from the message text listed one
  company's products in Arabic and was applied to every company's customers.
* Nothing the assistant decided was ever written down. The department lived in
  ``core/session.py`` — an in-process dictionary — while the column an employee
  reads was written only by an employee. A customer who chose a section from the
  menu was in that section until the next restart and in nothing afterwards.

Every test below runs against real, freshly provisioned, encrypted per-company
databases. The properties worth proving here are all storage-layer ones —
whether a row carries the right account, whether a choice outlives the process,
whether one company's vocabulary can reach another's customer — and a test that
mocked the database would prove none of them.
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module


CUSTOMER = "customer-dept-1"
OTHER_CUSTOMER = "customer-dept-2"


@pytest.fixture()
def wired(platform, monkeypatch):
    """Point every copy of the ``database_manager`` singleton at the test platform.

    Modules do ``from database.manager import database_manager``, so each holds
    its own reference. Patching only ``database.manager`` would leave half the
    chain talking to the process-wide manager rooted at the real data directory,
    and these tests would pass while exercising nothing. The whole chain is
    imported first: a module imported *after* the sweep would bind the real
    singleton and quietly escape the redirection.
    """
    import backend.api.routes.conversations  # noqa: F401
    import backend.services.business_department_service  # noqa: F401
    import backend.services.channel_account_service  # noqa: F401
    import backend.services.conversation_control_service  # noqa: F401
    import backend.services.message_service  # noqa: F401
    import channels.inbound  # noqa: F401
    import channels.meta.smart_reply  # noqa: F401
    import channels.meta.webhook  # noqa: F401
    import core.ai_router  # noqa: F401
    import core.engine  # noqa: F401
    import core.intent_transition  # noqa: F401
    import core.prompt_builder  # noqa: F401

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

    assert "backend.services.channel_account_service" in rebound
    assert "backend.services.conversation_control_service" in rebound
    assert "backend.services.business_department_service" in rebound

    # The engine's in-memory session store is process-wide and keyed by customer
    # id, so one test's state would otherwise be another test's starting point —
    # and "the choice survived a restart" would be indistinguishable from "the
    # session happened to still be warm".
    from core.session import session as session_store

    session_store.sessions.clear()
    session_store._touched.clear()

    # The profile lookup is an outbound Graph call. Nothing here is about
    # display names, and a test must not depend on the network.
    monkeypatch.setattr(
        "channels.inbound.resolve_meta_profile",
        lambda **_: {},
    )

    from backend.services.business_department_service import (
        business_department_service,
    )
    from backend.services.channel_account_service import channel_account_service
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )

    return {
        "departments": business_department_service,
        "accounts": channel_account_service,
        "conversations": conversation_control_service,
        "manager": manager,
    }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _department(wired, company: dict, code: str, **overrides) -> dict:
    data = {
        "code": code,
        "name_ar": f"{code} بالعربية",
        "name_en": code.replace("_", " ").title(),
        "button_ar": f"زر {code}",
        "button_en": f"{code.replace('_', ' ').title()} button",
        "enabled": True,
    }
    data.update(overrides)

    return wired["departments"].create_department(
        company_id=company["id"], data=data
    )


def _account(
    wired,
    company: dict,
    *,
    channel: str = "instagram",
    routing_id: str,
    department_id: int | None = None,
) -> dict:
    from backend.services.channel_account_service import ROUTING_FIELD

    return wired["accounts"].create_account(
        company_id=company["id"],
        channel=channel,
        name=f"{company['name']} {routing_id}",
        values={
            ROUTING_FIELD[channel]: routing_id,
            "access_token": f"token-{routing_id}",
            "department_id": department_id,
        },
    )


def _deliver(wired, *, channel: str, routing_id: str, sender: str, text: str):
    """Push one message through the real webhook path.

    Entered at ``_process_events`` rather than at the service beneath it: the
    hop being tested is the one that was missing, and it is the webhook that
    knows which account a message arrived on.
    """
    from channels.meta.parser import parse_meta_events
    from channels.meta.webhook import _process_events

    payload = {
        "object": "instagram" if channel == "instagram" else "page",
        "entry": [
            {
                "id": routing_id,
                "messaging": [
                    {
                        "sender": {"id": sender},
                        "recipient": {"id": routing_id},
                        "message": {"mid": f"mid-{sender}-{abs(hash(text))}", "text": text},
                    }
                ],
            }
        ],
    }

    return _process_events(parse_meta_events(payload))


def _conversation(wired, company: dict, channel: str, sender: str) -> dict:
    """Read the row straight out of the company's encrypted database.

    Deliberately not through the service: a test that asked the same code under
    test what it had stored could not tell a persisted value from a computed
    one, and persistence is the whole point here.
    """
    with wired["manager"].tenant(company["id"]) as conn:
        row = conn.execute(
            """
            SELECT * FROM conversations
            WHERE channel = ? AND external_user_id = ?
            """,
            (channel, sender),
        ).fetchone()

    assert row is not None, "no conversation was recorded"
    return dict(row)


# ----------------------------------------------------------------------
# The chain: company → channel account → department
# ----------------------------------------------------------------------


def test_a_message_records_its_company_its_account_and_that_accounts_department(
    wired, alpha
):
    """The whole chain in one message. ``channel_account_id`` stopped at the
    webhook and the department was never persisted at all, so the row a company
    ended up with named only the company."""
    sales = _department(wired, alpha, "sales")
    _account(wired, alpha, routing_id="IG_ALPHA", department_id=sales["id"])

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    row = _conversation(wired, alpha, "instagram", CUSTOMER)

    assert row["company_id"] == alpha["id"]
    assert row["channel_account_id"] is not None
    assert row["department_id"] == sales["id"]
    # The text column is kept in step, and it holds the code — the one
    # vocabulary — not a Title-Case name nothing else matches.
    assert row["department"] == "sales"


def test_two_accounts_of_one_channel_route_to_their_own_departments(wired, alpha):
    """The case the owner described. A company may legitimately connect three
    Instagram accounts; each may feed a different section. With only the company
    surviving the trip from the webhook, all three were indistinguishable and
    every message landed in the same place."""
    sales = _department(wired, alpha, "sales")
    support = _department(wired, alpha, "support")

    _account(wired, alpha, routing_id="IG_SALES", department_id=sales["id"])
    _account(wired, alpha, routing_id="IG_SUPPORT", department_id=support["id"])

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_SALES",
        sender=CUSTOMER,
        text="Do you have this in stock?",
    )
    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_SUPPORT",
        sender=OTHER_CUSTOMER,
        text="My order has not arrived.",
    )

    first = _conversation(wired, alpha, "instagram", CUSTOMER)
    second = _conversation(wired, alpha, "instagram", OTHER_CUSTOMER)

    assert first["department_id"] == sales["id"]
    assert second["department_id"] == support["id"]
    assert first["channel_account_id"] != second["channel_account_id"]


def test_two_accounts_may_share_one_department(wired, alpha):
    """Pointing several accounts at one section is ordinary — a shop with a
    Facebook page and an Instagram account both answered by the same team. The
    relationship is many-to-one and nothing may treat it as exclusive."""
    sales = _department(wired, alpha, "sales")

    _account(wired, alpha, routing_id="IG_ONE", department_id=sales["id"])
    _account(
        wired,
        alpha,
        channel="messenger",
        routing_id="PAGE_ONE",
        department_id=sales["id"],
    )

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ONE",
        sender=CUSTOMER,
        text="Hello",
    )
    _deliver(
        wired,
        channel="messenger",
        routing_id="PAGE_ONE",
        sender=OTHER_CUSTOMER,
        text="Hello",
    )

    assert (
        _conversation(wired, alpha, "instagram", CUSTOMER)["department_id"]
        == sales["id"]
    )
    assert (
        _conversation(wired, alpha, "messenger", OTHER_CUSTOMER)["department_id"]
        == sales["id"]
    )


def test_an_account_pointed_nowhere_leaves_the_conversation_unassigned(wired, alpha):
    """Routing by channel is optional. An account with no department must leave
    the conversation open to the customer's own choice rather than being given
    the first section the company happens to have defined."""
    _department(wired, alpha, "sales")
    _account(wired, alpha, routing_id="IG_NEUTRAL", department_id=None)

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_NEUTRAL",
        sender=CUSTOMER,
        text="Hello",
    )

    row = _conversation(wired, alpha, "instagram", CUSTOMER)

    assert row["department_id"] is None
    assert row["department"] == "Unassigned"
    # The account still reached the row: knowing which account a message
    # arrived on does not depend on that account routing anywhere.
    assert row["channel_account_id"] is not None


def test_an_account_cannot_be_pointed_at_another_companys_department(
    wired, alpha, beta
):
    """The pointer lives in the shared control database and the department lives
    inside one company's encrypted file, so nothing enforces the link for us.
    Ids restart at 1 in every company's database, which makes an unchecked value
    a working cross-tenant reference."""
    from backend.services.channel_account_service import ChannelAccountError

    alpha_sales = _department(wired, alpha, "sales")

    with pytest.raises(ChannelAccountError):
        _account(
            wired,
            beta,
            routing_id="IG_BETA",
            department_id=alpha_sales["id"],
        )


# ----------------------------------------------------------------------
# What the customer chooses
# ----------------------------------------------------------------------


def test_the_customers_menu_choice_overrides_the_accounts_default(wired, alpha):
    """Most specific wins. The account says where a message starts; the customer
    saying "I want Bookings" is the customer telling us where it belongs."""
    from core.engine import engine
    from core.request import Request

    sales = _department(wired, alpha, "sales")
    bookings = _department(wired, alpha, "bookings", button_en="Book a table")

    account = _account(wired, alpha, routing_id="IG_ALPHA", department_id=sales["id"])

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    assert (
        _conversation(wired, alpha, "instagram", CUSTOMER)["department_id"]
        == sales["id"]
    )

    engine.handle_ai(
        Request(
            channel="instagram",
            user_id=CUSTOMER,
            company_id=alpha["id"],
            channel_account_id=account["id"],
            message="Book a table",
        ),
        "en",
        None,
        None,
    )

    row = _conversation(wired, alpha, "instagram", CUSTOMER)

    assert row["department_id"] == bookings["id"]
    assert row["department"] == "bookings"


def test_the_customers_choice_survives_a_restart(wired, alpha, monkeypatch):
    """The defect this exists to pin down. The choice lived in
    ``core/session.py``, which is an in-process dictionary with a six-hour
    eviction — so it was lost on every deploy, and the employee reading the
    inbox never saw it at all. The assertion is deliberately against the stored
    row, because a session that happened to still be warm would hide exactly the
    failure being tested."""
    from core.engine import engine
    from core.request import Request
    from core.session import session as session_store

    _account(wired, alpha, routing_id="IG_ALPHA", department_id=None)
    bookings = _department(wired, alpha, "bookings", button_en="Book a table")

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    engine.handle_ai(
        Request(
            channel="instagram",
            user_id=CUSTOMER,
            company_id=alpha["id"],
            message="Book a table",
        ),
        "en",
        None,
        None,
    )

    # Every trace of the process the choice was made in.
    session_store.sessions.clear()
    session_store._touched.clear()

    row = _conversation(wired, alpha, "instagram", CUSTOMER)

    assert row["department_id"] == bookings["id"]
    assert row["department"] == "bookings"

    # And the engine picks it up again from the row, so the customer is not
    # asked to choose a second time.
    assert (
        engine.stored_department(
            Request(
                channel="instagram",
                user_id=CUSTOMER,
                company_id=alpha["id"],
                message="anything",
            )
        )
        == "bookings"
    )


def test_a_model_guess_never_displaces_a_choice_already_made(wired, alpha):
    """The order is customer choice, then account default, then classification.
    A guess arriving after a decision must lose, or the assistant would quietly
    move a conversation the customer had already placed."""
    from core.engine import engine
    from core.request import Request

    bookings = _department(wired, alpha, "bookings", button_en="Book a table")
    _department(wired, alpha, "sales")
    _account(wired, alpha, routing_id="IG_ALPHA", department_id=None)

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    request = Request(
        channel="instagram",
        user_id=CUSTOMER,
        company_id=alpha["id"],
        message="Book a table",
    )

    engine.remember_department(request, "bookings", source="customer_choice")
    engine.remember_department(
        request,
        "sales",
        source="ai_classification",
        only_if_unassigned=True,
    )

    assert (
        _conversation(wired, alpha, "instagram", CUSTOMER)["department_id"]
        == bookings["id"]
    )


def test_the_engine_records_nothing_for_a_conversation_that_does_not_exist(
    wired, alpha
):
    """The assistant preview runs the whole engine under a synthetic customer id
    and promises to leave nothing behind. Recording a department must update a
    conversation, never conjure one."""
    from core.engine import engine
    from core.request import Request

    _department(wired, alpha, "sales")

    engine.remember_department(
        Request(
            channel="instagram",
            user_id="dry-run:never-a-real-customer",
            company_id=alpha["id"],
            message="Hello",
        ),
        "sales",
        source="customer_choice",
    )

    with wired["manager"].tenant(alpha["id"]) as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM conversations"
        ).fetchone()["total"]

    assert total == 0


# ----------------------------------------------------------------------
# The account reaching the engine
# ----------------------------------------------------------------------


def test_the_account_survives_the_queue_and_reaches_the_engine(wired, alpha, monkeypatch):
    """``handle_text`` did not take a ``channel_account_id``, so
    ``Request.channel_account_id`` was always ``None`` however much the webhook
    knew. The reply is generated seconds later from a queued batch and possibly
    in a different process, so the account has to come back out of storage."""
    seen: dict = {}

    from core.engine import engine
    from gateway.message_gateway import message_gateway

    account = _account(wired, alpha, routing_id="IG_ALPHA", department_id=None)

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    monkeypatch.setattr(
        engine,
        "handle",
        lambda request: seen.update(
            {
                "company_id": request.company_id,
                "channel_account_id": request.channel_account_id,
            }
        ),
    )

    state = wired["conversations"].find_state(
        company_id=alpha["id"],
        channel="instagram",
        external_user_id=CUSTOMER,
    )

    message_gateway.handle_text(
        channel="instagram",
        user_id=CUSTOMER,
        company_id=alpha["id"],
        message="Hello",
        channel_account_id=state["channel_account_id"],
    )

    assert seen["company_id"] == alpha["id"]
    assert seen["channel_account_id"] == account["id"]


# ----------------------------------------------------------------------
# One vocabulary
# ----------------------------------------------------------------------


def test_a_company_defined_code_is_not_coerced_to_unknown(wired, alpha):
    """``AIRouter.DEPARTMENTS`` was a hardcoded list of nine — one company's
    sections. A business that defined ``bookings`` had the model answer
    ``bookings`` and had that answer silently rewritten to ``unknown``, so its
    own vocabulary could never survive the round trip."""
    from core.ai_router import ai_router

    _department(wired, alpha, "bookings")

    normalized = ai_router.normalize_result(
        {"department": "bookings", "reply": "Certainly.", "language": "en"},
        company_id=alpha["id"],
    )

    assert normalized["department"] == "bookings"

    # Capitalisation is not a routing decision.
    assert (
        ai_router.normalize_result(
            {"department": "Bookings", "reply": "Certainly.", "language": "en"},
            company_id=alpha["id"],
        )["department"]
        == "bookings"
    )


def test_one_companys_codes_are_never_valid_for_another(wired, alpha, beta):
    """Vocabularies are per company and must not be pooled. Accepting Alpha's
    section for Beta's customer is the same leak as showing Beta's customer
    Alpha's menu."""
    from core.ai_router import ai_router

    _department(wired, alpha, "bookings")
    _department(wired, beta, "repairs")

    assert (
        ai_router.normalize_result(
            {"department": "bookings", "reply": "x"}, company_id=beta["id"]
        )["department"]
        == "unknown"
    )
    assert (
        ai_router.normalize_result(
            {"department": "repairs", "reply": "x"}, company_id=alpha["id"]
        )["department"]
        == "unknown"
    )

    # And with no company at all there is nothing but the two reserved values.
    assert ai_router.allowed_departments(None) == ["human_support", "unknown"]


def test_the_prompt_contract_names_this_companys_codes(wired, alpha, beta):
    """The contract shown to the model hardcoded the same nine departments while
    the block above it injected the company's real ones into the very same
    prompt. The model was told to route to Bookings and, three lines later, that
    ``department`` had to be one of nine values that did not include it."""
    from core.prompt_builder import prompt_builder

    _department(wired, alpha, "bookings")
    _department(wired, beta, "repairs")

    contract = prompt_builder._required_json(company_id=alpha["id"])

    assert '"department": "bookings|human_support|unknown"' in contract
    assert "repairs" not in contract
    assert "iptv" not in contract

    # A prompt built for nobody names nobody's sections.
    assert (
        '"department": "human_support|unknown"'
        in prompt_builder._required_json()
    )


def test_keyword_detection_uses_the_companys_own_sections_only(wired, alpha, beta):
    """``DEPARTMENT_KEYWORDS`` was a hardcoded Arabic/English table naming one
    company's products, applied to every company on the platform: a clinic's
    patient writing "شاشة" was routed to a maintenance department the clinic
    does not have, and a company that named its section ``bookings`` could never
    be detected at all."""
    from core.intent_transition import intent_transition_manager

    _department(wired, alpha, "bookings", name_en="Bookings", name_ar="الحجوزات")
    _department(wired, beta, "repairs", name_en="Repairs")

    assert (
        intent_transition_manager.detect_department(
            "I need bookings please", company_id=alpha["id"]
        )
        == "bookings"
    )
    assert (
        intent_transition_manager.detect_department(
            "بدي الحجوزات", company_id=alpha["id"]
        )
        == "bookings"
    )

    # Beta's word means nothing to Alpha, and vice versa.
    assert (
        intent_transition_manager.detect_department(
            "I need repairs", company_id=alpha["id"]
        )
        is None
    )

    # No company means no match, rather than a built-in list.
    assert (
        intent_transition_manager.detect_department("I need bookings") is None
    )


def test_no_hardcoded_department_vocabulary_survives_in_the_routing_path(wired):
    """A guard against the list growing back. These names described one business
    and were applied to every business; anything reintroducing them would
    reintroduce the leak without failing anything else."""
    import core.ai_router as ai_router_module
    import core.intent_transition as intent_module
    import core.prompt_builder as prompt_module
    from backend.api.routes import conversations as conversations_route

    assert not hasattr(ai_router_module.AIRouter, "DEPARTMENTS")
    assert not hasattr(intent_module.IntentTransitionManager, "DEPARTMENT_KEYWORDS")
    assert not hasattr(conversations_route, "DEPARTMENTS")

    contract = prompt_module.prompt_builder._required_json()

    for code in ("sales", "iptv", "maintenance", "accounting", "telecom"):
        assert code not in contract


# ----------------------------------------------------------------------
# The inbox
# ----------------------------------------------------------------------


@pytest.fixture()
def inbox(wired, monkeypatch):
    """The inbox routes, with authentication and permission checks stubbed.

    The gate itself is exercised by the suite's auth tests; overriding
    ``get_current_user`` and answering every permission check with yes keeps
    these focused on *which* departments the routes offer and accept.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import conversations as route
    from backend.services.auth_service import get_current_user

    def _build(company_id: int, user_id: int = 7):
        app = FastAPI()
        app.include_router(route.router)
        app.dependency_overrides[get_current_user] = lambda: {
            "id": user_id,
            "is_super_admin": True,
        }

        monkeypatch.setattr(
            route.auth_service, "resolve_company_id", lambda _user: company_id
        )
        monkeypatch.setattr(
            route.auth_service, "has_permission", lambda **_kwargs: True
        )
        monkeypatch.setattr(
            route.auth_service, "company_employees", lambda *a, **k: []
        )
        monkeypatch.setattr(
            route.auth_service, "user_display_names", lambda *a, **k: {}
        )

        return TestClient(app)

    return _build


def test_the_inbox_offers_the_companys_own_departments(wired, inbox, alpha, beta):
    """The list was eight Title-Case English names hardcoded in the route file.
    It was one company's list shown to every company's employees, in a casing
    the assistant never produces — so a conversation the model put in ``sales``
    and one an employee put in ``Sales`` were two different departments to the
    filter."""
    _department(wired, alpha, "bookings", name_en="Bookings")
    _department(wired, beta, "repairs", name_en="Repairs")

    body = inbox(alpha["id"]).get("/conversations/options").json()

    assert body["departments"] == ["Unassigned", "bookings"]
    assert {"code": "bookings", "label": "Bookings"} in body["department_options"]
    assert "repairs" not in body["departments"]

    # And Beta is offered its own, never Alpha's.
    beta_body = inbox(beta["id"]).get("/conversations/options").json()

    assert beta_body["departments"] == ["Unassigned", "repairs"]
    assert "bookings" not in beta_body["departments"]


def test_a_patch_accepts_a_department_the_company_defined(wired, inbox, alpha):
    """Transferring a conversation is the one action this screen exists for. The
    constant it validated against rejected every code the company had actually
    defined."""
    bookings = _department(wired, alpha, "bookings", name_en="Bookings")
    _account(wired, alpha, routing_id="IG_ALPHA", department_id=None)

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    response = inbox(alpha["id"]).patch(
        f"/conversations/instagram/{CUSTOMER}/control",
        json={"department": "bookings"},
    )

    assert response.status_code == 200

    row = _conversation(wired, alpha, "instagram", CUSTOMER)

    # Both columns move together, or the screen and the engine disagree about
    # where the conversation is.
    assert row["department"] == "bookings"
    assert row["department_id"] == bookings["id"]


def test_a_patch_refuses_a_department_the_company_did_not_define(
    wired, inbox, alpha, beta
):
    """Including one that another company on the same platform did define. A
    department id is meaningless outside its own company's database, and a name
    is no better."""
    _department(wired, alpha, "bookings")
    _department(wired, beta, "repairs")
    _account(wired, alpha, routing_id="IG_ALPHA", department_id=None)

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    client = inbox(alpha["id"])

    assert (
        client.patch(
            f"/conversations/instagram/{CUSTOMER}/control",
            json={"department": "repairs"},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/conversations/instagram/{CUSTOMER}/control",
            json={"department": "Sales"},
        ).status_code
        == 422
    )

    assert _conversation(wired, alpha, "instagram", CUSTOMER)["department_id"] is None


def test_clearing_a_department_is_allowed(wired, inbox, alpha):
    """A conversation put in the wrong section has to be able to leave it, and
    "no section" is not a section the company defines."""
    _department(wired, alpha, "bookings")
    _account(wired, alpha, routing_id="IG_ALPHA", department_id=None)

    _deliver(
        wired,
        channel="instagram",
        routing_id="IG_ALPHA",
        sender=CUSTOMER,
        text="Hello",
    )

    client = inbox(alpha["id"])
    client.patch(
        f"/conversations/instagram/{CUSTOMER}/control",
        json={"department": "bookings"},
    )

    assert (
        client.patch(
            f"/conversations/instagram/{CUSTOMER}/control",
            json={"department": "Unassigned"},
        ).status_code
        == 200
    )

    row = _conversation(wired, alpha, "instagram", CUSTOMER)

    assert row["department"] == "Unassigned"
    assert row["department_id"] is None
