"""A demonstration arrives with sample data, and hands it back on activation.

Two halves, and the second is the one that matters. An empty demonstration
tells a prospective customer the platform does not work; a workspace that
became real and kept six invented customers among its real ones has a reporting
screen that lies to its owner.

"Delete everything from before the activation" is not the rule either -- they
will have added real things while trying the platform out, on the same channels
as the samples. So the seeder records every row it wrote, and activation
removes that list and nothing else. This file exercises exactly that: something
real is added alongside the samples, and it survives.
"""

from __future__ import annotations

import sys

import pytest

# Before any fixture patches `database.manager.database_manager`, and this list
# has to name every service the seeder reaches -- not just the seeder itself.
#
# `demo_seed_service` imports the catalogue, knowledge and customer services
# lazily, inside its methods, which means the *first* seed in a session imports
# them while the manager is patched. They then hold that test's database for
# the rest of the run and are never `is original` again, so no later fixture
# rebinds them. The symptom was a duplicate-SKU error and a customer count of
# zero in the second test onward, with every test passing alone.
import backend.services.catalogue_service  # noqa: E402,F401
import backend.services.customer_service  # noqa: E402,F401
import backend.services.demo_seed_service  # noqa: E402,F401
import backend.services.knowledge_service  # noqa: E402,F401
import backend.services.message_service  # noqa: E402,F401


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


def _count(company_id: int, table: str) -> int:
    from database.manager import database_manager

    with database_manager.tenant(company_id) as conn:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE company_id = ?",
                (company_id,),
            ).fetchone()["total"]
        )


def test_a_seeded_workspace_has_something_on_every_screen(wired, alpha):
    from backend.services.demo_seed_service import demo_seed_service

    demo_seed_service.seed(company_id=alpha["id"], owner_user_id=1)

    assert _count(alpha["id"], "conversations") >= 5
    assert _count(alpha["id"], "messages") >= 10
    assert _count(alpha["id"], "customers") >= 5


def test_the_sample_is_imperfect_on_purpose(wired, alpha):
    """A demonstration where everyone was answered shows none of the screens
    that exist to find the ones who were not."""
    from database.manager import database_manager
    from backend.services.demo_seed_service import demo_seed_service

    demo_seed_service.seed(company_id=alpha["id"], owner_user_id=1)

    with database_manager.tenant(alpha["id"]) as conn:
        unanswered = conn.execute(
            """
            SELECT COUNT(*) AS total FROM conversations
            WHERE company_id = ?
              AND id NOT IN (
                SELECT conversation_id FROM messages
                WHERE company_id = ? AND direction = 'outbound'
              )
            """,
            (alpha["id"], alpha["id"]),
        ).fetchone()["total"]

        assistant = conn.execute(
            "SELECT COUNT(*) AS total FROM messages "
            "WHERE company_id = ? AND sender_type = 'ai'",
            (alpha["id"],),
        ).fetchone()["total"]

        person = conn.execute(
            "SELECT COUNT(*) AS total FROM messages "
            "WHERE company_id = ? AND sender_type = 'employee'",
            (alpha["id"],),
        ).fetchone()["total"]

    assert unanswered >= 1, "nobody was left unanswered, so the report has nothing to find"
    assert assistant >= 1, "the assistant answered nothing, so automation reads 0%"
    assert person >= 1, "no employee replied, so the per-employee report is empty"


def test_the_messages_are_spread_over_days_not_one_second(wired, alpha):
    """A week of conversation stamped in one second gives the charts nothing."""
    from database.manager import database_manager
    from backend.services.demo_seed_service import demo_seed_service

    demo_seed_service.seed(company_id=alpha["id"], owner_user_id=1)

    with database_manager.tenant(alpha["id"]) as conn:
        days = conn.execute(
            "SELECT COUNT(DISTINCT substr(created_at, 1, 10)) AS days "
            "FROM messages WHERE company_id = ?",
            (alpha["id"],),
        ).fetchone()["days"]

    assert days >= 3, f"every seeded message landed on {days} day(s)"


def test_activation_removes_the_samples_and_keeps_what_the_owner_added(wired, alpha):
    """The property the ledger exists for."""
    from database.manager import database_manager
    from backend.services.demo_seed_service import demo_seed_service
    from backend.services.message_service import message_service

    demo_seed_service.seed(company_id=alpha["id"], owner_user_id=1)

    seeded_conversations = _count(alpha["id"], "conversations")

    # Something real, on the same channel as a sample, while still a demo --
    # through both halves of the inbound path, because that is what a real
    # message does. `save_message` alone leaves `conversations.customer_id`
    # null and creates no contact, which is the thing that made the seeder
    # produce a full inbox beside an empty Customers screen.
    from backend.services.customer_service import customer_service

    customer_service.upsert_from_channel(
        company_id=alpha["id"],
        channel="whatsapp",
        external_user_id="a-real-customer",
        display_name="A Real Customer",
    )
    message_service.save_message(
        company_id=alpha["id"],
        channel="whatsapp",
        external_user_id="a-real-customer",
        direction="inbound",
        text="This one actually happened.",
        sender_type="customer",
    )

    assert _count(alpha["id"], "conversations") == seeded_conversations + 1

    demo_seed_service.remove(company_id=alpha["id"])

    # The samples are gone.
    assert _count(alpha["id"], "conversations") == 1
    assert _count(alpha["id"], "customers") == 1

    # And the real one is exactly what is left.
    with database_manager.tenant(alpha["id"]) as conn:
        remaining = conn.execute(
            "SELECT external_user_id FROM conversations WHERE company_id = ?",
            (alpha["id"],),
        ).fetchall()

        text = conn.execute(
            "SELECT body FROM messages WHERE company_id = ? LIMIT 1",
            (alpha["id"],),
        ).fetchone()

    assert [row["external_user_id"] for row in remaining] == ["a-real-customer"]
    assert text is not None


def test_removing_twice_is_not_an_error(wired, alpha):
    """Activation can be retried; the ledger is emptied, not the rows guessed at."""
    from backend.services.demo_seed_service import demo_seed_service

    demo_seed_service.seed(company_id=alpha["id"], owner_user_id=1)
    demo_seed_service.remove(company_id=alpha["id"])

    assert demo_seed_service.remove(company_id=alpha["id"]) == {}


def test_the_ledger_can_only_name_tables_this_service_will_delete(wired, alpha):
    """A ledger row naming any table would be a way to delete arbitrary rows
    through a path the owner never sees, so the allowed set is in the code."""
    from database.manager import database_manager
    from backend.services.demo_seed_service import demo_seed_service
    from database.manager import utc_now_iso

    demo_seed_service.seed(company_id=alpha["id"], owner_user_id=1)

    with database_manager.tenant(alpha["id"]) as conn:
        conn.execute(
            "INSERT INTO demo_seeded_rows (company_id, table_name, row_id, created_at) "
            "VALUES (?, 'users', 1, ?)",
            (alpha["id"], utc_now_iso()),
        )
        conn.commit()

    removed = demo_seed_service.remove(company_id=alpha["id"])

    assert "users" not in removed
