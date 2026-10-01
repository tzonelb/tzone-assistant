"""Tests for email as a channel a company connects.

Email is the odd one out among the channels this platform now supports for a
different reason than website chat: every other channel either derives its
routing identifier from a token (Telegram, Slack, Discord) or generates one
server-side (webchat) — a mailbox address is the one routing value an
operator types in, because there is no provider to ask "which address is
this". What stands in for that "ask the provider" step instead is a real
IMAP login (`channel_account_service.verify_imap_login`), made once at
connect time.

Email is also the one channel with no webhook at all: `channels/email/
poller.py` polls a mailbox rather than waiting for a provider to call this
platform back, which is also why `database_manager.resolve_account_for_
channel` carries no "email" entry — there is no anonymous delivery for it to
route, unlike every channel tested in `test_a_channel_routes_only_on_its_
guarded_id.py`.

What this file tests, in order: the account (a real IMAP login is required,
non-secret settings round-trip through `config_json`), the poller (a message
is fetched, stored through the real shared inbound pipeline, and marked
`\\Seen` only after it is), the sender (SMTP is called with the right
credentials and threading headers), and that the shared dispatcher reaches it
the same way every other channel's send path does.
"""

from __future__ import annotations

import email.message
import imaplib

import pytest


IMAP_HOST = "imap.example.com"
SMTP_HOST = "smtp.example.com"
MAILBOX_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import backend.services.message_service  # noqa: F401
    import channels.credentials  # noqa: F401
    import channels.email.poller  # noqa: F401
    import channels.email.sender  # noqa: F401
    import channels.inbound  # noqa: F401
    import channels.sender  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.channel_account_service",
        "channels.email.poller",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


def _fake_imap_login_ok(monkeypatch, *, expected_password: str = MAILBOX_PASSWORD):
    """A fake IMAP4_SSL that accepts exactly one address/password pair."""
    import backend.services.channel_account_service as service_module

    class FakeConnection:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, address, password):
            if password != expected_password:
                raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] bad credentials")

        def logout(self):
            pass

    monkeypatch.setattr(service_module.imaplib, "IMAP4_SSL", FakeConnection)


def _connect(company, monkeypatch, *, address="support@company.example", password=MAILBOX_PASSWORD):
    from backend.services.channel_account_service import channel_account_service

    _fake_imap_login_ok(monkeypatch, expected_password=password)

    return channel_account_service.create_account(
        company_id=company["id"],
        channel="email",
        name="Support inbox",
        values={
            "external_account_id": address,
            "access_token": password,
            "imap_host": IMAP_HOST,
            "smtp_host": SMTP_HOST,
        },
    )


# ------------------------------------------------------------------ the account


def test_connecting_requires_a_working_imap_login(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    _fake_imap_login_ok(monkeypatch, expected_password=MAILBOX_PASSWORD)

    with pytest.raises(ChannelAccountError, match="rejected"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="email",
            name="Support inbox",
            values={
                "external_account_id": "support@company.example",
                "access_token": "the-wrong-password",
                "imap_host": IMAP_HOST,
                "smtp_host": SMTP_HOST,
            },
        )


def test_connecting_requires_mail_server_settings(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    _fake_imap_login_ok(monkeypatch)

    with pytest.raises(ChannelAccountError, match="SMTP"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="email",
            name="Support inbox",
            values={
                "external_account_id": "support@company.example",
                "access_token": MAILBOX_PASSWORD,
                "imap_host": IMAP_HOST,
                # smtp_host missing
            },
        )


def test_a_connected_mailbox_stores_its_settings_but_seals_the_password(
    wired, platform, alpha, monkeypatch
):
    account = _connect(alpha, monkeypatch)

    assert account["external_account_id"] == "support@company.example"
    assert account["has_access_token"] is True
    assert account["config"]["imap_host"] == IMAP_HOST
    assert account["config"]["imap_port"] == 993
    assert account["config"]["smtp_host"] == SMTP_HOST
    assert account["config"]["smtp_port"] == 587

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed, config_json FROM channel_accounts "
            "WHERE channel = 'email'"
        ).fetchone()

    assert row["access_token_sealed"] is not None
    assert MAILBOX_PASSWORD not in row["config_json"]


def test_two_companies_cannot_claim_the_same_mailbox(wired, alpha, beta, monkeypatch):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    _connect(alpha, monkeypatch)

    _fake_imap_login_ok(monkeypatch)
    with pytest.raises(ChannelAccountError, match="already connected"):
        channel_account_service.create_account(
            company_id=beta["id"],
            channel="email",
            name="Also support",
            values={
                "external_account_id": "support@company.example",
                "access_token": MAILBOX_PASSWORD,
                "imap_host": IMAP_HOST,
                "smtp_host": SMTP_HOST,
            },
        )


def test_updating_a_setting_keeps_the_rest(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import channel_account_service

    account = _connect(alpha, monkeypatch)

    updated = channel_account_service.update_account(
        company_id=alpha["id"],
        account_id=account["id"],
        values={"smtp_port": 465},
    )

    assert updated["config"]["smtp_port"] == 465
    # Untouched settings survive the merge.
    assert updated["config"]["imap_host"] == IMAP_HOST
    assert updated["config"]["smtp_host"] == SMTP_HOST


# ------------------------------------------------------------------ the poller


def _raw_email(*, from_addr, subject, body, message_id="<abc123@example.com>"):
    message = email.message.EmailMessage()
    message["From"] = from_addr
    message["To"] = "support@company.example"
    message["Subject"] = subject

    if message_id:
        message["Message-ID"] = message_id

    message.set_content(body)
    return message.as_bytes()


class FakeMailbox:
    """Enough of `imaplib.IMAP4_SSL` for `poll_account` to run against."""

    def __init__(self, messages):
        # messages: list of (uid: bytes, raw: bytes)
        self._messages = list(messages)
        self.seen_uids = []

    def login(self, address, password):
        pass

    def select(self, mailbox):
        return ("OK", [b"1"])

    def search(self, charset, criterion):
        uids = b" ".join(uid for uid, _ in self._messages)
        return ("OK", [uids])

    def fetch(self, uid, parts):
        for candidate_uid, raw in self._messages:
            if candidate_uid == uid:
                return ("OK", [(b"1 (BODY[] {%d}" % len(raw), raw)])
        return ("NO", [])

    def store(self, uid, flag_action, flags):
        self.seen_uids.append(uid)

    def close(self):
        pass

    def logout(self):
        pass


def test_an_unseen_message_is_stored_through_the_shared_pipeline(
    wired, alpha, monkeypatch
):
    from backend.services.message_service import message_service
    import channels.email.poller as poller_module

    account = _connect(alpha, monkeypatch)

    mailbox = FakeMailbox(
        [
            (
                b"101",
                _raw_email(
                    from_addr="Jane Customer <jane@customer.example>",
                    subject="Question about my order",
                    body="Where is my order #482?",
                ),
            )
        ]
    )
    monkeypatch.setattr(poller_module, "_connect", lambda config: mailbox)

    poller_module.poll_account(account["id"])

    messages = message_service.list_messages(
        company_id=alpha["id"], channel="email", external_user_id="jane@customer.example"
    )

    assert len(messages) == 1
    assert messages[0]["text"] == "Where is my order #482?"
    assert messages[0]["direction"] == "in"
    assert messages[0]["provider_message_id"] == "<abc123@example.com>"
    assert messages[0]["metadata"]["email_subject"] == "Question about my order"

    # Marked \Seen only after it was actually stored.
    assert mailbox.seen_uids == [b"101"]


def test_a_message_with_no_sender_address_is_skipped(wired, alpha, monkeypatch):
    from backend.services.message_service import message_service
    import channels.email.poller as poller_module

    account = _connect(alpha, monkeypatch)

    mailbox = FakeMailbox(
        [(b"1", _raw_email(from_addr="", subject="junk", body="junk"))]
    )
    monkeypatch.setattr(poller_module, "_connect", lambda config: mailbox)

    poller_module.poll_account(account["id"])

    # Never stored, and never marked \Seen either -- there was nowhere to
    # route it, so it is left for a human to look at directly.
    assert mailbox.seen_uids == []


def test_polling_a_disabled_account_does_nothing(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import channel_account_service
    import channels.email.poller as poller_module

    account = _connect(alpha, monkeypatch)
    channel_account_service.update_account(
        company_id=alpha["id"], account_id=account["id"], values={"status": "disabled"}
    )

    calls = []
    monkeypatch.setattr(
        poller_module, "_connect", lambda config: calls.append(config) or FakeMailbox([])
    )

    poller_module.poll_account(account["id"])

    assert calls == []


def test_a_message_with_no_message_id_still_gets_a_dedup_key(
    wired, alpha, monkeypatch
):
    """RFC 5322 does not require Message-ID, and some senders omit it.

    `idx_messages_provider`'s uniqueness is `WHERE provider_message_id IS NOT
    NULL`, so a NULL id sails through dedup untouched -- reprocessing the
    same raw message (the crash window `_process_one`'s own docstring
    describes, between storing it and marking it `\\Seen`) would otherwise
    land as a second, identical customer message with nothing to catch it.
    """
    from backend.services.message_service import message_service
    import channels.email.poller as poller_module

    account = _connect(alpha, monkeypatch)

    mailbox = FakeMailbox(
        [
            (
                b"201",
                _raw_email(
                    from_addr="Sam Customer <sam@customer.example>",
                    subject="No Message-ID here",
                    body="This mail server never set one.",
                    message_id=None,
                ),
            )
        ]
    )
    monkeypatch.setattr(poller_module, "_connect", lambda config: mailbox)

    poller_module.poll_account(account["id"])

    messages = message_service.list_messages(
        company_id=alpha["id"], channel="email", external_user_id="sam@customer.example"
    )

    assert len(messages) == 1
    assert messages[0]["provider_message_id"], (
        "a message with no Message-ID header stored with no dedup key at all"
    )


def test_reprocessing_the_same_headerless_message_is_not_duplicated(
    wired, alpha, monkeypatch
):
    """The scenario the fallback id exists for: the same raw bytes, with no
    Message-ID, arriving twice -- the shape of `_process_one`'s own
    store-then-mark-seen crash window reprocessing on the next sweep."""
    from backend.services.message_service import message_service
    import channels.email.poller as poller_module

    account = _connect(alpha, monkeypatch)

    raw = _raw_email(
        from_addr="Sam Customer <sam@customer.example>",
        subject="No Message-ID here",
        body="This mail server never set one.",
        message_id=None,
    )

    mailbox = FakeMailbox([(b"301", raw)])
    monkeypatch.setattr(poller_module, "_connect", lambda config: mailbox)
    poller_module.poll_account(account["id"])

    # A second sweep sees the same message again -- as it would if the
    # process died after storing it but before the \Seen flag below was set.
    mailbox_again = FakeMailbox([(b"301", raw)])
    monkeypatch.setattr(poller_module, "_connect", lambda config: mailbox_again)
    poller_module.poll_account(account["id"])

    messages = message_service.list_messages(
        company_id=alpha["id"], channel="email", external_user_id="sam@customer.example"
    )

    assert len(messages) == 1, (
        "the same headerless message was stored twice -- the fallback dedup "
        "key did not catch the reprocessing it exists for"
    )


# ------------------------------------------------------------------ the sender


def test_send_looks_up_credentials_and_calls_smtp(wired, alpha, monkeypatch):
    from backend.services.message_service import message_service
    import channels.email.sender as sender_module

    _connect(alpha, monkeypatch)

    # A prior inbound message, so the reply can thread against it.
    message_service.save_message(
        company_id=alpha["id"],
        channel="email",
        external_user_id="jane@customer.example",
        direction="in",
        text="Where is my order?",
        provider_message_id="<abc123@example.com>",
        metadata={"email_subject": "Question about my order"},
    )

    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def starttls(self, context=None):
            captured["starttls"] = True

        def login(self, address, password):
            captured["login"] = (address, password)

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(sender_module.smtplib, "SMTP", FakeSMTP)

    result = sender_module.send_email_text(
        recipient_id="jane@customer.example",
        text="It shipped yesterday.",
        company_id=alpha["id"],
    )

    assert result["ok"] is True
    assert captured["host"] == SMTP_HOST
    assert captured["port"] == 587
    assert captured["starttls"] is True
    assert captured["login"] == ("support@company.example", MAILBOX_PASSWORD)

    sent = captured["message"]
    assert sent["To"] == "jane@customer.example"
    assert sent["From"] == "support@company.example"
    assert sent["Subject"] == "Re: Question about my order"
    assert sent["In-Reply-To"] == "<abc123@example.com>"


def test_send_fails_cleanly_with_no_connected_mailbox(wired, alpha):
    import channels.email.sender as sender_module

    result = sender_module.send_email_text(
        recipient_id="jane@customer.example",
        text="hello",
        company_id=alpha["id"],
    )

    assert result["ok"] is False
    assert result["skipped"] is False


# --------------------------------------------------------------- the dispatcher


def test_email_is_reachable_through_the_shared_dispatcher():
    from channels.sender import SUPPORTED_CHANNELS

    assert "email" in SUPPORTED_CHANNELS


def test_send_text_actually_calls_the_email_sender(monkeypatch):
    """The same gap pinned for Slack, Discord and webchat: being listed in
    `SUPPORTED_CHANNELS` does not by itself prove `send_text` calls
    anything."""
    import channels.sender as sender_module

    captured = {}

    def fake_send_email_text(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr(sender_module, "send_email_text", fake_send_email_text)

    result = sender_module.send_text(
        channel="email", recipient_id="jane@customer.example", company_id=1, text="hi"
    )

    assert result["ok"] is True
    assert captured["recipient_id"] == "jane@customer.example"


# ------------------------------------------------------------ no webhook to route

def test_email_has_no_inbound_routing_entry(wired):
    """Unlike every webhook channel, there is no anonymous delivery for
    `resolve_account_for_channel` to route -- the poller already knows the
    company and account before it opens a connection."""
    assert (
        wired.resolve_account_for_channel(channel="email", page_id="support@company.example")
        is None
    )
