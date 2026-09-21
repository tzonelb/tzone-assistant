"""Analytics reads a company's whole message history by date range.

`analytics_service.py`'s `volume_by_day`, `by_channel`, `hourly_distribution`
and its summary counts all filter `messages` on `created_at` alone -- no
`conversation_id`, no `channel` -- so neither `idx_messages_conversation`
(led by `conversation_id`) nor `idx_messages_lookup` (led by `channel`) can
serve them. Without an index led by `created_at` itself, every analytics
screen open was a full table scan of the company's entire message history.
"""

from __future__ import annotations


def test_the_index_exists(alpha, platform):
    with platform["manager"].tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'index' AND name = 'idx_messages_created_at'"
        ).fetchone()

    assert row is not None


def test_a_date_range_scan_uses_the_index_not_a_full_table_scan(alpha, platform):
    with platform["manager"].tenant(alpha["id"]) as conn:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT COUNT(*) FROM messages WHERE created_at >= ? AND created_at <= ?
            """,
            ("2024-01-01", "2024-01-31"),
        ).fetchall()

    detail = " ".join(str(row["detail"]) for row in plan)
    assert "idx_messages_created_at" in detail
