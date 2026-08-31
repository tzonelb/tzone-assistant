"""Sign-up is a form anybody on the internet can post to, so it is throttled.

Every workspace it creates costs an encrypted database file, a set of roles and
an owner. A form that does all that for free is a way to fill the volume, so
two things stand in front of it: a code sent to an address the caller must be
able to read, and a ceiling on how many workspaces one address may create.

What is tested here is the refusing, because the succeeding is the easy half.
A code that is wrong, expired, guessed at repeatedly, replayed after use, or
requested to accumulate several valid codes at once -- and a mailer that cannot
deliver, which must refuse out loud rather than report "sent" to a screen that
then waits for a code nobody will ever receive.
"""

from __future__ import annotations

import sys

import pytest

# Before any fixture patches `database.manager.database_manager`; a module
# first imported inside that window binds the test manager permanently.
import backend.services.catalogue_service  # noqa: E402,F401
import backend.services.customer_service  # noqa: E402,F401
import backend.services.demo_seed_service  # noqa: E402,F401
import backend.services.knowledge_service  # noqa: E402,F401
import backend.services.mailer  # noqa: E402,F401
import backend.services.message_service  # noqa: E402,F401
import backend.services.platform_service  # noqa: E402,F401
import backend.services.signup_service  # noqa: E402,F401


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.demo_gate import demo_gate

    demo_gate.invalidate()
    yield test_manager
    demo_gate.invalidate()


@pytest.fixture()
def posted(monkeypatch):
    """Capture what would have been emailed, and report the mailer as ready."""
    from backend.services import mailer

    sent: list[dict] = []

    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(
        mailer,
        "send",
        lambda **kwargs: sent.append(kwargs) or mailer.DeliveryResult(
            delivered=True, backend="test"
        ),
    )

    return sent


def _a_minute_passes() -> None:
    """Age the send-log so the resend cooldown no longer applies.

    The cooldown is real and tested on its own below; a test about *what a code
    does* should not have to sleep through it, so it moves the clock instead of
    waiting on it.
    """
    from database.manager import database_manager

    with database_manager.control() as conn:
        conn.execute(
            "UPDATE signup_code_sends SET created_at = '2000-01-01T00:00:00+00:00'"
        )
        conn.commit()


def _code_from(sent: list[dict]) -> str:
    import re

    match = re.search(r"\b(\d{6})\b", sent[-1]["body"])

    assert match, sent[-1]["body"]

    return match.group(1)


# ------------------------------------------------------------------ the code


def test_a_code_is_emailed_and_stored_only_as_a_hash(wired, posted):
    from database.manager import database_manager
    from backend.services.signup_service import signup_service

    signup_service.send_code(email="Owner@Example.COM")

    code = _code_from(posted)

    with database_manager.control() as conn:
        row = dict(conn.execute("SELECT * FROM signup_codes").fetchone())

    # Lower-cased on the way in, so the same address is one row however typed.
    assert row["email"] == "owner@example.com"

    for column, value in row.items():
        assert code not in str(value), (
            f"the readable code was stored in signup_codes.{column}"
        )


def test_a_mailer_that_cannot_deliver_refuses_out_loud(wired, monkeypatch):
    """"Sent" that was never sent leaves a screen waiting forever."""
    from backend.services import mailer
    from backend.services.signup_service import SignupError, signup_service

    monkeypatch.setattr(mailer, "is_configured", lambda: False)

    with pytest.raises(SignupError) as refusal:
        signup_service.send_code(email="owner@example.com")

    assert "email" in str(refusal.value).lower()


def test_asking_again_replaces_the_code_rather_than_adding_one(wired, posted):
    """Otherwise a caller collects valid codes by pressing the button."""
    from database.manager import database_manager
    from backend.services.signup_service import signup_service

    signup_service.send_code(email="owner@example.com")
    first = _code_from(posted)

    _a_minute_passes()
    signup_service.send_code(email="owner@example.com")
    second = _code_from(posted)

    assert first != second

    with database_manager.control() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM signup_codes"
        ).fetchone()["total"] == 1

    from backend.services.signup_service import SignupError

    with pytest.raises(SignupError):
        signup_service.create_demo_workspace(
            company_name="Cedar",
            owner_full_name="Rana",
            owner_email="owner@example.com",
            password="a-long-enough-password",
            email_code=first,
        )


def test_a_code_cannot_be_guessed_indefinitely(wired, posted):
    from backend.services.signup_service import (
        MAX_ATTEMPTS,
        SignupError,
        signup_service,
    )

    signup_service.send_code(email="owner@example.com")
    real = _code_from(posted)

    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(SignupError):
            signup_service.create_demo_workspace(
                company_name="Cedar",
                owner_full_name="Rana",
                owner_email="owner@example.com",
                password="a-long-enough-password",
                email_code="000000",
            )

    # Past the ceiling the code is dead, even though this one is correct.
    with pytest.raises(SignupError):
        signup_service.create_demo_workspace(
            company_name="Cedar",
            owner_full_name="Rana",
            owner_email="owner@example.com",
            password="a-long-enough-password",
            email_code=real,
        )


def test_a_code_cannot_be_used_twice(wired, posted):
    from backend.services.signup_service import SignupError, signup_service

    signup_service.send_code(email="owner@example.com")
    code = _code_from(posted)

    signup_service.create_demo_workspace(
        company_name="Cedar Home",
        owner_full_name="Rana",
        owner_email="owner@example.com",
        password="a-long-enough-password",
        email_code=code,
    )

    with pytest.raises(SignupError):
        signup_service.create_demo_workspace(
            company_name="Cedar Two",
            owner_full_name="Rana",
            owner_email="owner@example.com",
            password="a-long-enough-password",
            email_code=code,
        )


# ------------------------------------------------------------- the workspace


def test_the_workspace_it_creates_is_a_demonstration(wired, posted):
    """The whole point: sign-up cannot produce something that can send."""
    from backend.services.demo_gate import demo_gate
    from backend.services.signup_service import signup_service

    signup_service.send_code(email="owner@example.com")

    created = signup_service.create_demo_workspace(
        company_name="Cedar Home Appliances",
        owner_full_name="Rana",
        owner_email="owner@example.com",
        password="a-long-enough-password",
        email_code=_code_from(posted),
    )

    assert created["is_demo"] is True
    assert demo_gate.is_demo(created["company_id"]) is True


def test_a_short_password_is_refused_at_the_platforms_own_minimum(wired, posted):
    from backend.services.auth_service import auth_service
    from backend.services.signup_service import SignupError, signup_service

    signup_service.send_code(email="owner@example.com")

    with pytest.raises(SignupError):
        signup_service.create_demo_workspace(
            company_name="Cedar",
            owner_full_name="Rana",
            owner_email="owner@example.com",
            password="x" * (auth_service.MIN_PASSWORD_LENGTH - 1),
            email_code=_code_from(posted),
        )


def test_one_address_cannot_create_workspaces_without_end(wired, posted):
    from backend.services.signup_service import (
        MAX_WORKSPACES_PER_EMAIL,
        SignupError,
        signup_service,
    )

    for index in range(MAX_WORKSPACES_PER_EMAIL):
        _a_minute_passes()
        signup_service.send_code(email="owner@example.com")
        signup_service.create_demo_workspace(
            company_name=f"Cedar {index}",
            owner_full_name="Rana",
            owner_email="owner@example.com",
            password="a-long-enough-password",
            email_code=_code_from(posted),
        )

    _a_minute_passes()
    signup_service.send_code(email="owner@example.com")

    with pytest.raises(SignupError) as refusal:
        signup_service.create_demo_workspace(
            company_name="Cedar too many",
            owner_full_name="Rana",
            owner_email="owner@example.com",
            password="a-long-enough-password",
            email_code=_code_from(posted),
        )

    assert "maximum" in str(refusal.value).lower()


def test_asking_for_a_code_says_the_same_thing_for_a_known_address(wired, posted):
    """Otherwise this endpoint answers "does this person have an account"."""
    from backend.services.signup_service import signup_service

    fresh = signup_service.send_code(email="nobody@example.com")

    signup_service.send_code(email="owner@example.com")
    signup_service.create_demo_workspace(
        company_name="Cedar Home",
        owner_full_name="Rana",
        owner_email="owner@example.com",
        password="a-long-enough-password",
        email_code=_code_from(posted),
    )

    _a_minute_passes()
    known = signup_service.send_code(email="owner@example.com")

    assert set(fresh) == set(known)
    assert fresh["sent"] == known["sent"] is True


# ---------------------------------------------------------- send throttling


def test_hammering_the_send_endpoint_does_not_email_a_victim_repeatedly(wired, posted):
    """`send_code` emails on every call, so without a cooldown a loop against a
    victim's address is an email-bombing tool that also drains the send quota."""
    from backend.services.signup_service import SignupError, signup_service

    signup_service.send_code(email="victim@example.com", ip_address="203.0.113.9")

    assert len(posted) == 1

    for _ in range(20):
        with pytest.raises(SignupError):
            signup_service.send_code(
                email="victim@example.com", ip_address="203.0.113.9"
            )

    # Twenty more attempts, one email. The cooldown held.
    assert len(posted) == 1


def test_one_source_cannot_email_a_whole_list_of_victims(wired, posted):
    """The per-address cooldown must not be side-stepped by walking a list."""
    from backend.services.signup_service import (
        MAX_SENDS_PER_IP_PER_HOUR,
        SignupError,
        signup_service,
    )

    # Each address is new, so the per-address cooldown never fires -- only the
    # per-source cap can stop this.
    sent = 0

    for i in range(MAX_SENDS_PER_IP_PER_HOUR + 5):
        try:
            signup_service.send_code(
                email=f"target{i}@example.com", ip_address="198.51.100.4"
            )
            sent += 1
        except SignupError:
            break

    assert sent <= MAX_SENDS_PER_IP_PER_HOUR, sent


def test_a_different_source_is_not_punished_for_the_first_ones_flood(wired, posted):
    """The cap is per source, so one abuser does not lock out everyone else."""
    from backend.services.signup_service import (
        MAX_SENDS_PER_IP_PER_HOUR,
        SignupError,
        signup_service,
    )

    for i in range(MAX_SENDS_PER_IP_PER_HOUR + 2):
        try:
            signup_service.send_code(
                email=f"a{i}@example.com", ip_address="198.51.100.4"
            )
        except SignupError:
            pass

    # A genuine new customer, from a different address, still gets a code.
    result = signup_service.send_code(
        email="genuine@example.com", ip_address="203.0.113.50"
    )

    assert result["sent"] is True
