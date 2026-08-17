"""A scheduled post goes out through the page the company chose.

`scheduled_posts.channel_account_id` shipped with the scheduler, was accepted
by the create endpoint, written to the row, and read by nothing. The publisher
called `resolve(company_id, channel)`, which returns the company's
lowest-numbered active account on that channel.

For a company with one Facebook page that is the same page, which is why it
went unnoticed. For a company with two, the post went to the wrong audience —
the same defect as a setting that saves and decides nothing, except this one
publishes to the company's followers.

The column was also unvalidated, and channel accounts live in the control
database where ids are global. That mattered less while nothing read the
column. Wiring the publisher to it is exactly what makes it matter: an id from
another company would have been an instruction to post through that company's
page, using that company's token. So the check lands in the same change as the
feature, not after it.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import backend.services.scheduler_service  # noqa: F401
    import channels.credentials  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.scheduler_service" in rebound
    assert "backend.services.channel_account_service" in rebound

    from backend.services.channel_account_service import channel_account_service
    from backend.services.scheduler_service import scheduler_service

    return scheduler_service, channel_account_service


def _connect(accounts, company_id, page_id, *, channel="messenger"):
    return accounts.create_account(
        company_id=company_id,
        channel=channel,
        name=f"Page {page_id}",
        values={"page_id": page_id, "access_token": f"token-for-{page_id}"},
    )


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


def _beta(platform):
    return platform["companies"]["beta"]["id"]


# ------------------------------------------------------------------ the write


def test_a_post_can_name_one_of_the_companys_own_accounts(wired, platform):
    scheduler, accounts = wired
    company = _alpha(platform)

    _connect(accounts, company, "PAGE-FIRST")
    second = _connect(accounts, company, "PAGE-SECOND")

    post = scheduler.create_post(
        company_id=company,
        channel="messenger",
        body="Hello",
        scheduled_for="2030-01-01T00:00:00+00:00",
        created_by_user_id=1,
        channel_account_id=second["id"],
    )

    assert post["channel_account_id"] == second["id"]


def test_another_companys_account_is_refused(wired, platform):
    scheduler, accounts = wired
    from backend.services.scheduler_service import SchedulerError

    foreign = _connect(accounts, _beta(platform), "BETA-PAGE")
    _connect(accounts, _alpha(platform), "ALPHA-PAGE")

    with pytest.raises(SchedulerError, match="does not belong to this company"):
        scheduler.create_post(
            company_id=_alpha(platform),
            channel="messenger",
            body="Hello",
            scheduled_for="2030-01-01T00:00:00+00:00",
            created_by_user_id=1,
            channel_account_id=foreign["id"],
        )


def test_an_account_on_a_different_channel_is_refused(wired, platform):
    """Not a leak — the company owns both. It is a post that could never go
    out, and the publisher would only discover that after the moment it was
    supposed to be published."""
    scheduler, accounts = wired
    from backend.services.scheduler_service import SchedulerError

    company = _alpha(platform)
    whatsapp = accounts.create_account(
        company_id=company,
        channel="whatsapp",
        name="WhatsApp",
        values={"phone_number_id": "PN-1", "access_token": "token"},
    )

    with pytest.raises(SchedulerError, match="not on the channel"):
        scheduler.create_post(
            company_id=company,
            channel="messenger",
            body="Hello",
            scheduled_for="2030-01-01T00:00:00+00:00",
            created_by_user_id=1,
            channel_account_id=whatsapp["id"],
        )


def test_no_account_is_still_allowed(wired, platform):
    """Most companies have one page and never choose. Requiring the field would
    break every existing post."""
    scheduler, accounts = wired
    company = _alpha(platform)
    _connect(accounts, company, "PAGE-ONLY")

    post = scheduler.create_post(
        company_id=company,
        channel="messenger",
        body="Hello",
        scheduled_for="2030-01-01T00:00:00+00:00",
        created_by_user_id=1,
    )

    assert post["channel_account_id"] is None


def test_changing_the_channel_clears_the_account(wired, platform):
    """`channel` was editable and `channel_account_id` was not, so a post moved
    from Messenger to WhatsApp kept pointing at a Messenger page."""
    scheduler, accounts = wired
    company = _alpha(platform)

    page = _connect(accounts, company, "PAGE-ONE")
    accounts.create_account(
        company_id=company,
        channel="whatsapp",
        name="WhatsApp",
        values={"phone_number_id": "PN-1", "access_token": "token"},
    )

    post = scheduler.create_post(
        company_id=company,
        channel="messenger",
        body="Hello",
        scheduled_for="2030-01-01T00:00:00+00:00",
        created_by_user_id=1,
        channel_account_id=page["id"],
    )

    moved = scheduler.update_post(
        company_id=company, post_id=post["id"], values={"channel": "whatsapp"}
    )

    assert moved["channel"] == "whatsapp"
    assert moved["channel_account_id"] is None


def test_an_edit_that_does_not_touch_the_channel_keeps_the_account(wired, platform):
    """Clearing on every edit would be worse than never honouring it."""
    scheduler, accounts = wired
    company = _alpha(platform)
    page = _connect(accounts, company, "PAGE-ONE")

    post = scheduler.create_post(
        company_id=company,
        channel="messenger",
        body="Hello",
        scheduled_for="2030-01-01T00:00:00+00:00",
        created_by_user_id=1,
        channel_account_id=page["id"],
    )

    edited = scheduler.update_post(
        company_id=company, post_id=post["id"], values={"body": "Hello again"}
    )

    assert edited["channel_account_id"] == page["id"]


# ------------------------------------------------------- credentials honour it


def test_credentials_come_from_the_named_account(wired, platform):
    """The heart of it. Without the account id the first page always wins."""
    scheduler, accounts = wired
    company = _alpha(platform)

    _connect(accounts, company, "PAGE-FIRST")
    second = _connect(accounts, company, "PAGE-SECOND")

    from channels.credentials import resolve

    assert resolve(company, "messenger")["page_id"] == "PAGE-FIRST"
    assert resolve(company, "messenger", second["id"])["page_id"] == "PAGE-SECOND"


def test_another_companys_account_id_cannot_fetch_its_token(wired, platform):
    """Defence in depth for a row stored before the check existed. Refusing to
    send is right: the caller asked for a page, and quietly using a different
    one is the failure being fixed."""
    scheduler, accounts = wired

    foreign = _connect(accounts, _beta(platform), "BETA-PAGE")
    _connect(accounts, _alpha(platform), "ALPHA-PAGE")

    from channels.credentials import MissingChannelCredentials, resolve

    with pytest.raises(MissingChannelCredentials):
        resolve(_alpha(platform), "messenger", foreign["id"])


def test_a_disabled_account_does_not_send(wired, platform):
    scheduler, accounts = wired
    company = _alpha(platform)

    _connect(accounts, company, "PAGE-FIRST")
    second = _connect(accounts, company, "PAGE-SECOND")
    accounts.update_account(
        company_id=company, account_id=second["id"], values={"status": "disabled"}
    )

    from channels.credentials import MissingChannelCredentials, resolve

    with pytest.raises(MissingChannelCredentials):
        resolve(company, "messenger", second["id"])


# --------------------------------------------------------------- the publisher


def test_the_publisher_posts_to_the_chosen_page(wired, platform, monkeypatch):
    """End to end, which is the only level at which the original defect was
    visible: every layer in isolation looked correct."""
    scheduler, accounts = wired
    company = _alpha(platform)

    _connect(accounts, company, "PAGE-FIRST")
    second = _connect(accounts, company, "PAGE-SECOND")

    post = scheduler.create_post(
        company_id=company,
        channel="messenger",
        body="Hello",
        scheduled_for="2000-01-01T00:00:00+00:00",  # already due
        created_by_user_id=1,
        channel_account_id=second["id"],
    )
    scheduler.approve(company_id=company, post_id=post["id"], approver_user_id=1)

    import channels.post_publisher as publisher

    sent: list[str] = []

    class _Response:
        content = b"{}"
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {"id": "provider-1"}

    def fake_post(url, **kwargs):
        sent.append(url)
        return _Response()

    monkeypatch.setattr(publisher.httpx, "post", fake_post)
    monkeypatch.setattr(
        publisher.module_gate, "enabled", lambda company_id, module: True
    )

    assert publisher.publish_due_posts(company) == 1
    assert len(sent) == 1
    assert "PAGE-SECOND/feed" in sent[0], (
        f"published to the wrong page: {sent[0]}"
    )
