"""Every control on the redesigned Contacts screens, driven through its route.

The two screens — the register at `/customers` and the client file at
`/customers/:customerId` — offer a stage dropdown, a tag chip, an owner
picker, a bulk bar, saved segments, custom fields, documents and a timeline.
Each of those is a request, and a control whose request does not exist is the
worst kind of defect this codebase has: the screen renders, the click is
accepted, and nothing is saved.

So this file does not check status codes on their own. For each control it
performs the action the screen performs and then reads the record back to see
the change landed — and where a value can name something outside this company
(an owner who works elsewhere, a customer id from another tenant), it checks
the refusal too.

`customers` is per-tenant: Alpha's connection is a different encrypted file and
cannot see Beta's rows at all. The isolation checks here are therefore about
the endpoints that take an id from the URL or a body — `bulk-update` takes a
whole list of them — where a missing scope would show up as a silent success
rather than as a leak.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import auth, conversations, customers

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    assert (
        getattr(sys.modules["backend.services.auth_service"], "database_manager", None)
        is test_manager
    ), "auth_service is not talking to the test database"

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(conversations.router)
    app.include_router(customers.router)
    # The segments router is a second router on the same module. Leaving it out
    # here would let this file pass while every "New segment" click 404s.
    app.include_router(customers.segments_router)

    return TestClient(app, raise_server_exceptions=False)


def _sign_in(platform, company, app_client, *, email, role_code):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name=email.split("@")[0].title()
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = ?",
            (company["id"], role_code),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    response = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


@pytest.fixture()
def alpha_owner(platform, alpha, app_client):
    return _sign_in(
        platform, alpha, app_client, email="owner@alpha.example.com", role_code="owner"
    )


@pytest.fixture()
def beta_owner(platform, beta, app_client):
    return _sign_in(
        platform, beta, app_client, email="owner@beta.example.com", role_code="owner"
    )


@pytest.fixture()
def alpha_agent(platform, alpha, app_client):
    """An agent: holds `customers.view` and no `settings.manage`, which is the
    split the Contacts screen itself uses to decide whether to draw the bulk
    bar."""
    return _sign_in(
        platform, alpha, app_client, email="agent@alpha.example.com", role_code="agent"
    )


def _ok(response, what):
    assert response.status_code in (200, 201), (
        f"{what} answered {response.status_code}:\n{response.text}"
    )
    return response.json()


def _contact(app_client, owner, **fields):
    payload = {"display_name": "Rami Aoun", **fields}
    return _ok(
        app_client.post("/api/customers", headers=owner["headers"], json=payload),
        "creating a contact",
    )


def _read(app_client, owner, customer_id):
    return _ok(
        app_client.get(f"/api/customers/{customer_id}", headers=owner["headers"]),
        "reading the contact back",
    )


# ------------------------------------------------------------------ options


def test_options_carries_the_pipeline_and_the_company_employees(
    app_client, alpha_owner
):
    body = _ok(
        app_client.get("/api/customers/options", headers=alpha_owner["headers"]),
        "the Contacts options",
    )

    assert body["lifecycle_stages"] == ["lead", "active", "customer", "vip", "churned"]

    employees = {employee["id"]: employee for employee in body["employees"]}
    assert alpha_owner["user_id"] in employees, (
        f"the owner dropdown cannot offer this company's own owner: {body}"
    )
    assert employees[alpha_owner["user_id"]]["display_name"], (
        "an employee with no display_name renders as a blank option"
    )


def test_options_does_not_offer_another_companys_employees(
    app_client, alpha_owner, beta_owner
):
    body = _ok(
        app_client.get("/api/customers/options", headers=alpha_owner["headers"]),
        "the Contacts options",
    )

    assert beta_owner["user_id"] not in {
        employee["id"] for employee in body["employees"]
    }, "Alpha's owner dropdown offered a Beta employee"


# ------------------------------------------------------- creating a contact


def test_a_new_contact_starts_as_a_lead_with_no_tags(app_client, alpha_owner):
    created = _contact(app_client, alpha_owner, phone="+9613111222")

    assert created["lifecycle_stage"] == "lead"
    assert created["tags"] == []
    assert created["custom_fields"] == {}
    assert created["documents"] == []
    assert created["assigned_user_id"] is None
    assert created["conversation_count"] == 0
    assert created["channels"] == []


def test_a_contact_needs_at_least_one_identifying_field(app_client, alpha_owner):
    response = app_client.post(
        "/api/customers", headers=alpha_owner["headers"], json={}
    )

    assert response.status_code == 400, response.text
    assert "name" in response.text.lower()


def test_a_created_contact_appears_in_the_register(app_client, alpha_owner):
    created = _contact(app_client, alpha_owner, display_name="Walk-in Nadia")

    listed = _ok(
        app_client.get(
            "/api/customers", headers=alpha_owner["headers"], params={"search": "Nadia"}
        ),
        "the register",
    )

    assert listed["total"] == 1
    assert [row["id"] for row in listed["items"]] == [created["id"]]
    # The row the screen draws reads these four names off the item directly.
    row = listed["items"][0]
    assert row["tags"] == []
    assert row["channels"] == []
    assert row["lifecycle_stage"] == "lead"
    assert "updated_at" in row


# -------------------------------------------------- stage, tags, assignment


def test_the_stage_dropdown_saves_and_refuses_a_stage_that_is_not_one(
    app_client, alpha_owner
):
    contact = _contact(app_client, alpha_owner)

    changed = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={"lifecycle_stage": "vip"},
        ),
        "changing the lifecycle stage",
    )
    assert changed["lifecycle_stage"] == "vip"
    assert _read(app_client, alpha_owner, contact["id"])["lifecycle_stage"] == "vip"

    refused = app_client.put(
        f"/api/customers/{contact['id']}",
        headers=alpha_owner["headers"],
        json={"lifecycle_stage": "platinum"},
    )
    assert refused.status_code == 400, refused.text
    assert _read(app_client, alpha_owner, contact["id"])["lifecycle_stage"] == "vip"


def test_tags_are_added_de_duplicated_and_removed(app_client, alpha_owner):
    contact = _contact(app_client, alpha_owner)

    added = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={"tags": ["vip", " vip ", "beirut"]},
        ),
        "adding tags",
    )
    assert added["tags"] == ["vip", "beirut"]

    removed = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={"tags": ["beirut"]},
        ),
        "removing a tag",
    )
    assert removed["tags"] == ["beirut"]
    assert _read(app_client, alpha_owner, contact["id"])["tags"] == ["beirut"]


def test_a_contact_can_be_assigned_to_an_employee_and_unassigned(
    app_client, alpha_owner
):
    contact = _contact(app_client, alpha_owner)

    assigned = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={"assigned_user_id": alpha_owner["user_id"]},
        ),
        "assigning an owner",
    )
    assert assigned["assigned_user_id"] == alpha_owner["user_id"]
    assert assigned["assigned_user_name"], (
        "the owner column would render blank: the name did not resolve"
    )

    cleared = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={"assigned_user_id": None},
        ),
        "unassigning",
    )
    assert cleared["assigned_user_id"] is None
    assert cleared["assigned_user_name"] is None


def test_a_contact_cannot_be_assigned_to_someone_from_another_company(
    app_client, alpha_owner, beta_owner
):
    contact = _contact(app_client, alpha_owner)

    refused = app_client.put(
        f"/api/customers/{contact['id']}",
        headers=alpha_owner["headers"],
        json={"assigned_user_id": beta_owner["user_id"]},
    )

    assert refused.status_code == 400, (
        f"a Beta employee was accepted as an Alpha contact's owner:\n{refused.text}"
    )
    assert _read(app_client, alpha_owner, contact["id"])["assigned_user_id"] is None


# ------------------------------------------- custom fields and documents


def test_custom_fields_round_trip_and_can_be_cleared(app_client, alpha_owner):
    contact = _contact(app_client, alpha_owner)

    saved = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={"custom_fields": {"ID number": "LB-9931", "Plan": "Gold"}},
        ),
        "saving custom fields",
    )
    assert saved["custom_fields"] == {"ID number": "LB-9931", "Plan": "Gold"}

    trimmed = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={"custom_fields": {"Plan": "Gold"}},
        ),
        "removing a custom field",
    )
    assert trimmed["custom_fields"] == {"Plan": "Gold"}


def test_documents_round_trip_and_drop_a_row_with_no_url(app_client, alpha_owner):
    contact = _contact(app_client, alpha_owner)

    saved = _ok(
        app_client.put(
            f"/api/customers/{contact['id']}",
            headers=alpha_owner["headers"],
            json={
                "documents": [
                    {"label": "ID photo", "url": "https://files.example.com/id.png"},
                    {"label": "No link", "url": ""},
                ]
            },
        ),
        "saving documents",
    )

    assert saved["documents"] == [
        {"label": "ID photo", "url": "https://files.example.com/id.png"}
    ]


# ------------------------------------------------------------ list filters


def test_the_register_filters_by_stage_tag_and_owner(app_client, alpha_owner):
    lead = _contact(app_client, alpha_owner, display_name="Lead Person")
    vip = _contact(app_client, alpha_owner, display_name="VIP Person")

    app_client.put(
        f"/api/customers/{vip['id']}",
        headers=alpha_owner["headers"],
        json={
            "lifecycle_stage": "vip",
            "tags": ["beirut"],
            "assigned_user_id": alpha_owner["user_id"],
        },
    )

    by_stage = _ok(
        app_client.get(
            "/api/customers",
            headers=alpha_owner["headers"],
            params={"lifecycle_stage": "vip"},
        ),
        "filtering by stage",
    )
    assert [row["id"] for row in by_stage["items"]] == [vip["id"]]

    by_tag = _ok(
        app_client.get(
            "/api/customers", headers=alpha_owner["headers"], params={"tag": "beirut"}
        ),
        "filtering by tag",
    )
    assert [row["id"] for row in by_tag["items"]] == [vip["id"]]

    by_owner = _ok(
        app_client.get(
            "/api/customers",
            headers=alpha_owner["headers"],
            params={"assigned_user_id": alpha_owner["user_id"]},
        ),
        "filtering by owner",
    )
    assert [row["id"] for row in by_owner["items"]] == [vip["id"]]

    unfiltered = _ok(
        app_client.get("/api/customers", headers=alpha_owner["headers"]),
        "the unfiltered register",
    )
    assert {row["id"] for row in unfiltered["items"]} == {lead["id"], vip["id"]}


def test_a_tag_filter_does_not_match_a_longer_tag_that_starts_the_same(
    app_client, alpha_owner
):
    contact = _contact(app_client, alpha_owner)
    app_client.put(
        f"/api/customers/{contact['id']}",
        headers=alpha_owner["headers"],
        json={"tags": ["vip-2024"]},
    )

    body = _ok(
        app_client.get(
            "/api/customers", headers=alpha_owner["headers"], params={"tag": "vip"}
        ),
        "filtering by tag",
    )

    assert body["items"] == [], "the 'vip' filter swept up 'vip-2024'"


# ---------------------------------------------------------------- segments


def test_a_segment_saves_the_filters_and_applying_it_returns_them(
    app_client, alpha_owner
):
    _contact(app_client, alpha_owner, display_name="Lead Person")
    vip = _contact(app_client, alpha_owner, display_name="VIP Person")
    app_client.put(
        f"/api/customers/{vip['id']}",
        headers=alpha_owner["headers"],
        json={"lifecycle_stage": "vip"},
    )

    segment = _ok(
        app_client.post(
            "/api/customer-segments",
            headers=alpha_owner["headers"],
            json={"name": "Top clients", "filters": {"lifecycle_stage": "vip"}},
        ),
        "saving a segment",
    )
    assert segment["filters"] == {"lifecycle_stage": "vip"}

    listed = _ok(
        app_client.get("/api/customer-segments", headers=alpha_owner["headers"]),
        "the segment list",
    )
    assert [item["name"] for item in listed["items"]] == ["Top clients"]

    applied = _ok(
        app_client.get(
            "/api/customers",
            headers=alpha_owner["headers"],
            params={"segment_id": segment["id"]},
        ),
        "applying a segment",
    )
    assert [row["id"] for row in applied["items"]] == [vip["id"]]


def test_a_segment_name_cannot_be_reused_and_a_bad_stage_is_refused(
    app_client, alpha_owner
):
    _ok(
        app_client.post(
            "/api/customer-segments",
            headers=alpha_owner["headers"],
            json={"name": "Top clients", "filters": {}},
        ),
        "saving a segment",
    )

    duplicate = app_client.post(
        "/api/customer-segments",
        headers=alpha_owner["headers"],
        json={"name": "top CLIENTS", "filters": {}},
    )
    assert duplicate.status_code == 400, duplicate.text

    bad_stage = app_client.post(
        "/api/customer-segments",
        headers=alpha_owner["headers"],
        json={"name": "Nonsense", "filters": {"lifecycle_stage": "platinum"}},
    )
    assert bad_stage.status_code == 400, bad_stage.text


def test_an_employee_can_delete_their_own_segment(app_client, alpha_agent):
    segment = _ok(
        app_client.post(
            "/api/customer-segments",
            headers=alpha_agent["headers"],
            json={"name": "My morning list", "filters": {"tag": "beirut"}},
        ),
        "an agent saving their own segment",
    )

    deleted = app_client.delete(
        f"/api/customer-segments/{segment['id']}", headers=alpha_agent["headers"]
    )
    assert deleted.status_code == 200, deleted.text

    listed = _ok(
        app_client.get("/api/customer-segments", headers=alpha_agent["headers"]),
        "the segment list",
    )
    assert listed["items"] == []


def test_an_employee_cannot_delete_a_colleagues_segment(
    app_client, alpha_owner, alpha_agent
):
    segment = _ok(
        app_client.post(
            "/api/customer-segments",
            headers=alpha_owner["headers"],
            json={"name": "The owner's list", "filters": {}},
        ),
        "the owner saving a segment",
    )

    refused = app_client.delete(
        f"/api/customer-segments/{segment['id']}", headers=alpha_agent["headers"]
    )
    assert refused.status_code == 403, refused.text

    listed = _ok(
        app_client.get("/api/customer-segments", headers=alpha_owner["headers"]),
        "the segment list",
    )
    assert [item["id"] for item in listed["items"]] == [segment["id"]]


# ------------------------------------------------------------- bulk update


def test_the_bulk_bar_sets_a_stage_and_adds_a_tag_without_duplicating_it(
    app_client, alpha_owner
):
    first = _contact(app_client, alpha_owner, display_name="First")
    second = _contact(app_client, alpha_owner, display_name="Second")

    app_client.put(
        f"/api/customers/{first['id']}",
        headers=alpha_owner["headers"],
        json={"tags": ["beirut"]},
    )

    staged = _ok(
        app_client.post(
            "/api/customers/bulk-update",
            headers=alpha_owner["headers"],
            json={
                "customer_ids": [first["id"], second["id"]],
                "lifecycle_stage": "customer",
            },
        ),
        "the bulk stage change",
    )
    assert staged == {"updated": 2}

    tagged = _ok(
        app_client.post(
            "/api/customers/bulk-update",
            headers=alpha_owner["headers"],
            json={"customer_ids": [first["id"], second["id"]], "add_tag": "beirut"},
        ),
        "the bulk tag",
    )
    assert tagged == {"updated": 2}

    for contact in (first, second):
        read = _read(app_client, alpha_owner, contact["id"])
        assert read["lifecycle_stage"] == "customer"
        assert read["tags"] == ["beirut"], (
            f"the tag was duplicated or lost: {read['tags']}"
        )


def test_the_bulk_bar_is_behind_settings_manage(app_client, alpha_owner, alpha_agent):
    contact = _contact(app_client, alpha_owner)

    refused = app_client.post(
        "/api/customers/bulk-update",
        headers=alpha_agent["headers"],
        json={"customer_ids": [contact["id"]], "lifecycle_stage": "vip"},
    )

    assert refused.status_code == 403, refused.text
    assert _read(app_client, alpha_owner, contact["id"])["lifecycle_stage"] == "lead"


# ---------------------------------------------------------------- timeline


def test_the_timeline_shows_profile_edits_newest_first(app_client, alpha_owner):
    contact = _contact(app_client, alpha_owner)

    app_client.put(
        f"/api/customers/{contact['id']}",
        headers=alpha_owner["headers"],
        json={"phone": "+9613999888"},
    )

    body = _ok(
        app_client.get(
            f"/api/customers/{contact['id']}/timeline", headers=alpha_owner["headers"]
        ),
        "the timeline",
    )

    kinds = [event["type"] for event in body["items"]]
    assert kinds.count("profile_updated") == 2, (
        f"creation and the edit should both be recorded: {body['items']}"
    )
    assert body["items"][0]["actor_name"], (
        "the timeline entry would read 'Profile updated' with nobody behind it"
    )
    assert "phone" in body["items"][0]["changes"]
    timestamps = [event["created_at"] for event in body["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_the_timeline_carries_the_contacts_conversations(
    app_client, alpha_owner, platform, alpha
):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.customer_service import customer_service

    # The order a real message takes: the channel opens the conversation, then
    # the identity is upserted, which is what links the two together.
    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="telegram", external_user_id="tg-4411"
    )
    contact = customer_service.upsert_from_channel(
        company_id=alpha["id"],
        channel="telegram",
        external_user_id="tg-4411",
        display_name="Karim Fares",
    )

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute(
            "UPDATE conversations SET topic = 'Delivery', department = 'sales', "
            "assigned_user_id = ? WHERE customer_id = ?",
            (alpha_owner["user_id"], contact["id"]),
        )
        conn.commit()

    body = _ok(
        app_client.get(
            f"/api/customers/{contact['id']}/timeline", headers=alpha_owner["headers"]
        ),
        "the timeline",
    )

    started = [
        event for event in body["items"] if event["type"] == "conversation_started"
    ]
    assert len(started) == 1, f"the conversation is missing: {body['items']}"
    assert started[0]["channel"] == "telegram"
    assert started[0]["topic"] == "Delivery"
    assert started[0]["handled_by_name"], (
        "'Handled by' would render blank: the employee name did not resolve"
    )


def test_the_timeline_of_a_contact_that_does_not_exist_is_a_404(
    app_client, alpha_owner
):
    response = app_client.get(
        "/api/customers/987654/timeline", headers=alpha_owner["headers"]
    )
    assert response.status_code == 404, response.text


def test_a_channel_contact_reports_the_channels_it_can_be_reached_on(
    app_client, alpha_owner, alpha
):
    from backend.services.customer_service import customer_service

    contact = customer_service.upsert_from_channel(
        company_id=alpha["id"],
        channel="whatsapp",
        external_user_id="wa-77",
        display_name="Omar Saad",
    )

    read = _read(app_client, alpha_owner, contact["id"])
    assert read["channels"] == ["whatsapp"]

    listed = _ok(
        app_client.get(
            "/api/customers", headers=alpha_owner["headers"], params={"search": "Omar"}
        ),
        "the register",
    )
    assert listed["items"][0]["channels"] == ["whatsapp"], (
        "the Channels column reads `channels` off the row and would show a dash"
    )


# -------------------------------------------------------- tenant isolation


def test_one_company_cannot_read_or_edit_another_companys_contact(
    app_client, alpha_owner, beta_owner
):
    theirs = _contact(app_client, beta_owner, display_name="Beta Secret Contact")

    assert _read(app_client, beta_owner, theirs["id"])["display_name"] == (
        "Beta Secret Contact"
    ), "positive control: Beta cannot read its own contact"

    read = app_client.get(
        f"/api/customers/{theirs['id']}", headers=alpha_owner["headers"]
    )
    assert read.status_code not in (200, 500), (
        f"CROSS-TENANT READ: Alpha read Beta's contact\n{read.text}"
    )

    timeline = app_client.get(
        f"/api/customers/{theirs['id']}/timeline", headers=alpha_owner["headers"]
    )
    assert timeline.status_code not in (200, 500), (
        f"CROSS-TENANT READ: Alpha read Beta's contact timeline\n{timeline.text}"
    )

    edit = app_client.put(
        f"/api/customers/{theirs['id']}",
        headers=alpha_owner["headers"],
        json={"lifecycle_stage": "churned", "tags": ["stolen"]},
    )
    assert edit.status_code not in (200, 500), (
        f"CROSS-TENANT WRITE: Alpha edited Beta's contact\n{edit.text}"
    )

    after = _read(app_client, beta_owner, theirs["id"])
    assert after["lifecycle_stage"] == "lead"
    assert after["tags"] == []


def test_a_bulk_update_cannot_reach_another_companys_contacts(
    app_client, alpha_owner, beta_owner
):
    mine = _contact(app_client, alpha_owner, display_name="Alpha Contact")

    # Ids are per-file, so both companies' first contact is id 1. Beta gets
    # three so that `theirs` names an id Alpha has never issued — otherwise the
    # foreign id would collide with Alpha's own row and the check would pass
    # for the wrong reason.
    for index in range(3):
        theirs = _contact(app_client, beta_owner, display_name=f"Beta Contact {index}")

    assert theirs["id"] != mine["id"]

    result = _ok(
        app_client.post(
            "/api/customers/bulk-update",
            headers=alpha_owner["headers"],
            json={
                "customer_ids": [mine["id"], theirs["id"]],
                "lifecycle_stage": "churned",
            },
        ),
        "a bulk update naming another company's id",
    )

    # The foreign id is skipped, not an error: it is indistinguishable from a
    # stale id, and the rest of the batch has to land.
    assert result["updated"] == 1
    assert _read(app_client, alpha_owner, mine["id"])["lifecycle_stage"] == "churned"
    assert _read(app_client, beta_owner, theirs["id"])["lifecycle_stage"] == "lead", (
        "CROSS-TENANT WRITE: a bulk update changed another company's contact"
    )


def test_segments_are_not_visible_or_appliable_across_companies(
    app_client, alpha_owner, beta_owner
):
    theirs = _ok(
        app_client.post(
            "/api/customer-segments",
            headers=beta_owner["headers"],
            json={"name": "Beta only", "filters": {"tag": "beirut"}},
        ),
        "Beta saving a segment",
    )

    listed = _ok(
        app_client.get("/api/customer-segments", headers=alpha_owner["headers"]),
        "Alpha's segment list",
    )
    assert listed["items"] == [], f"Alpha can see Beta's segments: {listed}"

    applied = app_client.get(
        "/api/customers",
        headers=alpha_owner["headers"],
        params={"segment_id": theirs["id"]},
    )
    assert applied.status_code == 404, (
        f"Alpha applied Beta's segment id\n{applied.text}"
    )

    deleted = app_client.delete(
        f"/api/customer-segments/{theirs['id']}", headers=alpha_owner["headers"]
    )
    assert deleted.status_code == 404, deleted.text
    assert (
        len(
            _ok(
                app_client.get(
                    "/api/customer-segments", headers=beta_owner["headers"]
                ),
                "Beta's segment list",
            )["items"]
        )
        == 1
    )
