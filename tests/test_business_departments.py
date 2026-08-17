"""Per-company business departments, and the greeting that goes with them.

The defect these tests pin down is a single-tenant one that survived into a
multi-tenant platform. Three pieces of the reply engine read shared files on
disk and sent one company's identity to every company's customers:

* ``core/response_policy.py`` defaulted every welcome to "Welcome to T-ZONE 💙".
* ``core/engine.py`` hardcoded that same greeting in the main menu.
* ``core/business_modules.py`` served ``config/business_modules.json`` — one
  company's departments — as the menu, the buttons and the prompt's department
  list for everybody.

So a customer messaging Beta Corp's page was greeted as T-ZONE and offered
T-ZONE's IPTV menu. Everything below runs against real, freshly provisioned,
encrypted per-company databases from ``tests/conftest.py``, because the property
worth proving — that one company's menu never reaches another company's
customer — only exists at the storage layer and in the engine's wiring.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import database.manager as manager_module
from core.request import Request


@pytest.fixture()
def service(platform, monkeypatch):
    """Point every copy of the ``database_manager`` singleton at the test platform.

    Services do ``from database.manager import database_manager``, so each module
    holds its *own* reference. Patching only ``database.manager`` would leave the
    department service talking to the process-wide manager rooted at the real
    data directory, and these tests would pass while exercising nothing.

    The whole reply chain is imported first: a module imported *after* the sweep
    would bind the real singleton and quietly escape the redirection.
    """
    import backend.api.routes.ai_teaching  # noqa: F401
    import backend.services.bot_profile_service  # noqa: F401
    import backend.services.business_department_service  # noqa: F401
    import core.business_modules  # noqa: F401
    import core.engine  # noqa: F401
    import core.prompt_builder  # noqa: F401
    import core.response_policy  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound: set[str] = set()
    for name, module in list(sys.modules.items()):
        if module is None or module is manager_module:
            continue
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.add(name)

    assert "backend.services.business_department_service" in rebound
    assert "backend.services.bot_profile_service" in rebound

    from backend.services.business_department_service import (
        business_department_service,
    )

    return business_department_service


def _add(service, company: dict, code: str, **overrides) -> dict:
    data = {
        "code": code,
        "name_ar": f"{code} بالعربية",
        "name_en": code.title(),
        "button_ar": f"زر {code}",
        "button_en": f"{code.title()} button",
        "enabled": True,
    }
    data.update(overrides)

    return service.create_department(company_id=company["id"], data=data)


def _clear_welcome(company: dict) -> None:
    """A company that never wrote a welcome message.

    The profile service seeds a generic one on first read, so "no welcome" has
    to be stated rather than assumed.
    """
    from backend.services.bot_profile_service import bot_profile_service

    bot_profile_service.update_default(
        company_id=company["id"],
        values={"welcome_message_ar": "", "welcome_message_en": ""},
    )


# ----------------------------------------------------------------------
# Isolation between companies
# ----------------------------------------------------------------------


def test_each_company_gets_only_its_own_menu_buttons_and_overview(
    service, alpha, beta
):
    """The whole defect in one test. The menu came from a shared JSON file, so
    every company offered every other company's sections. A company's menu,
    quick-reply buttons and "available sections" sentence must contain its own
    departments and nothing from anybody else."""
    from core.business_modules import business_modules
    from core.engine import engine

    _add(service, alpha, "sales", name_en="Alpha Sales", button_en="Alpha products")
    _add(service, beta, "bookings", name_en="Beta Bookings", button_en="Beta booking")

    alpha_menu = engine.build_main_menu_response("en", company_id=alpha["id"])
    beta_menu = engine.build_main_menu_response("en", company_id=beta["id"])

    assert "Alpha Sales" in alpha_menu.text
    assert "Beta Bookings" not in alpha_menu.text
    assert "Alpha products" in alpha_menu.buttons
    assert "Beta booking" not in alpha_menu.buttons

    assert "Beta Bookings" in beta_menu.text
    assert "Alpha Sales" not in beta_menu.text
    assert "Beta booking" in beta_menu.buttons
    assert "Alpha products" not in beta_menu.buttons

    alpha_overview = business_modules.overview_text(alpha["id"], "en")
    beta_overview = business_modules.overview_text(beta["id"], "en")

    assert "Alpha Sales" in alpha_overview
    assert "Beta Bookings" not in alpha_overview
    assert "Beta Bookings" in beta_overview
    assert "Alpha Sales" not in beta_overview


def test_a_button_press_resolves_only_against_the_pressing_companys_menu(
    service, alpha, beta
):
    """Buttons are matched by their label. Matching against the shared list meant
    a label another company invented would route this company's customer into a
    department it does not have."""
    from core.business_modules import business_modules

    _add(service, alpha, "sales", button_en="Alpha products")
    _add(service, beta, "bookings", button_en="Beta booking")

    assert (
        business_modules.get_module_by_button(alpha["id"], "Alpha products", "en")[
            "id"
        ]
        == "sales"
    )
    assert (
        business_modules.get_module_by_button(alpha["id"], "Beta booking", "en")
        is None
    )


def test_reading_or_writing_another_companys_department_does_nothing(
    service, alpha, beta
):
    """Ids restart at 1 inside every company's database, so the same small id is
    valid in both. Ownership has to be part of the WHERE clause rather than
    trusted from the caller."""
    department = _add(service, alpha, "sales")

    assert (
        service.get_department(
            company_id=beta["id"], department_id=department["id"]
        )
        is None
    )
    assert (
        service.update_department(
            company_id=beta["id"],
            department_id=department["id"],
            values={"name_en": "Rewritten by another company"},
        )
        is None
    )
    assert (
        service.delete_department(
            company_id=beta["id"], department_id=department["id"]
        )
        is False
    )

    unchanged = service.get_department(
        company_id=alpha["id"], department_id=department["id"]
    )
    assert unchanged["name_en"] == "Sales"


# ----------------------------------------------------------------------
# A company that has defined nothing
# ----------------------------------------------------------------------


def test_a_company_with_no_departments_gets_no_menu_at_all(service, alpha, beta):
    """This is where a fallback would silently restore the leak. A company that
    has defined no sections must be shown none, not the platform's built-in
    list and not the sections of whichever company happens to have some."""
    from core.engine import engine

    _add(service, beta, "iptv", name_en="IPTV", button_en="IPTV")

    for language, sections_label in (("en", "Available sections"), ("ar", "الأقسام")):
        menu = engine.build_main_menu_response(language, company_id=alpha["id"])

        assert sections_label not in menu.text
        assert "IPTV" not in menu.text
        assert "IPTV" not in menu.buttons

        # A reply still has to say something, and what it says names nobody.
        assert menu.text.strip()
        assert menu.buttons == [engine.SUPPORT_BUTTON[language]]


def test_a_message_with_no_company_is_offered_no_menu(service, alpha):
    """A message that could not be attributed to a company must not be answered
    out of some other company's menu — that is the leak, not a fallback."""
    from core.business_modules import business_modules
    from core.engine import engine

    _add(service, alpha, "sales", name_en="Alpha Sales", button_en="Alpha products")

    assert business_modules.overview_text(None, "en") == ""
    assert business_modules.buttons(None, "en") == []

    menu = engine.build_main_menu_response("en", company_id=None)

    assert "Alpha Sales" not in menu.text
    assert "Alpha products" not in menu.buttons


def test_a_disabled_department_leaves_the_menu(service, alpha):
    """Switching a section off is how a company retires it without losing what it
    typed. A disabled row that still reached the customer would make the toggle
    a lie."""
    from core.business_modules import business_modules

    department = _add(service, alpha, "iptv", name_en="IPTV", button_en="IPTV")

    assert business_modules.buttons(alpha["id"], "en") == ["IPTV"]

    service.update_department(
        company_id=alpha["id"],
        department_id=department["id"],
        values={"enabled": False},
    )

    assert business_modules.buttons(alpha["id"], "en") == []
    assert business_modules.overview_text(alpha["id"], "en") == ""

    # Still editable on the screen, which is the point of disabling rather than
    # deleting.
    assert len(service.list_departments(company_id=alpha["id"])) == 1


def test_the_menu_follows_the_order_the_company_chose(service, alpha):
    """The order is what a customer reads top to bottom. Leaving it to whatever
    the database returned made the menu reshuffle itself between deploys."""
    first = _add(service, alpha, "sales", name_en="Sales")
    second = _add(service, alpha, "support", name_en="Support")
    third = _add(service, alpha, "billing", name_en="Billing")

    from core.business_modules import business_modules

    assert business_modules.overview_text(alpha["id"], "en") == (
        "Available sections: Sales, Support, Billing."
    )

    service.reorder(
        company_id=alpha["id"],
        department_ids=[third["id"], first["id"], second["id"]],
    )

    assert business_modules.overview_text(alpha["id"], "en") == (
        "Available sections: Billing, Sales, Support."
    )


# ----------------------------------------------------------------------
# The welcome message
# ----------------------------------------------------------------------


def test_a_company_with_a_welcome_message_gets_its_own(service, alpha, beta):
    """The greeting used to be one string in a shared file, so every business on
    the platform introduced itself as the same company."""
    from backend.services.bot_profile_service import bot_profile_service
    from core.engine import engine
    from core.response_policy import response_policy

    bot_profile_service.update_default(
        company_id=alpha["id"],
        values={
            "welcome_enabled": True,
            "welcome_message_en": "Welcome to Alpha Corp.",
        },
    )
    bot_profile_service.update_default(
        company_id=beta["id"],
        values={
            "welcome_enabled": True,
            "welcome_message_en": "Welcome to Beta Corp.",
        },
    )

    session_state: dict = {}
    reply, _ = response_policy.compose_reply(
        channel="messenger",
        user_session=session_state,
        ai_result={"language": "en", "reply": "We open at nine."},
        company_id=alpha["id"],
    )

    assert reply.startswith("Welcome to Alpha Corp.")
    assert "Beta Corp" not in reply
    assert session_state["welcome_sent"] is True

    menu = engine.build_main_menu_response("en", company_id=alpha["id"])
    assert menu.text.startswith("Welcome to Alpha Corp.")


def test_a_company_without_a_welcome_message_gets_no_greeting(service, alpha):
    """Prefixing a reply with a greeting a business never wrote is inventing
    content in its name. Silence is the honest default, not a generic
    stand-in."""
    from core.response_policy import response_policy

    _clear_welcome(alpha)

    session_state: dict = {}
    reply, _ = response_policy.compose_reply(
        channel="messenger",
        user_session=session_state,
        ai_result={"language": "en", "reply": "We open at nine."},
        company_id=alpha["id"],
    )

    assert reply == "We open at nine."
    assert "welcome_sent" not in session_state


def test_a_reply_with_no_company_carries_no_greeting(service):
    """No company means no welcome to send. Falling back to a shared default is
    exactly how the T-ZONE greeting reached every other business's customers."""
    from core.response_policy import response_policy

    reply, _ = response_policy.compose_reply(
        channel="messenger",
        user_session={},
        ai_result={"language": "ar", "reply": "منفتح الساعة تسعة."},
        company_id=None,
    )

    assert reply == "منفتح الساعة تسعة."


def test_no_shipped_default_carries_a_company_name(service):
    """The greeting used to live in ``DEFAULT_POLICY`` and in the shared policy
    file, both of which are read by every company. Nothing customer-facing may
    live there again."""
    from core.response_policy import response_policy

    shipped = json.dumps(
        [response_policy.DEFAULT_POLICY, response_policy.get_channel_policy("messenger")],
        ensure_ascii=False,
    )

    assert "T-ZONE" not in shipped
    assert "welcome_message_ar" not in shipped
    assert "welcome_message_en" not in shipped


def test_a_company_that_switched_its_welcome_off_is_not_given_one(service, alpha):
    """``welcome_enabled`` is a decision, not a hint. Sending the stored text
    anyway would make the switch on the screen do nothing."""
    from backend.services.bot_profile_service import bot_profile_service
    from core.response_policy import response_policy

    bot_profile_service.update_default(
        company_id=alpha["id"],
        values={
            "welcome_enabled": False,
            "welcome_message_en": "Welcome to Alpha Corp.",
        },
    )

    assert (
        response_policy.get_welcome_message(
            "messenger", "en", company_id=alpha["id"]
        )
        == ""
    )


# ----------------------------------------------------------------------
# What reaches the model
# ----------------------------------------------------------------------


SHARED_JSON_DEPARTMENT_NAMES = ("Sales", "Accounting", "Maintenance", "Information")


def test_the_neutral_prompt_names_no_company_and_no_shared_departments(
    service, alpha
):
    """The neutral prompt tells the model it has no company identity and must not
    invent one. It then used to hand it ``config/bot_profile.json`` — a company
    name, a channel role and one company's department list — which contradicted
    the instruction in the same breath."""
    from core.prompt_builder import prompt_builder

    _add(service, alpha, "sales", name_en="Alpha Sales")

    text = prompt_builder.build_system_prompt("messenger")

    assert "T-ZONE" not in text
    assert "Alpha Corp" not in text
    assert "Alpha Sales" not in text
    assert "channel_role" not in text
    assert "business_modules" not in text
    assert "business_departments" not in text

    for name in SHARED_JSON_DEPARTMENT_NAMES:
        assert name not in text

    # Still a usable prompt rather than an empty one.
    assert "Required JSON" in text


def test_the_company_prompt_carries_that_companys_departments_only(
    service, alpha, beta
):
    """The model is told which sections it may route to. Reading them from a
    shared file meant every company's assistant was briefed on one company's
    business."""
    from core.prompt_builder import prompt_builder

    _add(service, alpha, "sales", name_en="Alpha Sales")
    _add(service, beta, "bookings", name_en="Beta Bookings")

    alpha_prompt = prompt_builder.build_system_prompt(
        "messenger", company_id=alpha["id"]
    )
    beta_prompt = prompt_builder.build_system_prompt(
        "messenger", company_id=beta["id"]
    )

    assert "Alpha Sales" in alpha_prompt
    assert "Beta Bookings" not in alpha_prompt

    assert "Beta Bookings" in beta_prompt
    assert "Alpha Sales" not in beta_prompt

    assert "T-ZONE" not in alpha_prompt
    assert "channel_role" not in alpha_prompt


def test_a_disabled_department_is_not_offered_to_the_model(service, alpha):
    """A section the company switched off must not be somewhere the assistant can
    still route a customer."""
    from core.prompt_builder import prompt_builder

    department = _add(service, alpha, "sales", name_en="Alpha Sales")
    service.update_department(
        company_id=alpha["id"],
        department_id=department["id"],
        values={"enabled": False},
    )

    assert "Alpha Sales" not in prompt_builder.build_system_prompt(
        "messenger", company_id=alpha["id"]
    )


def test_the_greeting_reply_lists_only_this_companys_sections(service, alpha, beta):
    """"hello" is answered with what the business offers, straight from the
    engine and without a model call. It used to answer with the shared file."""
    from core.engine import engine

    _add(service, alpha, "sales", name_en="Alpha Sales", button_en="Alpha products")
    _add(service, beta, "bookings", name_en="Beta Bookings", button_en="Beta booking")

    result = engine.build_greeting_result("en", company_id=alpha["id"])

    assert "Alpha Sales" in result["reply"]
    assert "Beta Bookings" not in result["reply"]
    assert result["buttons"] == ["Alpha products"]

    empty = engine.build_greeting_result("en", company_id=None)

    assert "Alpha Sales" not in empty["reply"]
    assert empty["buttons"] == []


def test_the_engine_routes_a_button_press_with_the_senders_company(
    service, alpha, beta, monkeypatch
):
    """The engine has the company on the request. If it were not threaded through
    to the menu, a customer pressing a button would be matched against whatever
    company the shared file described."""
    import core.engine as engine_module

    _add(service, alpha, "sales", name_en="Alpha Sales", button_en="Alpha products")
    _add(service, beta, "bookings", name_en="Beta Bookings", button_en="Beta booking")

    monkeypatch.setattr(
        engine_module.automation_policy,
        "should_auto_reply_with_ai",
        # `**_` because the engine now names the company as well as the
        # channel: the decision is per company, since the shared file shipped
        # WhatsApp and Telegram as non-AI channels for everybody and sent their
        # customers to a flow that was T-ZONE's own.
        lambda channel, **_: True,
    )
    monkeypatch.setattr(engine_module.ai_router, "route", lambda **kwargs: None)

    response = engine_module.engine.handle_ai(
        Request(
            channel="messenger",
            user_id=f"customer-{alpha['id']}",
            company_id=alpha["id"],
            message="Alpha products",
        ),
        "en",
        None,
        None,
    )

    assert "Alpha Sales" in response.text

    # Beta's customer pressing Alpha's label matches nothing and falls through to
    # the ordinary reply path instead of entering Alpha's department.
    other = engine_module.engine.handle_ai(
        Request(
            channel="messenger",
            user_id=f"customer-{beta['id']}",
            company_id=beta["id"],
            message="Alpha products",
        ),
        "en",
        None,
        None,
    )

    assert "Alpha Sales" not in other.text


# ----------------------------------------------------------------------
# Validation and the import
# ----------------------------------------------------------------------


def test_a_department_needs_a_code_and_a_name(service, alpha):
    """The code is what the assistant routes on and the name is what a customer
    reads. A row missing either is a blank line in the menu that leads
    nowhere."""
    from backend.services.business_department_service import BusinessDepartmentError

    with pytest.raises(BusinessDepartmentError):
        service.create_department(company_id=alpha["id"], data={"code": "   "})

    with pytest.raises(BusinessDepartmentError):
        service.create_department(
            company_id=alpha["id"],
            data={"code": "sales", "name_ar": "  ", "name_en": ""},
        )


def test_codes_are_normalised_and_unique_within_a_company(service, alpha, beta):
    """The code is compared against the department the model reports and the one
    stored in the session, both lowercase ascii. ``Sales Team`` stored verbatim
    would produce a department nothing ever matches."""
    from backend.services.business_department_service import BusinessDepartmentError

    created = _add(service, alpha, "Sales Team")
    assert created["code"] == "sales_team"

    with pytest.raises(BusinessDepartmentError):
        _add(service, alpha, "sales-team")

    # The same code is free for another company: these are ordinary words.
    assert _add(service, beta, "sales_team")["code"] == "sales_team"


def test_reimporting_the_same_code_updates_instead_of_duplicating(service, alpha):
    """``import-departments`` is expected to be run more than once. Inserting
    blindly would double the menu on every run and show the customer each
    section twice."""
    first, created = service.upsert_by_code(
        company_id=alpha["id"],
        code="sales",
        data={"name_en": "Sales", "name_ar": "مبيعات"},
    )
    assert created is True

    second, created_again = service.upsert_by_code(
        company_id=alpha["id"],
        code="sales",
        data={"name_en": "Sales and offers", "name_ar": "مبيعات"},
    )

    assert created_again is False
    assert second["id"] == first["id"]
    assert len(service.list_departments(company_id=alpha["id"])) == 1
    assert second["name_en"] == "Sales and offers"


def test_the_legacy_file_imports_into_one_company_only(service, alpha, beta):
    """``config/business_modules.json`` holds the founding company's real
    departments. It is imported into that one company by hand — seeding it at
    provisioning would recreate the leak being fixed."""
    from tools.manage_platform import _legacy_to_department, _read_departments_file

    path = Path("config") / "business_modules.json"

    if not path.exists():
        pytest.skip("The legacy business modules file is not in this checkout.")

    for position, entry in enumerate(_read_departments_file(path)):
        mapped = _legacy_to_department(entry, position)

        if mapped is None:
            continue

        code, values = mapped
        service.upsert_by_code(company_id=alpha["id"], code=code, data=values)

    codes = {row["code"] for row in service.list_departments(company_id=alpha["id"])}

    assert "iptv" in codes
    assert service.list_departments(company_id=beta["id"]) == []


# ----------------------------------------------------------------------
# The HTTP surface
# ----------------------------------------------------------------------


def _client(company_id: int):
    """An app carrying only this router, with the permission gate stubbed.

    The gate itself is exercised by the unauthenticated test below; overriding
    it here keeps the rest focused on the routes.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    app.dependency_overrides[ai_teaching.view_context] = lambda: company_id
    app.dependency_overrides[ai_teaching.manage_context] = lambda: company_id
    app.dependency_overrides[ai_teaching.manage_actor] = lambda: {
        "company_id": company_id,
        "actor_user_id": 1,
        "user": {"id": 1, "full_name": "Test Actor"},
    }

    return TestClient(app)


def test_endpoints_list_create_edit_reorder_and_delete(service, alpha):
    """The screen is only as real as its routes: a service that works behind a
    router that does not is a screen that cannot define anything."""
    client = _client(alpha["id"])

    assert client.get("/api/ai-teaching/departments").json()["items"] == []

    created = client.post(
        "/api/ai-teaching/departments",
        json={"code": "sales", "name_en": "Sales", "button_en": "Products"},
    )
    assert created.status_code == 201
    first_id = created.json()["department"]["id"]

    second_id = client.post(
        "/api/ai-teaching/departments",
        json={"code": "support", "name_en": "Support"},
    ).json()["department"]["id"]

    edited = client.put(
        f"/api/ai-teaching/departments/{first_id}",
        json={"name_en": "Sales and offers", "enabled": False},
    )
    assert edited.status_code == 200
    assert edited.json()["department"]["name_en"] == "Sales and offers"
    assert edited.json()["department"]["enabled"] is False

    reordered = client.post(
        "/api/ai-teaching/departments/reorder",
        json={"department_ids": [second_id, first_id]},
    )
    assert [row["id"] for row in reordered.json()["items"]] == [second_id, first_id]

    assert client.delete(f"/api/ai-teaching/departments/{first_id}").status_code == 200
    assert client.delete(f"/api/ai-teaching/departments/{first_id}").status_code == 404


def test_endpoints_refuse_a_caller_without_a_token(service, alpha):
    """These routes decide what every customer of this company is offered. An
    unauthenticated caller must never reach them."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    client = TestClient(app)

    assert client.get("/api/ai-teaching/departments").status_code in (401, 403)
    assert (
        client.post(
            "/api/ai-teaching/departments", json={"code": "sales", "name_en": "Sales"}
        ).status_code
        in (401, 403)
    )
