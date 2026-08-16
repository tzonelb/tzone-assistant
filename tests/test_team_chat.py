"""Tests for internal team chat.

This module is staff-only discussion: colleagues talking about customers, about
escalations, and about each other. The properties worth testing here are all
disclosure properties — who can see a private channel, whose company's messages
are reachable, and who a mention hands the message text to — so they are tested
against real encrypted databases rather than against mocks.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the team chat service — and everything it calls — at the test databases.

    The singleton is captured before the sweep and the rebinding is asserted,
    because a test that silently kept the real `database_manager` would pass
    while writing into the developer's own data directory.
    """
    import sys

    import backend.services.auth_service  # noqa: F401
    import backend.services.notification_service  # noqa: F401
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

    # The identity sweep above only catches modules still holding the singleton
    # this fixture captured. Team chat reads the employee roster and writes
    # notifications through two other modules, so those are pinned by name as
    # well: if either were left on a different manager, the mention tests would
    # quietly resolve names against a database that is not the one under test.
    for name in (
        "backend.services.auth_service",
        "backend.services.notification_service",
    ):
        monkeypatch.setattr(sys.modules[name], "database_manager", test_manager)
        assert sys.modules[name].database_manager is test_manager

    from backend.services.team_chat_service import team_chat_service

    return team_chat_service


@pytest.fixture()
def staff(platform):
    """Real employees in the control database, so mentions have someone to resolve to."""
    from database.manager import utc_now_iso

    manager = platform["manager"]
    people: dict[str, int] = {}

    roster = {
        "alpha": [
            ("rana", "Rana Haddad", "rana@alpha.test"),
            ("sara", "Sara Nasr", "sara@alpha.test"),
            ("karim", "Karim Aziz", "karim@alpha.test"),
        ],
        "beta": [
            ("lina", "Lina Saad", "lina@beta.test"),
        ],
    }

    with manager.control() as conn:
        now = utc_now_iso()

        for slug, members in roster.items():
            company_id = platform["companies"][slug]["id"]

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
                    VALUES (?, ?, NULL, 'active', ?)
                    """,
                    (company_id, user_id, now),
                )
                people[f"{slug}.{key}"] = user_id

        conn.commit()

    return people


def _mentions_for(user_id: int, company_id: int) -> list[dict]:
    from backend.services.notification_service import notification_service

    return notification_service.list_for_user(
        company_id=company_id,
        user_id=user_id,
        notification_type="team_mention",
    )


# ----------------------------------------------------------------------
# Private channels
# ----------------------------------------------------------------------


def test_a_private_channel_is_not_listed_to_a_non_member(service, alpha):
    """A private channel that appears in someone's sidebar has already leaked
    that colleagues are discussing something without them."""
    service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )

    visible = service.list_channels(company_id=alpha["id"], user_id=2)

    assert [channel["name"] for channel in visible] == []


def test_a_non_member_cannot_read_a_private_channel(service, alpha):
    """Unlisted is not the same as invisible. Fetching the channel by id must
    fail too, or the sidebar filter is the only thing protecting the content."""
    from backend.services.team_chat_service import ChannelNotFound

    channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )
    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        body="Complaint about a colleague.",
        employees=[],
    )

    with pytest.raises(ChannelNotFound):
        service.get_channel(company_id=alpha["id"], user_id=2, channel_id=channel["id"])

    with pytest.raises(ChannelNotFound):
        service.list_messages(
            company_id=alpha["id"], user_id=2, channel_id=channel["id"]
        )

    with pytest.raises(ChannelNotFound):
        service.list_members(
            company_id=alpha["id"], user_id=2, channel_id=channel["id"]
        )


def test_a_non_member_cannot_post_to_a_private_channel(service, alpha):
    """Writing into a private channel would put the outsider's text in front of
    people who never invited them, and hand them the thread in return."""
    from backend.services.team_chat_service import ChannelNotFound

    channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )

    with pytest.raises(ChannelNotFound):
        service.post_message(
            company_id=alpha["id"],
            user_id=2,
            channel_id=channel["id"],
            body="hello",
            employees=[],
        )

    assert (
        service.list_messages(
            company_id=alpha["id"], user_id=1, channel_id=channel["id"]
        )["total"]
        == 0
    )


def test_a_non_member_cannot_add_themselves_to_a_private_channel(service, alpha):
    """Self-service joining would make `is_private` decorative: anyone who
    guessed the id could walk in."""
    from backend.services.team_chat_service import ChannelNotFound

    channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )

    with pytest.raises(ChannelNotFound):
        service.join_channel(
            company_id=alpha["id"], user_id=2, channel_id=channel["id"]
        )

    assert service.is_member(
        company_id=alpha["id"], channel_id=channel["id"], user_id=2
    ) is False


def test_a_private_channel_fails_exactly_like_a_channel_that_does_not_exist(
    service, alpha
):
    """A distinct 'forbidden' answer still confirms the channel exists. The
    non-member must not be able to tell the two cases apart."""
    from backend.services.team_chat_service import ChannelNotFound

    channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )

    with pytest.raises(ChannelNotFound) as private_error:
        service.get_channel(company_id=alpha["id"], user_id=2, channel_id=channel["id"])

    with pytest.raises(ChannelNotFound) as missing_error:
        service.get_channel(company_id=alpha["id"], user_id=2, channel_id=999_999)

    assert str(private_error.value) == str(missing_error.value)


def test_an_invited_member_can_read_a_private_channel(service, alpha):
    """The privacy rule must still let the invited colleague in, or an invite
    does nothing."""
    channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )
    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        body="Case notes.",
        employees=[],
    )

    service.add_member(
        company_id=alpha["id"], actor_user_id=1, channel_id=channel["id"], user_id=2
    )

    page = service.list_messages(
        company_id=alpha["id"], user_id=2, channel_id=channel["id"]
    )

    assert page["total"] == 1
    assert page["items"][0]["body"] == "Case notes."


def test_leaving_a_private_channel_ends_access_immediately(service, alpha):
    """Membership is the access check itself, so removing it must remove the
    channel from view rather than only from the member list."""
    from backend.services.team_chat_service import ChannelNotFound

    channel = service.create_channel(
        company_id=alpha["id"],
        user_id=1,
        name="hr-cases",
        is_private=True,
        member_user_ids=[2],
    )

    assert service.leave_channel(
        company_id=alpha["id"], user_id=2, channel_id=channel["id"]
    )

    with pytest.raises(ChannelNotFound):
        service.list_messages(
            company_id=alpha["id"], user_id=2, channel_id=channel["id"]
        )


def test_a_public_channel_is_readable_without_joining(service, alpha):
    """Making every channel members-only would be safe and useless: a public
    channel exists so colleagues can find the discussion."""
    channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="general"
    )
    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        body="Morning all",
        employees=[],
    )

    page = service.list_messages(
        company_id=alpha["id"], user_id=2, channel_id=channel["id"]
    )

    assert page["total"] == 1
    assert [item["name"] for item in service.list_channels(
        company_id=alpha["id"], user_id=2
    )] == ["general"]


# ----------------------------------------------------------------------
# Tenant isolation
# ----------------------------------------------------------------------


def test_one_company_cannot_see_another_companys_channels(service, alpha, beta):
    """Team chat is company data like any other; the two databases must not
    bleed into one listing."""
    service.create_channel(company_id=alpha["id"], user_id=1, name="general")
    service.create_channel(company_id=beta["id"], user_id=1, name="beta-only")

    alpha_names = [
        channel["name"]
        for channel in service.list_channels(company_id=alpha["id"], user_id=1)
    ]
    beta_names = [
        channel["name"]
        for channel in service.list_channels(company_id=beta["id"], user_id=1)
    ]

    assert alpha_names == ["general"]
    assert beta_names == ["beta-only"]


def test_one_company_cannot_read_another_companys_messages(service, alpha, beta):
    """Channel ids restart at 1 in every tenant database, so a caller passing a
    plausible id must be stopped by the company scope, not by luck."""
    from backend.services.team_chat_service import ChannelNotFound

    alpha_channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="general"
    )
    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=alpha_channel["id"],
        body="Alpha internal note",
        employees=[],
    )

    # Same id, different company: Beta has nothing under it yet.
    with pytest.raises(ChannelNotFound):
        service.list_messages(
            company_id=beta["id"], user_id=1, channel_id=alpha_channel["id"]
        )

    beta_channel = service.create_channel(
        company_id=beta["id"], user_id=1, name="general"
    )
    beta_page = service.list_messages(
        company_id=beta["id"], user_id=1, channel_id=beta_channel["id"]
    )

    assert beta_page["total"] == 0


def test_the_same_channel_name_can_exist_in_two_companies(service, alpha, beta):
    """Uniqueness is per company. A global constraint would let one company's
    channel name block another company from creating its own."""
    from backend.services.team_chat_service import ChannelNameTaken

    service.create_channel(company_id=alpha["id"], user_id=1, name="general")
    service.create_channel(company_id=beta["id"], user_id=1, name="general")

    with pytest.raises(ChannelNameTaken):
        service.create_channel(company_id=alpha["id"], user_id=1, name="General")


# ----------------------------------------------------------------------
# Messages
# ----------------------------------------------------------------------


def test_a_message_is_stored_and_read_back_in_order(service, alpha):
    """The thread is worthless if it does not preserve the order people spoke in."""
    channel = service.create_channel(company_id=alpha["id"], user_id=1, name="general")

    for text in ("first", "second", "third"):
        service.post_message(
            company_id=alpha["id"],
            user_id=1,
            channel_id=channel["id"],
            body=text,
            employees=[],
        )

    page = service.list_messages(
        company_id=alpha["id"], user_id=1, channel_id=channel["id"]
    )

    assert [item["body"] for item in page["items"]] == ["first", "second", "third"]
    assert page["total"] == 3
    assert page["has_more"] is False


def test_pagination_returns_the_newest_page_and_a_cursor_backwards(service, alpha):
    """Opening a busy channel must not load its whole history, and paging back
    must not skip or repeat a message."""
    channel = service.create_channel(company_id=alpha["id"], user_id=1, name="general")

    for index in range(10):
        service.post_message(
            company_id=alpha["id"],
            user_id=1,
            channel_id=channel["id"],
            body=f"message {index}",
            employees=[],
        )

    newest = service.list_messages(
        company_id=alpha["id"], user_id=1, channel_id=channel["id"], limit=4
    )

    assert [item["body"] for item in newest["items"]] == [
        "message 6",
        "message 7",
        "message 8",
        "message 9",
    ]
    assert newest["has_more"] is True

    older = service.list_messages(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        limit=4,
        before_id=newest["next_before_id"],
    )

    assert [item["body"] for item in older["items"]] == [
        "message 2",
        "message 3",
        "message 4",
        "message 5",
    ]


def test_only_the_author_may_edit_a_message(service, alpha):
    """Editing a colleague's message would let anyone rewrite what someone else
    is recorded as having said."""
    from backend.services.team_chat_service import NotMessageAuthor

    channel = service.create_channel(company_id=alpha["id"], user_id=1, name="general")
    message = service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        body="original",
        employees=[],
    )

    with pytest.raises(NotMessageAuthor):
        service.edit_message(
            company_id=alpha["id"],
            user_id=2,
            message_id=message["id"],
            body="rewritten",
            employees=[],
        )

    edited = service.edit_message(
        company_id=alpha["id"],
        user_id=1,
        message_id=message["id"],
        body="corrected",
        employees=[],
    )

    assert edited["body"] == "corrected"
    assert edited["edited_at"]


def test_an_empty_message_is_refused(service, alpha):
    """Whitespace-only sends would fill the thread with blank rows nobody can
    read or delete."""
    channel = service.create_channel(company_id=alpha["id"], user_id=1, name="general")

    with pytest.raises(ValueError):
        service.post_message(
            company_id=alpha["id"],
            user_id=1,
            channel_id=channel["id"],
            body="   ",
            employees=[],
        )


# ----------------------------------------------------------------------
# Mentions
# ----------------------------------------------------------------------


def test_a_mention_notifies_the_named_colleague(service, alpha, staff):
    """Without the notification a mention is decoration: the person named is the
    one who has to act, and they are not watching the channel."""
    author = staff["alpha.rana"]
    mentioned = staff["alpha.sara"]

    channel = service.create_channel(
        company_id=alpha["id"], user_id=author, name="general"
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=author,
        channel_id=channel["id"],
        body="@Sara Nasr can you take this refund?",
        author_name="Rana Haddad",
    )

    assert message["mentions"] == [mentioned]

    inbox = _mentions_for(mentioned, alpha["id"])
    assert len(inbox) == 1
    assert inbox[0]["data"]["channel_id"] == channel["id"]
    assert inbox[0]["data"]["message_id"] == message["id"]
    assert inbox[0]["actor_user_id"] == author


def test_the_author_is_not_notified_for_mentioning_themselves(service, alpha, staff):
    """Self-notification makes the badge meaningless — every message you write
    would come back at you as something needing attention."""
    author = staff["alpha.rana"]
    colleague = staff["alpha.sara"]

    channel = service.create_channel(
        company_id=alpha["id"], user_id=author, name="general"
    )
    service.post_message(
        company_id=alpha["id"],
        user_id=author,
        channel_id=channel["id"],
        body="@Rana Haddad is on it, @Sara Nasr please review",
    )

    assert _mentions_for(author, alpha["id"]) == []
    assert len(_mentions_for(colleague, alpha["id"])) == 1


def test_a_mention_in_a_private_channel_never_reaches_a_non_member(
    service, alpha, staff
):
    """The notification carries the message text. Notifying someone outside the
    channel would hand them the contents of a discussion they cannot open —
    exactly the leak the private flag exists to prevent."""
    author = staff["alpha.rana"]
    member = staff["alpha.sara"]
    outsider = staff["alpha.karim"]

    channel = service.create_channel(
        company_id=alpha["id"],
        user_id=author,
        name="hr-cases",
        is_private=True,
        member_user_ids=[member],
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=author,
        channel_id=channel["id"],
        body="@Karim Aziz is the subject of this complaint, @Sara Nasr please handle",
    )

    assert message["mentions"] == [member]
    assert _mentions_for(outsider, alpha["id"]) == []
    assert len(_mentions_for(member, alpha["id"])) == 1


def test_a_mention_cannot_notify_another_companys_employee(service, alpha, beta, staff):
    """Names are resolved against this company's roster only. Resolving against
    the whole platform would deliver Alpha's internal text into Beta's inbox."""
    author = staff["alpha.rana"]
    outsider = staff["beta.lina"]

    channel = service.create_channel(
        company_id=alpha["id"], user_id=author, name="general"
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=author,
        channel_id=channel["id"],
        body="@Lina Saad please look at the Beta account",
    )

    assert message["mentions"] == []
    assert _mentions_for(outsider, beta["id"]) == []
    assert _mentions_for(outsider, alpha["id"]) == []


def test_the_same_person_written_two_ways_resolves_to_one_id(service, alpha, staff):
    """People type '@sara.nasr' and '@Sara Nasr'. Only resolving one form means
    half of all mentions silently notify nobody."""
    author = staff["alpha.rana"]
    sara = staff["alpha.sara"]

    channel = service.create_channel(
        company_id=alpha["id"], user_id=author, name="general"
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=author,
        channel_id=channel["id"],
        body="@sara.nasr and @Sara Nasr are the same person",
    )

    assert message["mentions"] == [sara]
    assert len(_mentions_for(sara, alpha["id"])) == 1


def test_an_ambiguous_name_is_not_guessed(service, alpha, staff, platform):
    """Two people answering to '@sara' must produce no mention rather than a
    coin flip that sends a colleague's message to the wrong person."""
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO users (email, password_hash, full_name, status, created_at, updated_at)
            VALUES ('sara2@alpha.test', 'x', 'Sara Khoury', 'active', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, NULL, 'active', ?)
            """,
            (alpha["id"], int(cursor.lastrowid), now),
        )
        conn.commit()

    author = staff["alpha.rana"]
    channel = service.create_channel(
        company_id=alpha["id"], user_id=author, name="general"
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=author,
        channel_id=channel["id"],
        body="@sara please check",
    )

    assert message["mentions"] == []
    assert _mentions_for(staff["alpha.sara"], alpha["id"]) == []


def test_editing_a_message_notifies_only_the_newly_mentioned(service, alpha, staff):
    """Re-notifying everyone on every typo fix trains the team to ignore
    mentions; never notifying means an added name is never told."""
    author = staff["alpha.rana"]
    sara = staff["alpha.sara"]
    karim = staff["alpha.karim"]

    channel = service.create_channel(
        company_id=alpha["id"], user_id=author, name="general"
    )
    message = service.post_message(
        company_id=alpha["id"],
        user_id=author,
        channel_id=channel["id"],
        body="@Sara Nasr please look",
    )

    service.edit_message(
        company_id=alpha["id"],
        user_id=author,
        message_id=message["id"],
        body="@Sara Nasr and @Karim Aziz please look",
    )

    assert len(_mentions_for(sara, alpha["id"])) == 1
    assert len(_mentions_for(karim, alpha["id"])) == 1


# ----------------------------------------------------------------------
# Unread state
# ----------------------------------------------------------------------


def test_unread_counts_are_per_user(service, alpha):
    """One shared counter would clear everybody's badge as soon as the first
    person opened the channel, and the rest would never know they were needed."""
    channel = service.create_channel(company_id=alpha["id"], user_id=1, name="general")
    service.join_channel(company_id=alpha["id"], user_id=2, channel_id=channel["id"])
    service.join_channel(company_id=alpha["id"], user_id=3, channel_id=channel["id"])

    for text in ("one", "two"):
        service.post_message(
            company_id=alpha["id"],
            user_id=1,
            channel_id=channel["id"],
            body=text,
            employees=[],
        )

    assert service.unread_counts(company_id=alpha["id"], user_id=2)["total"] == 2
    assert service.unread_counts(company_id=alpha["id"], user_id=3)["total"] == 2

    service.mark_read(company_id=alpha["id"], user_id=2, channel_id=channel["id"])

    assert service.unread_counts(company_id=alpha["id"], user_id=2)["total"] == 0
    assert service.unread_counts(company_id=alpha["id"], user_id=3)["total"] == 2


def test_your_own_message_is_never_unread_for_you(service, alpha):
    """A badge that lights up for what you just typed makes the badge useless."""
    channel = service.create_channel(company_id=alpha["id"], user_id=1, name="general")
    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        body="mine",
        employees=[],
    )

    assert service.unread_counts(company_id=alpha["id"], user_id=1)["total"] == 0


def test_unread_counts_ignore_channels_the_user_may_not_see(service, alpha):
    """A badge total that includes a private channel tells a non-member that
    something is happening in it."""
    private_channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )
    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=private_channel["id"],
        body="sensitive",
        employees=[],
    )

    summary = service.unread_counts(company_id=alpha["id"], user_id=2)

    assert summary["total"] == 0
    assert summary["channels"] == {}


# ----------------------------------------------------------------------
# Live stream
# ----------------------------------------------------------------------


def test_the_live_signature_changes_when_a_visible_message_arrives(service, alpha):
    """The SSE stream only rebuilds a page when this value moves. A constant
    signature is a screen that never updates."""
    channel = service.create_channel(company_id=alpha["id"], user_id=1, name="general")
    before = service.live_signature(company_id=alpha["id"], user_id=1)

    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        body="new",
        employees=[],
    )

    assert service.live_signature(company_id=alpha["id"], user_id=1) != before


def test_the_live_signature_does_not_move_for_a_private_channel_you_cannot_see(
    service, alpha
):
    """The signature is sent to every connected client. If private traffic moved
    it, an outsider's screen would flicker in time with a discussion they are
    not part of — a side channel that reveals activity and timing."""
    channel = service.create_channel(
        company_id=alpha["id"], user_id=1, name="hr-cases", is_private=True
    )
    before = service.live_signature(company_id=alpha["id"], user_id=2)

    service.post_message(
        company_id=alpha["id"],
        user_id=1,
        channel_id=channel["id"],
        body="sensitive",
        employees=[],
    )

    assert service.live_signature(company_id=alpha["id"], user_id=2) == before
    assert service.live_signature(company_id=alpha["id"], user_id=1) != before
