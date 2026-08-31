"""Direct messages and groups, on the channels team chat already had.

The redesigned Team Chat screen opens on one company-wide stream and a list of
the direct messages and groups a person belongs to. None of that is a second
chat system: a DM is a private channel between a fixed pair, a group is a
private channel with a member list, and the company stream is the ordinary
public channel everybody shares. So the properties worth holding here are the
ones the privacy rule at the top of `team_chat_service` already promises, asked
again through the shape the screen speaks:

* a DM opened twice by either person is one conversation, never two;
* a colleague of another company cannot be opened a DM with, and cannot be put
  into a group — the ids arrive in a request body, which says nothing about who
  owns them;
* a room is titled by who is in it, so the same row reads differently to each
  of the two people in it;
* a stranger cannot read, write to, or delete from a room they are not in — and
  is told "not found", never "not yours";
* only the author withdraws their own message.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    """Point team chat, the directory and notifications at the test databases."""
    import backend.services.auth_service  # noqa: F401
    import backend.services.notification_service  # noqa: F401
    import backend.services.ownership  # noqa: F401
    import backend.services.team_chat_service  # noqa: F401
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.team_chat_service" in rebound

    for name in (
        "backend.services.auth_service",
        "backend.services.notification_service",
        "backend.services.ownership",
    ):
        monkeypatch.setattr(sys.modules[name], "database_manager", test_manager)
        assert sys.modules[name].database_manager is test_manager

    from backend.services.team_chat_service import team_chat_service

    return team_chat_service


@pytest.fixture()
def staff(platform):
    """Three colleagues at Alpha and one at Beta, as real employees."""
    from database.manager import utc_now_iso

    manager = platform["manager"]
    people: dict[str, int] = {}

    roster = {
        "alpha": [
            ("rana", "Rana Haddad", "rana@alpha.test"),
            ("sara", "Sara Nasr", "sara@alpha.test"),
            ("karim", "Karim Aziz", "karim@alpha.test"),
        ],
        "beta": [("lina", "Lina Saad", "lina@beta.test")],
    }

    with manager.control() as conn:
        now = utc_now_iso()

        for slug, members in roster.items():
            company_id = platform["companies"][slug]["id"]
            role = conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = 'agent'",
                (company_id,),
            ).fetchone()

            for key, full_name, email in members:
                cursor = conn.execute(
                    """
                    INSERT INTO users (
                        email, password_hash, full_name, status, created_at, updated_at
                    )
                    VALUES (?, 'x', ?, 'active', ?, ?)
                    """,
                    (email, full_name, now, now),
                )
                user_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO company_users (
                        company_id, user_id, role_id, status, created_at
                    )
                    VALUES (?, ?, ?, 'active', ?)
                    """,
                    (company_id, user_id, int(role["id"]), now),
                )
                people[f"{slug}.{key}"] = user_id

        conn.commit()

    return people


# ---------------------------------------------------------------- the stream


def test_the_company_stream_is_the_same_channel_for_everyone(
    service, alpha, staff
):
    """Two employees opening team chat land in one conversation, not two.

    The stream is created on first use, so the second caller must find the
    first one's channel rather than make another — otherwise every employee
    would be talking to themselves in a room named the same thing.
    """
    first = service.company_stream_id(
        company_id=alpha["id"], user_id=staff["alpha.rana"]
    )
    second = service.company_stream_id(
        company_id=alpha["id"], user_id=staff["alpha.sara"]
    )

    assert first == second

    service.post_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        channel_id=first,
        body="Morning, everyone.",
    )

    page = service.list_messages(
        company_id=alpha["id"], user_id=staff["alpha.sara"], channel_id=second
    )

    assert [message["body"] for message in page["items"]] == ["Morning, everyone."]


def test_a_message_may_carry_a_file_with_no_caption(service, alpha, staff):
    """A photo with no words is a message. Refusing it would refuse the
    attachment button the composer offers beside the text box."""
    channel_id = service.company_stream_id(
        company_id=alpha["id"], user_id=staff["alpha.rana"]
    )

    message = service.post_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        channel_id=channel_id,
        body="",
        attachment_url=f"/api/media/{alpha['id']}/receipt.png",
        attachment_type="image",
        attachment_filename="receipt.png",
    )

    assert message["attachment_url"].endswith("receipt.png")
    assert message["attachment_type"] == "image"


def test_a_message_with_neither_words_nor_a_file_is_refused(
    service, alpha, staff
):
    channel_id = service.company_stream_id(
        company_id=alpha["id"], user_id=staff["alpha.rana"]
    )

    with pytest.raises(ValueError):
        service.post_message(
            company_id=alpha["id"],
            user_id=staff["alpha.rana"],
            channel_id=channel_id,
            body="   ",
        )


def test_a_picked_mention_reaches_the_person_it_named(service, alpha, staff):
    """The composer resolves each `@name` to an id as it is chosen.

    That is the only way a name two colleagues both answer to reaches the right
    one — `extract_mentions` drops an ambiguous alias on purpose rather than
    guessing. The id has to be honoured for the picker to mean anything.
    """
    from backend.services.notification_service import notification_service

    channel_id = service.company_stream_id(
        company_id=alpha["id"], user_id=staff["alpha.rana"]
    )

    service.post_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        channel_id=channel_id,
        body="@Sara can you take the 3pm?",
        mentioned_user_ids=[staff["alpha.sara"]],
    )

    delivered = notification_service.list_for_user(
        company_id=alpha["id"],
        user_id=staff["alpha.sara"],
        notification_type="team_mention",
    )

    assert len(delivered) == 1, delivered


def test_a_mention_id_from_another_company_names_nobody(service, alpha, staff):
    """A hand-edited payload must not deliver the message text to a stranger."""
    from backend.services.notification_service import notification_service

    channel_id = service.company_stream_id(
        company_id=alpha["id"], user_id=staff["alpha.rana"]
    )

    message = service.post_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        channel_id=channel_id,
        body="Nothing here is for you.",
        mentioned_user_ids=[staff["beta.lina"]],
    )

    assert message["mentions"] == []
    assert not notification_service.list_for_user(
        company_id=alpha["id"],
        user_id=staff["beta.lina"],
        notification_type="team_mention",
    )


# ------------------------------------------------------------ direct messages


def test_a_direct_message_opened_twice_is_one_conversation(
    service, alpha, staff
):
    """Including from the other end. Two rows for one pair would split a
    conversation in half, each person seeing only what they started."""
    opened = service.get_or_create_dm(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        other_user_id=staff["alpha.sara"],
    )
    again = service.get_or_create_dm(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        other_user_id=staff["alpha.sara"],
    )
    from_the_other_side = service.get_or_create_dm(
        company_id=alpha["id"],
        user_id=staff["alpha.sara"],
        other_user_id=staff["alpha.rana"],
    )

    assert opened["id"] == again["id"] == from_the_other_side["id"]


def test_a_direct_message_is_titled_by_the_other_person(service, alpha, staff):
    """The same row reads "Sara Nasr" to Rana and "Rana Haddad" to Sara. A
    stored name is a key; a person is what a client should show."""
    room = service.get_or_create_dm(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        other_user_id=staff["alpha.sara"],
    )

    assert room["display_name"] == "Sara Nasr", room

    theirs = service.get_room(
        company_id=alpha["id"], user_id=staff["alpha.sara"], room_id=room["id"]
    )

    assert theirs["display_name"] == "Rana Haddad", theirs


def test_a_direct_message_cannot_be_opened_with_another_companys_employee(
    service, alpha, staff
):
    from backend.services.ownership import NotOwnedByCompany

    with pytest.raises(NotOwnedByCompany):
        service.get_or_create_dm(
            company_id=alpha["id"],
            user_id=staff["alpha.rana"],
            other_user_id=staff["beta.lina"],
        )


def test_a_direct_message_is_invisible_to_everybody_else(service, alpha, staff):
    """Two people talking privately is exactly what a third must not find."""
    from backend.services.team_chat_service import ChannelNotFound

    room = service.get_or_create_dm(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        other_user_id=staff["alpha.sara"],
    )
    service.post_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        channel_id=room["id"],
        body="Between us.",
    )

    assert service.list_rooms(
        company_id=alpha["id"], user_id=staff["alpha.karim"]
    ) == []

    with pytest.raises(ChannelNotFound):
        service.list_messages(
            company_id=alpha["id"],
            user_id=staff["alpha.karim"],
            channel_id=room["id"],
        )


# -------------------------------------------------------------------- groups


def test_a_group_keeps_the_name_it_was_given(service, alpha, staff):
    """The stored name is normalised for the uniqueness constraint. Showing
    "sales-team" to somebody who typed "Sales Team" is the key leaking into
    the interface."""
    room = service.create_group(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        name="Sales Team",
        member_user_ids=[staff["alpha.sara"]],
    )

    assert room["display_name"] == "Sales Team"
    assert room["kind"] == "group"
    assert sorted(member["id"] for member in room["members"]) == sorted(
        [staff["alpha.rana"], staff["alpha.sara"]]
    )


def test_two_groups_may_share_a_name(service, alpha, staff):
    """People name groups after the work, and the work repeats. A second
    "Sales Team" must be creatable, not refused by a storage key."""
    first = service.create_group(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        name="Sales Team",
        member_user_ids=[staff["alpha.sara"]],
    )
    second = service.create_group(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        name="Sales Team",
        member_user_ids=[staff["alpha.karim"]],
    )

    assert first["id"] != second["id"]
    assert first["display_name"] == second["display_name"] == "Sales Team"


def test_a_group_cannot_be_given_another_companys_employee(
    service, alpha, staff
):
    from backend.services.ownership import NotOwnedByCompany

    with pytest.raises(NotOwnedByCompany):
        service.create_group(
            company_id=alpha["id"],
            user_id=staff["alpha.rana"],
            name="Leak",
            member_user_ids=[staff["beta.lina"]],
        )


def test_a_group_of_one_is_refused(service, alpha, staff):
    with pytest.raises(ValueError):
        service.create_group(
            company_id=alpha["id"],
            user_id=staff["alpha.rana"],
            name="Just me",
            member_user_ids=[],
        )


def test_a_group_by_department_says_why_it_cannot(service, alpha, staff):
    """This platform records an employee's role and branch, not a department:
    `business_departments` is the customer's menu, not a roster. Building a
    private group from a guess is worse than saying so."""
    with pytest.raises(ValueError) as raised:
        service.create_group(
            company_id=alpha["id"],
            user_id=staff["alpha.rana"],
            name="Sales",
            department="sales",
        )

    assert "department" in str(raised.value).lower()


# ------------------------------------------------------------------ deletion


def test_only_the_author_withdraws_a_message(service, alpha, staff):
    from backend.services.team_chat_service import NotMessageAuthor

    channel_id = service.company_stream_id(
        company_id=alpha["id"], user_id=staff["alpha.rana"]
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        channel_id=channel_id,
        body="Said too quickly.",
    )

    with pytest.raises(NotMessageAuthor):
        service.delete_message(
            company_id=alpha["id"],
            user_id=staff["alpha.sara"],
            message_id=message["id"],
        )

    service.delete_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        message_id=message["id"],
    )

    page = service.list_messages(
        company_id=alpha["id"], user_id=staff["alpha.rana"], channel_id=channel_id
    )

    assert page["items"] == []


def test_a_message_in_a_room_you_are_not_in_is_not_found(service, alpha, staff):
    """"Not yours" would confirm the message exists, which is the leak."""
    from backend.services.team_chat_service import ChannelNotFound

    room = service.get_or_create_dm(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        other_user_id=staff["alpha.sara"],
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=staff["alpha.rana"],
        channel_id=room["id"],
        body="Between us.",
    )

    with pytest.raises(ChannelNotFound):
        service.delete_message(
            company_id=alpha["id"],
            user_id=staff["alpha.karim"],
            message_id=message["id"],
        )
