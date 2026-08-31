"""Conversation ownership and the human-takeover handover.

Every test here runs the real service against a real, freshly provisioned,
encrypted per-company database from ``tests/conftest.py``. Nothing about
ownership is mocked: the guarantees below are enforced by SQL predicates inside
``BEGIN IMMEDIATE`` transactions, so a test that stubbed the database would
prove nothing at all.
"""

from __future__ import annotations

import sys
from datetime import timedelta

import pytest

import database.manager as manager_module
from backend.services.conversation_control_service import (
    DEFAULT_TAKEOVER_MINUTES,
    ConversationControlService,
    ConversationOwnershipConflict,
    conversation_control_service,
    parse_datetime,
    utc_now,
)


CHANNEL = "messenger"
CUSTOMER = "customer-ownership-1"
OTHER_CUSTOMER = "customer-ownership-2"

EMPLOYEE_A = 101
EMPLOYEE_B = 202
ADMIN = 999


@pytest.fixture()
def svc(platform, monkeypatch):
    """Point every copy of the ``database_manager`` singleton at the test platform.

    Services do ``from database.manager import database_manager``, so each
    module holds its *own* reference to the singleton. Patching only
    ``database.manager.database_manager`` would leave the services talking to
    the process-wide manager rooted at the real data directory, and these tests
    would pass while exercising nothing. The assertions below fail loudly if
    that ever silently stops working.
    """
    original = manager_module.database_manager
    manager = platform["manager"]

    # Import the modules under test so they are present in sys.modules and get
    # swept below, whatever import order the rest of the suite happened to use.
    import backend.services.company_settings_service  # noqa: F401
    import backend.services.conversation_control_service  # noqa: F401
    import backend.services.message_service  # noqa: F401

    monkeypatch.setattr(manager_module, "database_manager", manager)

    patched: set[str] = set()
    for name, module in list(sys.modules.items()):
        if module is None or module is manager_module:
            continue
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", manager)
            patched.add(name)

    assert "backend.services.conversation_control_service" in patched
    assert "backend.services.company_settings_service" in patched

    return conversation_control_service


def _row(platform, company_id: int, conversation_id: int) -> dict:
    """Read a conversation straight out of the company's encrypted database."""
    with platform["manager"].tenant(company_id) as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def _set_expiry(platform, company_id: int, conversation_id: int, value: str) -> None:
    """Force a conversation's takeover deadline, simulating elapsed time."""
    with platform["manager"].tenant(company_id) as conn:
        conn.execute(
            "UPDATE conversations SET takeover_expires_at = ? WHERE id = ?",
            (value, conversation_id),
        )
        conn.commit()


# ----------------------------------------------------------------------
# Taking a conversation over
# ----------------------------------------------------------------------


def test_first_takeover_assigns_the_conversation_to_that_employee(svc, alpha, platform):
    """Taking over used only to flip an `ai_enabled` flag with no owner recorded,
    so the inbox could not tell who was answering and no later check had anyone
    to compare against."""
    result = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    assert result["assigned_user_id"] == EMPLOYEE_A
    assert result["handled_by_ai"] is False
    assert result["takeover_expires_at"] is not None

    # Read the row back out of the encrypted tenant file, so a service that
    # never reached the database could not make this test pass.
    stored = _row(platform, alpha["id"], result["id"])
    assert stored["assigned_user_id"] == EMPLOYEE_A
    assert stored["handled_by_ai"] == 0
    assert stored["ai_enabled"] == 0
    assert stored["status"] == "human_handling"


def test_second_employee_takeover_raises_conflict(svc, alpha):
    """Two employees grabbing the same conversation used to silently overwrite
    each other's ownership, so both answered the same customer and only the
    second one's name appeared anywhere."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    with pytest.raises(ConversationOwnershipConflict) as exc_info:
        svc.set_ai_mode(
            company_id=alpha["id"],
            channel=CHANNEL,
            external_user_id=CUSTOMER,
            handled_by_ai=False,
            actor_user_id=EMPLOYEE_B,
        )

    assert exc_info.value.owner_user_id == EMPLOYEE_A

    state = svc.get_state(alpha["id"], CHANNEL, CUSTOMER)
    assert state["assigned_user_id"] == EMPLOYEE_A


def test_takeover_of_another_companys_conversation_is_a_separate_conversation(
    svc, alpha, beta
):
    """Ownership is scoped per company database. A conflict raised for one
    tenant must never block an unrelated tenant whose customer id happens to
    collide — the old shared table keyed conversations by channel + external id
    across every company."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    beta_state = svc.set_ai_mode(
        company_id=beta["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_B,
    )

    assert beta_state["assigned_user_id"] == EMPLOYEE_B
    assert svc.get_state(alpha["id"], CHANNEL, CUSTOMER)["assigned_user_id"] == EMPLOYEE_A


def test_return_to_ai_after_takeover_clears_the_owner(svc, alpha):
    """Handing a conversation back to the assistant has to drop the human owner
    and the takeover deadline too. Leaving either behind left conversations that
    the AI answered while still showing as locked to an employee."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    result = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=True,
        actor_user_id=EMPLOYEE_A,
    )

    assert result["handled_by_ai"] is True
    assert result["assigned_user_id"] is None
    assert result["takeover_expires_at"] is None


# ----------------------------------------------------------------------
# Releasing
# ----------------------------------------------------------------------


def test_owner_can_release_then_second_employee_can_take_over(svc, alpha):
    """Release has to actually clear ownership. When it did not, a conversation
    stayed locked to whoever touched it first until a server restart."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )
    svc.release(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        actor_user_id=EMPLOYEE_A,
        force=False,
    )

    result = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_B,
    )

    assert result["assigned_user_id"] == EMPLOYEE_B


def test_non_owner_cannot_release(svc, alpha):
    """Any employee could previously release any conversation, which dropped a
    colleague's active chat back into the queue mid-reply."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    with pytest.raises(ConversationOwnershipConflict) as exc_info:
        svc.release(
            company_id=alpha["id"],
            channel=CHANNEL,
            external_user_id=CUSTOMER,
            actor_user_id=EMPLOYEE_B,
            force=False,
        )

    assert exc_info.value.owner_user_id == EMPLOYEE_A
    assert svc.get_state(alpha["id"], CHANNEL, CUSTOMER)["assigned_user_id"] == EMPLOYEE_A


def test_admin_can_force_release_another_employees_conversation(svc, alpha):
    """The ownership lock must still have an escape hatch: without a forced
    release, a conversation held by an employee who logged off stayed
    unreachable for everyone including managers."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    result = svc.release(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        actor_user_id=ADMIN,
        force=True,
    )

    assert result["assigned_user_id"] is None


def test_release_starts_a_timeout_instead_of_returning_to_ai_immediately(svc, alpha):
    """Release means "open to any employee for N minutes", not an instant
    hand-back to the assistant. Clearing takeover_expires_at here meant a
    released conversation sat human-owned by nobody forever, because the expiry
    worker had no deadline to act on."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    result = svc.release(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        actor_user_id=EMPLOYEE_A,
        force=False,
    )

    assert result["assigned_user_id"] is None
    assert result["handled_by_ai"] is False
    assert result["takeover_expires_at"] is not None


def test_second_employee_can_still_claim_a_released_conversation_before_timeout(
    svc, alpha
):
    """The release timer is a fallback for when nobody claims the chat, not an
    exclusive lock. Treating the pending deadline as ownership blocked every
    other employee from picking the conversation up."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )
    svc.release(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        actor_user_id=EMPLOYEE_A,
        force=False,
    )

    result = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_B,
    )

    assert result["assigned_user_id"] == EMPLOYEE_B


# ----------------------------------------------------------------------
# Expiry
# ----------------------------------------------------------------------


def test_released_conversation_auto_returns_to_ai_after_timeout(svc, alpha, platform):
    """Nobody claims a released conversation inside the window, so it must go
    back to the assistant on its own. Without the sweep, released conversations
    were answered by no one and the customer was left waiting indefinitely."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )
    state = svc.release(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        actor_user_id=EMPLOYEE_A,
        force=False,
    )

    _set_expiry(
        platform,
        alpha["id"],
        state["id"],
        (utc_now() - timedelta(minutes=1)).isoformat(),
    )

    assert svc.expire_overdue_takeovers(company_id=alpha["id"]) == 1

    final = svc.get_state(alpha["id"], CHANNEL, CUSTOMER)
    assert final["handled_by_ai"] is True
    assert final["assigned_user_id"] is None
    assert final["takeover_expires_at"] is None


def test_expiry_sweep_is_scoped_to_one_company(svc, alpha, beta, platform):
    """The sweep takes a company_id and opens only that company's database. An
    unscoped sweep over a shared table returned other tenants' conversations to
    the AI while their employees were mid-conversation."""
    for company in (alpha, beta):
        state = svc.set_ai_mode(
            company_id=company["id"],
            channel=CHANNEL,
            external_user_id=CUSTOMER,
            handled_by_ai=False,
            actor_user_id=EMPLOYEE_A,
        )
        _set_expiry(
            platform,
            company["id"],
            state["id"],
            (utc_now() - timedelta(minutes=1)).isoformat(),
        )

    assert svc.expire_overdue_takeovers(company_id=alpha["id"]) == 1

    assert svc.get_state(alpha["id"], CHANNEL, CUSTOMER)["handled_by_ai"] is True
    beta_state = svc.get_state(beta["id"], CHANNEL, CUSTOMER)
    assert beta_state["handled_by_ai"] is False
    assert beta_state["assigned_user_id"] == EMPLOYEE_A


def test_expiry_does_not_steal_a_conversation_taken_over_mid_sweep(
    svc, alpha, platform, monkeypatch
):
    """Regression: the sweep reads the overdue rows first and writes them one by
    one, so an employee who takes a conversation over in between had it yanked
    straight back to the AI — reachable in practice because the sweep used to
    run on almost every read.

    The race is reproduced for real here, not asserted about: `insert_event` is
    called from inside the sweep's own transaction and on its own connection, so
    the hook below performs the competing takeover exactly in the window between
    the read and the write of the second conversation.
    """
    first = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )
    second = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=OTHER_CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    stale = (utc_now() - timedelta(minutes=1)).isoformat()
    for conversation_id in (first["id"], second["id"]):
        _set_expiry(platform, alpha["id"], conversation_id, stale)

    renewed = (utc_now() + timedelta(minutes=DEFAULT_TAKEOVER_MINUTES)).isoformat()
    ids = {int(first["id"]), int(second["id"])}
    raced: list[int] = []

    original_insert_event = ConversationControlService.insert_event

    def racing_insert_event(self, conn, conversation_id, company_id, actor_user_id,
                            event_type, data=None):
        if not raced:
            # The sweep has just expired one conversation and is still holding
            # its transaction open. A second employee grabs the *other* one now.
            victim = next(iter(ids - {int(conversation_id)}))
            raced.append(victim)
            conn.execute(
                """
                UPDATE conversations
                SET assigned_user_id = ?,
                    takeover_expires_at = ?
                WHERE id = ?
                """,
                (EMPLOYEE_B, renewed, victim),
            )
        return original_insert_event(
            self,
            conn=conn,
            conversation_id=conversation_id,
            company_id=company_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            data=data,
        )

    monkeypatch.setattr(
        ConversationControlService, "insert_event", racing_insert_event
    )

    expired = svc.expire_overdue_takeovers(company_id=alpha["id"])

    assert raced, "the race hook never fired; the test proved nothing"
    assert expired == 1

    stolen = _row(platform, alpha["id"], raced[0])
    assert stolen["assigned_user_id"] == EMPLOYEE_B
    assert stolen["handled_by_ai"] == 0
    assert stolen["takeover_expires_at"] == renewed

    returned_id = next(iter(ids - {raced[0]}))
    returned = _row(platform, alpha["id"], returned_id)
    assert returned["handled_by_ai"] == 1
    assert returned["assigned_user_id"] is None


# ----------------------------------------------------------------------
# Renewing the lease
# ----------------------------------------------------------------------


def test_renew_reply_lease_extends_the_takeover_expiry(svc, alpha, platform):
    """An employee typing a long reply used to lose the conversation to the
    expiry sweep mid-sentence, because nothing pushed the deadline forward while
    they were still working. (`renew_reply_lease` was also called by
    manual_messages.py before it existed at all, 500-ing every manual reply.)"""
    state = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )

    about_to_lapse = (utc_now() - timedelta(seconds=1)).isoformat()
    _set_expiry(platform, alpha["id"], state["id"], about_to_lapse)

    renewed = svc.renew_reply_lease(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        actor_user_id=EMPLOYEE_A,
    )

    new_expiry = parse_datetime(renewed["takeover_expires_at"])
    assert new_expiry is not None
    assert new_expiry > parse_datetime(about_to_lapse)
    assert new_expiry >= utc_now() + timedelta(minutes=DEFAULT_TAKEOVER_MINUTES - 1)
    assert renewed["assigned_user_id"] == EMPLOYEE_A

    # The renewed lease must actually survive the sweep it was extended against.
    assert svc.expire_overdue_takeovers(company_id=alpha["id"]) == 0
    still_owned = svc.get_state(alpha["id"], CHANNEL, CUSTOMER)
    assert still_owned["assigned_user_id"] == EMPLOYEE_A
    assert still_owned["handled_by_ai"] is False


def test_renew_reply_lease_rejects_a_non_owner(svc, alpha, platform):
    """Renewal must not double as a way to seize a colleague's conversation: an
    unchecked renewal let any employee keep another employee's chat alive (and,
    with the same UPDATE, quietly reset its deadline)."""
    state = svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )
    before = _row(platform, alpha["id"], state["id"])["takeover_expires_at"]

    with pytest.raises(ConversationOwnershipConflict) as exc_info:
        svc.renew_reply_lease(
            company_id=alpha["id"],
            channel=CHANNEL,
            external_user_id=CUSTOMER,
            actor_user_id=EMPLOYEE_B,
        )

    assert exc_info.value.owner_user_id == EMPLOYEE_A

    after = _row(platform, alpha["id"], state["id"])
    assert after["takeover_expires_at"] == before
    assert after["assigned_user_id"] == EMPLOYEE_A


def test_renew_reply_lease_rejects_an_ai_handled_conversation(svc, alpha):
    """A conversation that has gone back to the assistant has no lease to renew.
    Renewing one re-created a human lock with no owner, which the expiry sweep
    then had to clean up on the next pass."""
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=False,
        actor_user_id=EMPLOYEE_A,
    )
    svc.set_ai_mode(
        company_id=alpha["id"],
        channel=CHANNEL,
        external_user_id=CUSTOMER,
        handled_by_ai=True,
        actor_user_id=EMPLOYEE_A,
    )

    with pytest.raises(ConversationOwnershipConflict):
        svc.renew_reply_lease(
            company_id=alpha["id"],
            channel=CHANNEL,
            external_user_id=CUSTOMER,
            actor_user_id=EMPLOYEE_A,
        )

    state = svc.get_state(alpha["id"], CHANNEL, CUSTOMER)
    assert state["handled_by_ai"] is True
    assert state["takeover_expires_at"] is None
