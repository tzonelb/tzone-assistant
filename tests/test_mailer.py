"""Tests for outbound email.

The property that matters here is not "does mail arrive" — that depends on a
server this test cannot reach. It is: **does the platform tell the truth about
whether mail arrived.**

A mailer that logs an exception and returns quietly is worse than no mailer at
all in the flow that uses it. The administrator clicks "send reset link", sees
success, tells the employee to check their mail, and the employee waits for
something that was never sent. Both of them now believe a locked account is
recoverable when it is not.
"""

from __future__ import annotations

import pytest

from backend.services import mailer


@pytest.fixture()
def settings(monkeypatch):
    from config.settings import config

    return lambda **values: [
        monkeypatch.setattr(config, name, value) for name, value in values.items()
    ]


def test_the_console_backend_reports_delivery(settings):
    """What development and the test suite run on."""
    settings(EMAIL_BACKEND="console")

    result = mailer.send(to="someone@example.com", subject="Hi", body="Body")

    assert result.delivered
    assert result.backend == "console"
    assert bool(result) is True


def test_the_disabled_backend_refuses_and_says_why(settings):
    settings(EMAIL_BACKEND="disabled")

    result = mailer.send(to="someone@example.com", subject="Hi", body="Body")

    assert not result.delivered
    assert "switched off" in result.reason.lower()


def test_an_unrecognised_backend_refuses_rather_than_guessing(settings):
    """Guessing `smtp` would try to reach a server that may not exist; guessing
    `console` would silently stop sending real mail in production. A typo in
    this setting has to fail loudly in one direction, and refusing is the
    direction that cannot lose a message silently."""
    settings(EMAIL_BACKEND="smpt")

    assert mailer.configured_backend() == "disabled"
    assert not mailer.send(to="a@example.com", subject="s", body="b").delivered


def test_smtp_without_a_host_is_not_configured(settings):
    settings(EMAIL_BACKEND="smtp", SMTP_HOST="", SMTP_FROM="from@example.com")

    assert not mailer.is_configured()

    result = mailer.send(to="a@example.com", subject="s", body="b")

    assert not result.delivered
    assert "SMTP_HOST" in result.reason


def test_smtp_without_a_from_address_is_not_configured(settings):
    settings(EMAIL_BACKEND="smtp", SMTP_HOST="mail.example.com", SMTP_FROM="")

    assert not mailer.is_configured()
    assert "SMTP_FROM" in mailer.send(
        to="a@example.com", subject="s", body="b"
    ).reason


def test_assert_configured_names_the_way_back_in(settings):
    """The message an administrator sees when email is broken has to tell them
    what to do instead, because at that moment somebody is locked out."""
    settings(EMAIL_BACKEND="disabled")

    with pytest.raises(mailer.MailerNotConfigured) as raised:
        mailer.assert_configured()

    assert "manage_platform" in str(raised.value)


def test_assert_configured_is_silent_when_it_can_send(settings):
    settings(EMAIL_BACKEND="console")

    mailer.assert_configured()


def test_send_never_raises_even_when_the_server_is_unreachable(settings):
    """`send` returning a result rather than raising is the contract the
    callers are written against — they decide what a failure means, and for the
    reset endpoint it means a 503 that names the problem."""
    settings(
        EMAIL_BACKEND="smtp",
        SMTP_HOST="127.0.0.1",
        SMTP_PORT=1,
        SMTP_FROM="from@example.com",
        SMTP_USER="",
        SMTP_TIMEOUT_SECONDS=1,
        SMTP_STARTTLS=False,
    )

    result = mailer.send(to="a@example.com", subject="s", body="b")

    assert not result.delivered
    assert result.backend == "smtp"
    assert result.reason


def test_the_body_is_not_logged_by_the_smtp_backend(settings, caplog):
    """A reset link is a bearer credential for one account. The console backend
    prints it on purpose — that is what it is for — but the real one must not
    put it in a production log."""
    settings(
        EMAIL_BACKEND="smtp",
        SMTP_HOST="127.0.0.1",
        SMTP_PORT=1,
        SMTP_FROM="from@example.com",
        SMTP_USER="",
        SMTP_TIMEOUT_SECONDS=1,
        SMTP_STARTTLS=False,
    )

    secret = "https://app.example.com/reset-password/SECRET-TOKEN-VALUE"

    with caplog.at_level("DEBUG"):
        mailer.send(to="a@example.com", subject="Reset", body=secret)

    assert "SECRET-TOKEN-VALUE" not in caplog.text
