"""Canned replies an employee inserts while answering a customer.

Two properties matter here and neither is about the text itself.

The first is that the library is company-owned: it lives in that company's own
encrypted database, so one company's wording can never appear in another's
composer. The failure this guards against is the ordinary multi-tenant one --
a shared table and a forgotten `WHERE company_id = ?` -- which the per-company
file makes unreachable rather than merely forbidden. The test proves the
isolation holds through the API, not just in the service.

The second is the split between reading and writing. Anyone who can open the
inbox can use a saved reply, because inserting one is part of answering. Editing
the library is a settings decision: it changes the words the whole company
answers with, so it takes `settings.manage`. A build that let any agent rewrite
the library would pass every functional test and still be wrong.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "AgentPass123456"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.saved_replies  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.saved_reply_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.saved_reply_service" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, saved_replies

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(saved_replies.router)

    return TestClient(app)


def _token(client, service, company, email, role):
    user_id = service.create_user(email, PASSWORD, "Person")
    service.assign_user_to_company(user_id, company["id"], role)

    response = client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text

    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def owner(client, service, alpha):
    return _token(client, service, alpha, "owner@alpha.example.com", "owner")


@pytest.fixture()
def agent(client, service, alpha):
    return _token(client, service, alpha, "agent@alpha.example.com", "agent")


@pytest.fixture()
def beta_owner(client, service, beta):
    return _token(client, service, beta, "owner@beta.example.com", "owner")


def _create(client, token, title="Opening hours", body="9 to 6", department=""):
    return client.post(
        "/api/saved-replies",
        headers=_headers(token),
        json={"title": title, "body": body, "department": department},
    )


# ------------------------------------------------------------------ the basics


def test_a_reply_can_be_written_and_read_back(client, owner):
    created = _create(client, owner)
    assert created.status_code == 201, created.text
    assert created.json()["title"] == "Opening hours"

    listed = client.get("/api/saved-replies", headers=_headers(owner))
    assert listed.status_code == 200, listed.text
    assert [item["title"] for item in listed.json()["items"]] == ["Opening hours"]


def test_a_reply_can_be_edited_and_deleted(client, owner):
    reply_id = _create(client, owner).json()["id"]

    edited = client.patch(
        f"/api/saved-replies/{reply_id}",
        headers=_headers(owner),
        json={"body": "9am to 6pm"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["body"] == "9am to 6pm"
    # The title was not sent, so it must not have been blanked.
    assert edited.json()["title"] == "Opening hours"

    removed = client.delete(
        f"/api/saved-replies/{reply_id}", headers=_headers(owner)
    )
    assert removed.status_code == 200, removed.text
    assert client.get("/api/saved-replies", headers=_headers(owner)).json()["items"] == []


def test_editing_a_reply_that_does_not_exist_is_a_404(client, owner):
    missing = client.patch(
        "/api/saved-replies/4242", headers=_headers(owner), json={"body": "x"}
    )
    assert missing.status_code == 404, missing.text


# ------------------------------------------------------------------- filtering


def test_a_section_sees_its_own_replies_and_the_general_ones(client, owner):
    _create(client, owner, title="Sales pitch", body="...", department="sales")
    _create(client, owner, title="Shipping", body="2-3 days")
    _create(client, owner, title="Refunds", body="...", department="support")

    sales = client.get(
        "/api/saved-replies", headers=_headers(owner), params={"department": "sales"}
    )
    titles = sorted(item["title"] for item in sales.json()["items"])

    # "Shipping" has no department, so it belongs to every section. Excluding it
    # would leave a company that files most replies as general staring at an
    # empty list on every section.
    assert titles == ["Sales pitch", "Shipping"]
    assert "Refunds" not in titles


# ------------------------------------------------------------------ permission


def test_an_agent_can_read_the_library_but_not_rewrite_it(client, owner, agent):
    _create(client, owner)

    readable = client.get("/api/saved-replies", headers=_headers(agent))
    assert readable.status_code == 200, readable.text
    assert len(readable.json()["items"]) == 1
    # The composer hides the editing affordance rather than offering a control
    # that answers 403 when clicked.
    assert readable.json()["can_manage"] is False

    refused = _create(client, agent, title="Mine")
    assert refused.status_code == 403, refused.text


def test_the_owner_is_told_they_may_manage(client, owner):
    listed = client.get("/api/saved-replies", headers=_headers(owner))
    assert listed.json()["can_manage"] is True


# ------------------------------------------------------------------- isolation


def test_one_companys_library_never_reaches_another(
    client, owner, beta_owner
):
    _create(client, owner, title="Alpha wording")

    other = client.get("/api/saved-replies", headers=_headers(beta_owner))
    assert other.status_code == 200, other.text
    assert other.json()["items"] == []


def test_a_reply_id_from_another_company_cannot_be_edited(
    client, owner, beta_owner
):
    """The id is a small integer, so guessing one is trivial.

    Each company's replies live in its own database file, so id 1 exists in both
    and means something different in each. The request must not reach across.
    """
    reply_id = _create(client, owner, title="Alpha wording").json()["id"]

    stolen = client.patch(
        f"/api/saved-replies/{reply_id}",
        headers=_headers(beta_owner),
        json={"body": "changed"},
    )
    assert stolen.status_code == 404, stolen.text

    intact = client.get("/api/saved-replies", headers=_headers(owner))
    assert intact.json()["items"][0]["body"] == "9 to 6"


# ------------------------------------------------------------------ validation


def test_a_blank_title_is_refused(client, owner):
    blank = _create(client, owner, title="   ")
    assert blank.status_code in (400, 422), blank.text
