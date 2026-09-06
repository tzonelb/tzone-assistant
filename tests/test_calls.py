"""The call history: what happened on the phone, written down.

Three properties are worth a test here and none of them is the wording of a
note.

The first is that the history is company-owned. Calls live in the company's own
encrypted database, so one company's phone numbers can never appear in
another's list — the ordinary multi-tenant failure, a shared table and a
forgotten `WHERE company_id = ?`, is unreachable rather than merely forbidden.
The test proves it through the API rather than in the service.

The second is the split between recording and removing. Logging a call is
answering a customer by another route, so it rides on `conversations.reply` and
any agent may do it. Deleting the record of a customer contact is a
record-keeping decision and takes `settings.manage`. A build that let any agent
erase the company's call history would pass every functional test and still be
wrong.

The third is that a call always has somebody on the other end of it. A row with
neither a number nor a contact is a blank line in a history, and the screen
would show it as "Unknown contact — —" forever.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "AgentPass123456"


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the call log service and the API at the test platform.

    The assertion is the point: without it a missed rebinding would run every
    test below against the developer's real database and prove nothing.
    """
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.calls  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.call_log_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.call_log_service" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, calls

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(calls.router)

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
def viewer(client, service, alpha):
    return _token(client, service, alpha, "viewer@alpha.example.com", "viewer")


@pytest.fixture()
def beta_owner(client, service, beta):
    return _token(client, service, beta, "owner@beta.example.com", "owner")


def _customer(platform, company, name="Nour Haddad", phone="+96170111222") -> int:
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(company["id"]) as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, phone,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (company["id"], name, phone, now, now, now, now),
        )
        conn.commit()

        return int(cursor.lastrowid)


def _log(client, token, **values):
    payload = {
        "direction": "outbound",
        "phone_number": "+96170111222",
        "duration_seconds": 90,
        "status": "completed",
        "notes": None,
    }
    payload.update(values)

    return client.post("/api/calls", headers=_headers(token), json=payload)


# ------------------------------------------------------------------ the basics


def test_a_call_can_be_logged_and_read_back(client, owner):
    created = _log(client, owner, notes="Asked about the new price list.")
    assert created.status_code == 201, created.text
    assert created.json()["direction"] == "outbound"
    assert created.json()["duration_seconds"] == 90

    listed = client.get("/api/calls", headers=_headers(owner))
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["notes"] == "Asked about the new price list."


def test_the_person_who_logged_a_call_is_named_on_it(client, owner):
    """Calls live in the tenant database and users in the control plane, so the
    name has to be resolved rather than joined. When that resolution is missed
    the column reads "—" for every row and the history stops saying who spoke
    to the customer."""
    _log(client, owner)

    row = client.get("/api/calls", headers=_headers(owner)).json()["items"][0]

    assert row["called_by_name"] == "Person"


def test_a_call_linked_to_a_contact_carries_that_contact_s_name(
    client, owner, platform, alpha
):
    customer_id = _customer(platform, alpha)

    created = _log(client, owner, customer_id=customer_id, phone_number=None)
    assert created.status_code == 201, created.text

    row = client.get("/api/calls", headers=_headers(owner)).json()["items"][0]

    assert row["customer_id"] == customer_id
    assert row["customer_name"] == "Nour Haddad"


def test_the_form_is_offered_the_values_the_api_will_accept(client, owner):
    """The dropdowns and the validation have to come from one list. Two lists
    is a form that offers an outcome the API refuses."""
    options = client.get("/api/calls/options", headers=_headers(owner))

    assert options.status_code == 200, options.text
    assert options.json()["directions"] == ["inbound", "outbound"]
    assert "voicemail" in options.json()["statuses"]


# ------------------------------------------------------------------ refusals


def test_a_call_with_nobody_on_the_other_end_is_refused(client, owner):
    refused = _log(client, owner, phone_number="", customer_id=None)

    assert refused.status_code == 400, refused.text
    assert "phone number" in refused.json()["detail"].lower()


def test_an_outcome_the_screen_never_offers_is_refused(client, owner):
    refused = _log(client, owner, status="probably_fine")

    assert refused.status_code == 400, refused.text
    assert "probably_fine" in refused.json()["detail"]


def test_a_direction_the_screen_never_offers_is_refused(client, owner):
    refused = _log(client, owner, direction="sideways")

    assert refused.status_code == 400, refused.text


def test_a_contact_from_another_company_cannot_be_linked(
    client, owner, platform, beta
):
    """The id exists — in another company's file. Accepting it would file one
    company's call against another company's customer."""
    other_customer = _customer(platform, beta, name="Beta Contact")

    refused = _log(client, owner, customer_id=other_customer, phone_number=None)

    assert refused.status_code == 404, refused.text


def test_deleting_a_call_that_does_not_exist_is_a_404(client, owner):
    missing = client.delete("/api/calls/4242", headers=_headers(owner))

    assert missing.status_code == 404, missing.text


# ------------------------------------------------------------------ filtering


def test_the_history_can_be_narrowed_to_a_direction_and_an_outcome(client, owner):
    _log(client, owner, direction="outbound", status="completed")
    _log(client, owner, direction="inbound", status="missed")
    _log(client, owner, direction="inbound", status="completed")

    inbound = client.get(
        "/api/calls", headers=_headers(owner), params={"direction": "inbound"}
    )
    assert inbound.json()["total"] == 2

    missed = client.get(
        "/api/calls", headers=_headers(owner), params={"status": "missed"}
    )
    assert missed.json()["total"] == 1
    assert missed.json()["items"][0]["direction"] == "inbound"


def test_a_contact_s_own_call_history_can_be_read(client, owner, platform, alpha):
    customer_id = _customer(platform, alpha)

    _log(client, owner, customer_id=customer_id)
    _log(client, owner)

    theirs = client.get(
        "/api/calls",
        headers=_headers(owner),
        params={"customer_id": customer_id},
    )

    assert theirs.json()["total"] == 1


# ----------------------------------------------------------------- permission


def test_an_agent_may_record_a_call_but_not_erase_one(client, owner, agent):
    """Logging a call is answering a customer. Deleting the record of one is a
    different act, and giving both to the same permission would mean anybody
    who can pick up the phone can also make a call disappear."""
    call_id = _log(client, agent).json()["id"]

    assert client.get("/api/calls", headers=_headers(agent)).status_code == 200

    refused = client.delete(f"/api/calls/{call_id}", headers=_headers(agent))
    assert refused.status_code == 403, refused.text

    allowed = client.delete(f"/api/calls/{call_id}", headers=_headers(owner))
    assert allowed.status_code == 200, allowed.text
    assert client.get("/api/calls", headers=_headers(owner)).json()["total"] == 0


def test_a_viewer_can_read_the_history_and_not_add_to_it(client, owner, viewer):
    _log(client, owner)

    assert client.get("/api/calls", headers=_headers(viewer)).status_code == 200
    assert _log(client, viewer).status_code == 403


# ------------------------------------------------------------------ isolation


def test_one_companys_call_history_never_reaches_another(
    client, owner, beta_owner
):
    _log(client, owner, phone_number="+96170999888")

    other = client.get("/api/calls", headers=_headers(beta_owner))

    assert other.status_code == 200, other.text
    assert other.json()["items"] == []


def test_a_call_cannot_be_deleted_from_another_company(client, owner, beta_owner):
    call_id = _log(client, owner).json()["id"]

    refused = client.delete(f"/api/calls/{call_id}", headers=_headers(beta_owner))

    # 404, not 403: the other company's history is not a thing whose existence
    # this caller gets to confirm.
    assert refused.status_code == 404, refused.text
    assert client.get("/api/calls", headers=_headers(owner)).json()["total"] == 1
