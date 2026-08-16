"""Reporting over a company's own conversation history.

Every figure is produced by an aggregate query inside the company's encrypted
database. Nothing is loaded into memory and counted in Python: a busy company
has hundreds of thousands of messages, and the reporting screen must not be the
thing that takes the server down.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


MAX_RANGE_DAYS = 365
DEFAULT_RANGE_DAYS = 30


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalyticsService:
    def _window(self, days: int) -> tuple[str, str, int]:
        """Clamp the requested range and return ISO bounds.

        Bounded because the range reaches the query as a scan limit; an
        unbounded window turns one dashboard load into a full-history scan.
        """
        # `days or DEFAULT` would be wrong: 0 is falsy, so an explicit 0 would
        # silently widen the window to the default instead of clamping to 1.
        requested = DEFAULT_RANGE_DAYS if days is None else int(days)
        safe_days = max(1, min(requested, MAX_RANGE_DAYS))
        end = utc_now()
        start = end - timedelta(days=safe_days)
        return start.isoformat(), end.isoformat(), safe_days

    def overview(self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS) -> dict[str, Any]:
        """Headline totals for the selected period."""
        company_id = int(company_id)
        start, end, safe_days = self._window(days)

        with database_manager.tenant(company_id) as conn:
            messages = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN direction = 'in' THEN 1 ELSE 0 END), 0) AS inbound,
                    COALESCE(SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END), 0) AS outbound,
                    COALESCE(SUM(CASE WHEN sender_type = 'ai' THEN 1 ELSE 0 END), 0) AS by_assistant,
                    COALESCE(SUM(CASE WHEN sender_type = 'employee' THEN 1 ELSE 0 END), 0) AS by_employee
                FROM messages
                WHERE created_at >= ? AND created_at <= ?
                """,
                (start, end),
            ).fetchone()

            conversations = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN handled_by_ai = 0 THEN 1 ELSE 0 END), 0) AS human_handled,
                    COALESCE(SUM(CASE WHEN unread_count > 0 THEN 1 ELSE 0 END), 0) AS unread,
                    COALESCE(SUM(CASE WHEN needs_human = 1 THEN 1 ELSE 0 END), 0) AS needs_human
                FROM conversations
                """
            ).fetchone()

            new_conversations = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM conversations WHERE created_at >= ?",
                    (start,),
                ).fetchone()["n"]
            )

            new_customers = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM customers WHERE first_seen_at >= ?",
                    (start,),
                ).fetchone()["n"]
            )

        total_messages = int(messages["total"])
        by_assistant = int(messages["by_assistant"])
        outbound = int(messages["outbound"])

        return {
            "range_days": safe_days,
            "from": start,
            "to": end,
            "messages": {
                "total": total_messages,
                "inbound": int(messages["inbound"]),
                "outbound": outbound,
                "by_assistant": by_assistant,
                "by_employee": int(messages["by_employee"]),
                # Share of replies the assistant handled without a human.
                "automation_rate": (
                    round(by_assistant / outbound, 4) if outbound else 0.0
                ),
            },
            "conversations": {
                "total": int(conversations["total"]),
                "new_in_range": new_conversations,
                "human_handled": int(conversations["human_handled"]),
                "unread": int(conversations["unread"]),
                "needs_human": int(conversations["needs_human"]),
            },
            "customers": {"new_in_range": new_customers},
        }

    def volume_by_day(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS
    ) -> list[dict[str, Any]]:
        """Daily message counts, split inbound and outbound."""
        start, end, _ = self._window(days)

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT
                    substr(created_at, 1, 10) AS day,
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN direction = 'in' THEN 1 ELSE 0 END), 0) AS inbound,
                    COALESCE(SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END), 0) AS outbound
                FROM messages
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY day
                ORDER BY day
                """,
                (start, end),
            ).fetchall()

        return [
            {
                "day": row["day"],
                "total": int(row["total"]),
                "inbound": int(row["inbound"]),
                "outbound": int(row["outbound"]),
            }
            for row in rows
        ]

    def by_channel(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS
    ) -> list[dict[str, Any]]:
        """Which channels the company's customers actually use."""
        start, end, _ = self._window(days)

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT
                    m.channel,
                    COUNT(*) AS messages,
                    COUNT(DISTINCT m.conversation_id) AS conversations
                FROM messages m
                WHERE m.created_at >= ? AND m.created_at <= ?
                GROUP BY m.channel
                ORDER BY messages DESC
                """,
                (start, end),
            ).fetchall()

        return [
            {
                "channel": row["channel"],
                "messages": int(row["messages"]),
                "conversations": int(row["conversations"]),
            }
            for row in rows
        ]

    def hourly_distribution(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS
    ) -> list[dict[str, Any]]:
        """When customers write, so staffing can match demand."""
        start, end, _ = self._window(days)

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT substr(created_at, 12, 2) AS hour, COUNT(*) AS messages
                FROM messages
                WHERE direction = 'in' AND created_at >= ? AND created_at <= ?
                GROUP BY hour
                ORDER BY hour
                """,
                (start, end),
            ).fetchall()

        counts = {str(row["hour"]): int(row["messages"]) for row in rows}

        # Every hour is present even when empty, so the chart keeps a stable
        # 24-slot axis instead of collapsing quiet hours.
        return [
            {"hour": f"{hour:02d}", "messages": counts.get(f"{hour:02d}", 0)}
            for hour in range(24)
        ]

    def assistant_health(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS
    ) -> dict[str, Any]:
        """Assistant reliability and speed, from the diagnostics trail."""
        start, end, _ = self._window(days)

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT event_type, COUNT(*) AS total,
                       AVG(duration_ms) AS avg_duration
                FROM diagnostic_events
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY event_type
                """,
                (start, end),
            ).fetchall()

            pending = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM pending_replies"
                ).fetchone()["n"]
            )

        events = {
            str(row["event_type"]): {
                "count": int(row["total"]),
                "average_ms": int(row["avg_duration"]) if row["avg_duration"] else None,
            }
            for row in rows
        }

        sent = events.get("ai_reply_sent", {}).get("count", 0)
        failed = events.get("ai_reply_error", {}).get("count", 0)
        attempted = sent + failed

        return {
            "replies_sent": sent,
            "replies_failed": failed,
            "failure_rate": round(failed / attempted, 4) if attempted else 0.0,
            "average_reply_ms": events.get("ai_reply_sent", {}).get("average_ms"),
            "handovers_to_human": events.get(
                "ai_buffer_waiting_for_human_timeout", {}
            ).get("count", 0),
            "queued_now": pending,
            "events": events,
        }

    def employee_activity(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS
    ) -> list[dict[str, Any]]:
        """Per-employee reply counts and takeovers.

        Returns user ids only. Names live in the control database and are
        resolved by the caller in one batched query, because a tenant database
        cannot join across to `users`.
        """
        start, end, _ = self._window(days)

        with database_manager.tenant(int(company_id)) as conn:
            replies = conn.execute(
                """
                SELECT sender_user_id AS user_id, COUNT(*) AS replies
                FROM messages
                WHERE sender_type = 'employee'
                  AND sender_user_id IS NOT NULL
                  AND created_at >= ? AND created_at <= ?
                GROUP BY sender_user_id
                """,
                (start, end),
            ).fetchall()

            takeovers = conn.execute(
                """
                SELECT actor_user_id AS user_id, COUNT(*) AS takeovers
                FROM conversation_events
                WHERE event_type IN ('human_took_over', 'assigned_user_changed')
                  AND actor_user_id IS NOT NULL
                  AND created_at >= ? AND created_at <= ?
                GROUP BY actor_user_id
                """,
                (start, end),
            ).fetchall()

        combined: dict[int, dict[str, Any]] = {}

        for row in replies:
            combined[int(row["user_id"])] = {
                "user_id": int(row["user_id"]),
                "replies": int(row["replies"]),
                "takeovers": 0,
            }

        for row in takeovers:
            user_id = int(row["user_id"])
            entry = combined.setdefault(
                user_id, {"user_id": user_id, "replies": 0, "takeovers": 0}
            )
            entry["takeovers"] = int(row["takeovers"])

        return sorted(
            combined.values(), key=lambda item: item["replies"], reverse=True
        )

    def first_response_times(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS
    ) -> dict[str, Any]:
        """How long a customer waits for the first reply.

        Measured per conversation as the gap between its first inbound message
        and the first outbound message that follows, which is the number a
        customer actually experiences.
        """
        start, end, _ = self._window(days)

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                WITH first_in AS (
                    SELECT conversation_id, MIN(created_at) AS asked_at
                    FROM messages
                    WHERE direction = 'in' AND created_at >= ? AND created_at <= ?
                    GROUP BY conversation_id
                )
                SELECT
                    first_in.conversation_id,
                    first_in.asked_at,
                    (
                        SELECT MIN(m.created_at) FROM messages m
                        WHERE m.conversation_id = first_in.conversation_id
                          AND m.direction = 'out'
                          AND m.created_at > first_in.asked_at
                    ) AS answered_at
                FROM first_in
                """,
                (start, end),
            ).fetchall()

        durations: list[float] = []

        for row in rows:
            if not row["answered_at"]:
                continue

            try:
                asked = datetime.fromisoformat(row["asked_at"])
                answered = datetime.fromisoformat(row["answered_at"])
            except (TypeError, ValueError):
                continue

            durations.append((answered - asked).total_seconds())

        if not durations:
            return {
                "answered": 0,
                "unanswered": len(rows),
                "average_seconds": None,
                "median_seconds": None,
            }

        durations.sort()
        middle = len(durations) // 2
        median = (
            durations[middle]
            if len(durations) % 2
            else (durations[middle - 1] + durations[middle]) / 2
        )

        return {
            "answered": len(durations),
            "unanswered": len(rows) - len(durations),
            "average_seconds": round(sum(durations) / len(durations), 1),
            "median_seconds": round(median, 1),
        }


analytics_service = AnalyticsService()
