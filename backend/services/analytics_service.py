"""Reporting over a company's own conversation history.

Every figure is produced by an aggregate query inside the company's encrypted
database. Nothing is loaded into memory and counted in Python: a busy company
has hundreds of thousands of messages, and the reporting screen must not be the
thing that takes the server down.
"""

from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from backend.services.conversation_control_service import (
    EVENT_ASSIGNMENT_CHANGED,
    EVENT_HUMAN_TAKEOVER,
)
from database.manager import database_manager


logger = logging.getLogger(__name__)


MAX_RANGE_DAYS = 365
DEFAULT_RANGE_DAYS = 30

# The section code a conversation carries before anyone has routed it. Stored
# as `conversations.department`'s default too, so the report and the inbox
# agree on what "not sorted yet" is called.
UNASSIGNED_DEPARTMENT = "Unassigned"

# How many named rows the slowest-wait lists carry. Enough to act on, short
# enough that the report stays a summary rather than becoming an export.
SLOWEST_SAMPLE = 8

# Nearest-rank percentiles. p50 is the median, and p90/p95 are where the
# customers an average silently absorbs actually show up.
PERCENTILES: tuple[tuple[str, float], ...] = (
    ("p50", 0.50),
    ("p75", 0.75),
    ("p90", 0.90),
    ("p95", 0.95),
)

# Upper bound in seconds, and the label. The last bucket is open-ended.
WAIT_BUCKETS: tuple[tuple[str, float | None], ...] = (
    ("under 1 min", 60.0),
    ("1-5 min", 300.0),
    ("5-15 min", 900.0),
    ("15-60 min", 3600.0),
    ("1-4 hours", 14400.0),
    ("over 4 hours", None),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def percentile(sorted_values: list[float], share: float) -> float:
    """Nearest-rank percentile over an already-sorted list.

    No interpolation: every value returned is a wait some real customer
    actually had, which is what makes it answerable.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty distribution")

    rank = max(1, min(len(sorted_values), math.ceil(share * len(sorted_values))))
    return sorted_values[rank - 1]


def empty_wait_buckets() -> list[dict[str, Any]]:
    """The buckets with nothing in them.

    Present even when empty so the chart keeps its axis instead of collapsing
    to whichever bands happen to have data.
    """
    return [{"label": label, "conversations": 0} for label, _ in WAIT_BUCKETS]


def bucket_waits(durations: list[float]) -> list[dict[str, Any]]:
    """Group waits into readable bands."""
    buckets = empty_wait_buckets()

    for seconds in durations:
        for index, (_, upper) in enumerate(WAIT_BUCKETS):
            if upper is None or seconds < upper:
                buckets[index]["conversations"] += 1
                break

    return buckets


class AnalyticsService:
    @contextmanager
    def _reader(self, company_id: int, conn: Any = None) -> Iterator[Any]:
        """Yield a reader on the company's own database.

        Opening a tenant file is not free: it derives the company's key and
        decrypts the header before the first row is read. Every section used to
        open its own, so one load of the reporting screen paid that cost once
        per figure. Passing an already-open connection through lets `report()`
        pay it once for the whole page, while each section stays individually
        callable — and individually testable — exactly as before.
        """
        if conn is not None:
            yield conn
            return

        with database_manager.tenant(int(company_id)) as opened:
            yield opened

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

    def overview(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> dict[str, Any]:
        """Headline totals for the selected period."""
        company_id = int(company_id)
        start, end, safe_days = self._window(days)

        with self._reader(company_id, conn) as conn:
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
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> list[dict[str, Any]]:
        """Daily message counts, split inbound and outbound."""
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
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
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> list[dict[str, Any]]:
        """Which channels the company's customers actually use."""
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
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
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> list[dict[str, Any]]:
        """When customers write, so staffing can match demand."""
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
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
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> dict[str, Any]:
        """Assistant reliability and speed, from the diagnostics trail."""
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
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

    def first_response_times(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> dict[str, Any]:
        """How long a customer waits for the first reply, and how that is spread.

        Measured per conversation as the gap between its first inbound message
        and the first outbound message that follows, which is the number a
        customer actually experiences.
        """
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
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
                    c.channel,
                    c.external_user_id,
                    c.customer_alias,
                    c.official_customer_name,
                    (
                        SELECT MIN(m.created_at) FROM messages m
                        WHERE m.conversation_id = first_in.conversation_id
                          AND m.direction = 'out'
                          AND m.created_at > first_in.asked_at
                    ) AS answered_at
                FROM first_in
                JOIN conversations c ON c.id = first_in.conversation_id
                """,
                (start, end),
            ).fetchall()

        durations: list[float] = []
        waits: list[dict[str, Any]] = []
        unanswered_waits: list[dict[str, Any]] = []

        for row in rows:
            entry = {
                "conversation_id": int(row["conversation_id"]),
                "channel": row["channel"],
                "external_user_id": row["external_user_id"],
                "customer": (
                    row["customer_alias"]
                    or row["official_customer_name"]
                    or row["external_user_id"]
                ),
                "asked_at": row["asked_at"],
            }

            if not row["answered_at"]:
                unanswered_waits.append({**entry, "waited_seconds": None})
                continue

            try:
                asked = datetime.fromisoformat(row["asked_at"])
                answered = datetime.fromisoformat(row["answered_at"])
            except (TypeError, ValueError):
                continue

            seconds = (answered - asked).total_seconds()
            durations.append(seconds)
            waits.append({**entry, "waited_seconds": round(seconds, 1)})

        # The slowest waits, named. An average of four minutes and a customer
        # who waited nine hours produce the same average; only this list shows
        # the second one, and it is the one the owner has to answer for.
        slowest = sorted(
            waits, key=lambda item: item["waited_seconds"], reverse=True
        )[:SLOWEST_SAMPLE]

        if not durations:
            return {
                "answered": 0,
                "unanswered": len(unanswered_waits),
                "average_seconds": None,
                "median_seconds": None,
                "percentiles": {label: None for label, _ in PERCENTILES},
                "buckets": empty_wait_buckets(),
                "slowest": [],
                "never_answered": unanswered_waits[:SLOWEST_SAMPLE],
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
            "unanswered": len(unanswered_waits),
            "average_seconds": round(sum(durations) / len(durations), 1),
            "median_seconds": round(median, 1),
            # An average is one number standing in for a distribution, and it
            # is the number a bad week hides behind. The percentiles and the
            # buckets are what show the tail.
            "percentiles": {
                label: round(percentile(durations, share), 1)
                for label, share in PERCENTILES
            },
            "buckets": bucket_waits(durations),
            "slowest": slowest,
            "never_answered": unanswered_waits[:SLOWEST_SAMPLE],
        }

    def by_department(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> list[dict[str, Any]]:
        """Traffic per section of the business.

        A conversation carries its section as `conversations.department`, the
        code `conversation_control_service` writes beside `department_id`. The
        readable names live in `business_departments` — in this same file, so
        this one join is legitimate, unlike a join onto `users`, which lives in
        the control plane and is a different database entirely.
        """
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(c.department), ''), ?) AS code,
                    COUNT(*) AS messages,
                    COUNT(DISTINCT m.conversation_id) AS conversations,
                    COALESCE(SUM(CASE WHEN m.direction = 'in' THEN 1 ELSE 0 END), 0) AS inbound,
                    COALESCE(SUM(CASE WHEN m.sender_type = 'ai' THEN 1 ELSE 0 END), 0) AS by_assistant,
                    COALESCE(SUM(CASE WHEN m.sender_type = 'employee' THEN 1 ELSE 0 END), 0) AS by_employee
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.created_at >= ? AND m.created_at <= ?
                GROUP BY code
                ORDER BY messages DESC
                """,
                (UNASSIGNED_DEPARTMENT, start, end),
            ).fetchall()

            # Current state, not range state: "waiting right now" is the only
            # useful reading of an open queue, and the screen labels it that
            # way rather than mixing it into the period's totals.
            waiting = {
                str(row["code"]): int(row["waiting"])
                for row in conn.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(TRIM(department), ''), ?) AS code,
                        COUNT(*) AS waiting
                    FROM conversations
                    WHERE needs_human = 1
                    GROUP BY code
                    """,
                    (UNASSIGNED_DEPARTMENT,),
                ).fetchall()
            }

            named = {
                str(row["code"]): row
                for row in conn.execute(
                    "SELECT code, name_en, name_ar FROM business_departments"
                ).fetchall()
            }

        breakdown: list[dict[str, Any]] = []

        for row in rows:
            code = str(row["code"])
            label = named.get(code)
            by_assistant = int(row["by_assistant"])
            outbound = by_assistant + int(row["by_employee"])

            # The unsorted pile is not a missing definition, it is the expected
            # state of a conversation nobody has routed yet. Flagging it as
            # undefined would put a warning on the one row that is behaving
            # normally.
            unassigned = code == UNASSIGNED_DEPARTMENT

            breakdown.append(
                {
                    "code": code,
                    # A section the company has since renamed or never defined
                    # still has traffic under its code; showing the code beats
                    # showing nothing.
                    "name": (
                        (label["name_en"] or label["name_ar"] or code)
                        if label
                        else code
                    ),
                    "name_ar": label["name_ar"] if label else None,
                    "defined": label is not None or unassigned,
                    "messages": int(row["messages"]),
                    "conversations": int(row["conversations"]),
                    "inbound": int(row["inbound"]),
                    "by_assistant": by_assistant,
                    "by_employee": int(row["by_employee"]),
                    "automation_rate": (
                        round(by_assistant / outbound, 4) if outbound else 0.0
                    ),
                    "waiting_for_human": waiting.get(code, 0),
                }
            )

        return breakdown

    def channel_trend(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> dict[str, Any]:
        """Daily message counts per channel.

        `by_channel` gives the period's totals, which cannot show a channel
        that went quiet three weeks ago — its total still looks healthy.
        Pivoted here rather than in the browser, so the chart draws what the
        server counted.
        """
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
            rows = conn.execute(
                """
                SELECT
                    substr(created_at, 1, 10) AS day,
                    channel,
                    COUNT(*) AS messages
                FROM messages
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY day, channel
                ORDER BY day
                """,
                (start, end),
            ).fetchall()

        channels: list[str] = []
        by_day: dict[str, dict[str, int]] = {}

        for row in rows:
            channel = str(row["channel"])
            day = str(row["day"])

            if channel not in channels:
                channels.append(channel)

            by_day.setdefault(day, {})[channel] = int(row["messages"])

        # Every channel appears on every day, as zero where it was silent. A
        # missing key makes a stacked chart drop the band instead of drawing a
        # gap, which reads as "no data" rather than "nothing happened".
        series = [
            dict(
                {channel: by_day[day].get(channel, 0) for channel in channels},
                day=day,
            )
            for day in sorted(by_day)
        ]

        return {"channels": channels, "days": series}

    def employee_performance(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS, conn: Any = None
    ) -> list[dict[str, Any]]:
        """Per-employee workload and speed.

        Returns user ids only. Names live in the control database and are
        resolved by the caller in one batched query, because a tenant database
        cannot join across to `users`.

        Response time is measured only on a reply that answered a waiting
        customer — one whose immediately preceding message in the thread came
        from the customer. Timing every employee message would count the second
        and third line of a burst as two near-instant replies and quietly halve
        the reported wait.
        """
        start, end, _ = self._window(days)

        with self._reader(company_id, conn) as conn:
            volume = conn.execute(
                """
                SELECT
                    sender_user_id AS user_id,
                    COUNT(*) AS replies,
                    COUNT(DISTINCT conversation_id) AS conversations,
                    MIN(created_at) AS first_reply_at,
                    MAX(created_at) AS last_reply_at
                FROM messages
                WHERE sender_type = 'employee'
                  AND sender_user_id IS NOT NULL
                  AND created_at >= ? AND created_at <= ?
                GROUP BY sender_user_id
                """,
                (start, end),
            ).fetchall()

            # `julianday` is what keeps this an aggregate: the gaps are
            # averaged inside SQLite rather than by pulling one row per reply
            # into Python. A timestamp it cannot parse yields NULL, and NULL is
            # skipped rather than counted as a zero-second reply.
            speed = conn.execute(
                """
                WITH answered AS (
                    SELECT
                        m.sender_user_id AS user_id,
                        (julianday(m.created_at) - julianday(
                            (SELECT p.created_at FROM messages p
                              WHERE p.conversation_id = m.conversation_id
                                AND p.created_at < m.created_at
                              ORDER BY p.created_at DESC, p.id DESC LIMIT 1)
                        )) * 86400.0 AS waited
                    FROM messages m
                    WHERE m.sender_type = 'employee'
                      AND m.sender_user_id IS NOT NULL
                      AND m.created_at >= ? AND m.created_at <= ?
                      AND (SELECT p.direction FROM messages p
                            WHERE p.conversation_id = m.conversation_id
                              AND p.created_at < m.created_at
                            ORDER BY p.created_at DESC, p.id DESC LIMIT 1) = 'in'
                )
                SELECT
                    user_id,
                    COUNT(waited) AS answered,
                    AVG(waited) AS average_seconds,
                    MAX(waited) AS slowest_seconds
                FROM answered
                WHERE waited IS NOT NULL
                GROUP BY user_id
                """,
                (start, end),
            ).fetchall()

            takeovers = conn.execute(
                """
                SELECT actor_user_id AS user_id, COUNT(*) AS takeovers
                FROM conversation_events
                WHERE event_type IN (?, ?)
                  AND actor_user_id IS NOT NULL
                  AND created_at >= ? AND created_at <= ?
                GROUP BY actor_user_id
                """,
                (EVENT_HUMAN_TAKEOVER, EVENT_ASSIGNMENT_CHANGED, start, end),
            ).fetchall()

        combined: dict[int, dict[str, Any]] = {}

        def entry_for(user_id: int) -> dict[str, Any]:
            return combined.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "replies": 0,
                    "conversations": 0,
                    "takeovers": 0,
                    "answered": 0,
                    "average_response_seconds": None,
                    "slowest_response_seconds": None,
                    "first_reply_at": None,
                    "last_reply_at": None,
                },
            )

        for row in volume:
            entry = entry_for(int(row["user_id"]))
            entry["replies"] = int(row["replies"])
            entry["conversations"] = int(row["conversations"])
            entry["first_reply_at"] = row["first_reply_at"]
            entry["last_reply_at"] = row["last_reply_at"]

        for row in speed:
            entry = entry_for(int(row["user_id"]))
            entry["answered"] = int(row["answered"])
            entry["average_response_seconds"] = (
                round(float(row["average_seconds"]), 1)
                if row["average_seconds"] is not None
                else None
            )
            entry["slowest_response_seconds"] = (
                round(float(row["slowest_seconds"]), 1)
                if row["slowest_seconds"] is not None
                else None
            )

        for row in takeovers:
            entry_for(int(row["user_id"]))["takeovers"] = int(row["takeovers"])

        return sorted(
            combined.values(), key=lambda item: item["replies"], reverse=True
        )

    def report(
        self, *, company_id: int, days: int = DEFAULT_RANGE_DAYS
    ) -> dict[str, Any]:
        """Every section of the reporting screen, over one open connection.

        Opening the company's encrypted file costs a key derivation and a
        decrypted header. Nine sections opening nine connections paid that nine
        times for one screen; this pays it once.
        """
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            return {
                "overview": self.overview(
                    company_id=company_id, days=days, conn=conn
                ),
                "volume_by_day": self.volume_by_day(
                    company_id=company_id, days=days, conn=conn
                ),
                "by_channel": self.by_channel(
                    company_id=company_id, days=days, conn=conn
                ),
                "channel_trend": self.channel_trend(
                    company_id=company_id, days=days, conn=conn
                ),
                "by_department": self.by_department(
                    company_id=company_id, days=days, conn=conn
                ),
                "hourly_distribution": self.hourly_distribution(
                    company_id=company_id, days=days, conn=conn
                ),
                "assistant": self.assistant_health(
                    company_id=company_id, days=days, conn=conn
                ),
                "employees": self.employee_performance(
                    company_id=company_id, days=days, conn=conn
                ),
                "first_response": self.first_response_times(
                    company_id=company_id, days=days, conn=conn
                ),
            }

analytics_service = AnalyticsService()
