"""An internal note can name a colleague, and naming one has to reach them.

The composer offered an `@` picker before any of this existed: a colleague was
chosen, their name was written into the note text, and the request carried
`{"note": "..."}` and nothing else. So the mention was a piece of typography.
The person named was never told, and the note itself did not record who it was
for — which is the same shape of defect as a button that does nothing, arriving
from the other end.

Four properties are worth holding, and the third is the one that makes the
other three safe to have:

* a named colleague is told, and the note remembers who was named;
* the author is not told they mentioned themselves;
* an id belonging to another company's employee names nobody — it is neither
  stored on the note nor turned into a notification, so a hand-edited payload
  cannot deliver a note about one company's customer to a stranger;
* a note with nobody named still saves, because that is most notes.

Run against real encrypted databases, two companies, like everything else here:
the cross-company property does not exist at all against a mock.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import auth, conversations

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    # Pinned by name as well as by the sweep above. The notification the mention
    # raises is written through this module; if it were left on a different
    # manager the delivery assertions would read an empty database and pass.
    for name in (
        "backend.services.auth_service",
        "backend.services.notification_service",
        "backend.services.conversation_control_service",
    ):
        monkeypatch.setattr(sys.modules[name], "database_manager", test_manager)
        assert sys.modules[name].database_manager is test_manager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, conversations):
        app.include_router(module.router)

    return TestClient(app, raise_server_exceptions=False)


def _employ(platform, company, *, email, name, role_code="owner") -> int:
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name=name
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

    return user_id


@pytest.fixture()
def author(platform, alpha, app_client):
    """The employee who writes the note, signed in."""
    user_id = _employ(
        platform, alpha, email="rana@alpha.example.com", name="Rana Haddad"
    )

    response = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "rana@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


@pytest.fixture()
def colleague(platform, alpha) -> int:
    """A second Alpha employee — the one the note is for."""
    return _employ(
        platform,
        alpha,
        email="sami@alpha.example.com",
        name="Sami Nasr",
        role_code="agent",
    )


@pytest.fixture()
def outsider(platform, beta) -> int:
    """An employee of the *other* company. Alpha may not reach them."""
    return _employ(
        platform,
        beta,
        email="omar@beta.example.com",
        name="Omar Beta",
        role_code="agent",
    )


@pytest.fixture()
def conversation(alpha):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.message_service import message_service

    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-1"
    )
    message_service.save_message(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-1",
        direction="in",
        text="Do you deliver on Sundays?",
        sender_type="customer",
    )

    return "messenger", "cust-1"


def _mentions_for(company_id: int, user_id: int) -> list[dict]:
    from backend.services.notification_service import notification_service

    return notification_service.list_for_user(
        company_id=company_id,
        user_id=user_id,
        notification_type="conversation_mention",
    )


def _post_note(app_client, author, conversation, note, mentioned):
    channel, external_user_id = conversation

    return app_client.post(
        f"/conversations/{channel}/{external_user_id}/notes",
        headers=author["headers"],
        json={"note": note, "mentioned_user_ids": mentioned},
    )


# --------------------------------------------------------------------------


def test_a_named_colleague_is_told_and_the_note_remembers_them(
    app_client, author, colleague, conversation, alpha
):
    written = _post_note(
        app_client,
        author,
        conversation,
        "@Sami Nasr can you call them back about Sunday?",
        [colleague],
    )

    assert written.status_code in (200, 201), written.text
    assert written.json()["note"]["mentioned_user_ids"] == [colleague], (
        "the note did not record who it was for:\n" + written.text
    )

    delivered = _mentions_for(alpha["id"], colleague)

    assert len(delivered) == 1, (
        "the colleague named in the note was not notified; "
        f"they have {len(delivered)} mention notifications"
    )
    assert "Rana Haddad" in delivered[0]["title"], delivered[0]
    assert "Sunday" in (delivered[0]["body"] or ""), delivered[0]

    # And it survives a read: the timeline the panel opens shows the mention,
    # not just the request that created it.
    channel, external_user_id = conversation
    panel = app_client.get(
        f"/conversations/{channel}/{external_user_id}/control",
        headers=author["headers"],
    ).json()

    assert panel["notes"][0]["mentioned_user_ids"] == [colleague], panel["notes"][0]


def test_every_named_colleague_is_told(
    platform, app_client, author, colleague, conversation, alpha
):
    """Two names, two notifications. One loop that stops early is a silent half."""
    second = _employ(
        platform,
        alpha,
        email="lina@alpha.example.com",
        name="Lina Aziz",
        role_code="agent",
    )

    written = _post_note(
        app_client,
        author,
        conversation,
        "@Sami Nasr @Lina Aziz one of you please take this.",
        [colleague, second],
    )
    assert written.status_code in (200, 201), written.text

    assert len(_mentions_for(alpha["id"], colleague)) == 1, "first name missed"
    assert len(_mentions_for(alpha["id"], second)) == 1, "second name missed"


def test_the_author_is_not_told_they_mentioned_themselves(
    app_client, author, colleague, conversation, alpha
):
    written = _post_note(
        app_client,
        author,
        conversation,
        "@Rana Haddad @Sami Nasr — noting this for both of us.",
        [author["user_id"], colleague],
    )
    assert written.status_code in (200, 201), written.text

    assert _mentions_for(alpha["id"], colleague), (
        "the colleague was not notified when the author named themselves too"
    )
    assert not _mentions_for(alpha["id"], author["user_id"]), (
        "the author was notified about their own note"
    )


def test_another_companys_employee_cannot_be_named(
    app_client, author, outsider, conversation, alpha, beta
):
    """The property the whole check exists for.

    The id is a plain integer in a request body. Nothing about it says which
    company owns it, so a payload can carry any number on the platform — and if
    it were believed, an internal note about one company's customer would be
    delivered, with its full text, to somebody who does not work there.
    """
    written = _post_note(
        app_client,
        author,
        conversation,
        "Nothing here is for you.",
        [outsider],
    )

    assert written.status_code in (200, 201), written.text
    assert written.json()["note"]["mentioned_user_ids"] == [], (
        "an id from another company was stored on the note:\n" + written.text
    )

    # Neither company's notifications carry it: not Alpha's (where the note
    # lives) and not Beta's (where the person does).
    assert not _mentions_for(alpha["id"], outsider), (
        "another company's employee was notified out of Alpha's database"
    )
    assert not _mentions_for(beta["id"], outsider), (
        "the note reached across into the other company's database"
    )


def test_a_disabled_colleague_is_not_named(
    platform, app_client, author, conversation, alpha
):
    """"Active employee" is both halves. Somebody who has left is not on call."""
    departed = _employ(
        platform,
        alpha,
        email="gone@alpha.example.com",
        name="Departed Colleague",
        role_code="agent",
    )

    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE company_users SET status = 'disabled' WHERE user_id = ?",
            (departed,),
        )
        conn.commit()

    written = _post_note(
        app_client, author, conversation, "@Departed Colleague ?", [departed]
    )

    assert written.status_code in (200, 201), written.text
    assert written.json()["note"]["mentioned_user_ids"] == [], written.text
    assert not _mentions_for(alpha["id"], departed)


def test_a_note_with_nobody_named_still_saves(
    app_client, author, conversation, alpha
):
    """Most notes name nobody, and they were working before any of this."""
    channel, external_user_id = conversation

    written = app_client.post(
        f"/conversations/{channel}/{external_user_id}/notes",
        headers=author["headers"],
        json={"note": "Customer asked about Sunday delivery."},
    )

    assert written.status_code in (200, 201), (
        f"a plain note answered {written.status_code}:\n{written.text}"
    )

    note = written.json()["note"]

    assert note["mentioned_user_ids"] == [], note
    assert note["author_name"] == "Rana Haddad", note

    panel = app_client.get(
        f"/conversations/{channel}/{external_user_id}/control",
        headers=author["headers"],
    ).json()

    assert panel["notes"], "the note is not on the timeline"
