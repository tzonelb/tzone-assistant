from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import db


def _parse_datetime(value: str | None) -> datetime | None:
    """Best-effort parser for the ISO-ish timestamps stored across the
    schema (customer_service.utc_now_iso() produces e.g.
    "2026-07-30T12:00:00.123456+00:00"; some rows may use a trailing "Z"
    or a bare SQLite `datetime('now')` value like "2026-07-30 12:00:00").
    Returns None (rather than raising) for anything unparsable so a
    single bad row never breaks the whole summary."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_tags(raw_tags_json: str | None) -> list[str]:
    try:
        parsed = json.loads(raw_tags_json or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


class AnalyticsService:
    def __init__(self) -> None:
        # No dedicated tables — this service is read-only aggregation
        # over customers/conversations, which are already created by
        # customer_service.ensure_schema() and database.database.db.
        pass

    def ensure_schema(self) -> None:
        """No new tables needed — analytics is a read-only aggregation
        over existing tables (customers, conversations)."""
        pass

    def get_summary(self, company_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            customer_rows = conn.execute(
                "SELECT lifecycle_stage, first_seen_at, tags_json FROM customers WHERE company_id = ?",
                (company_id,),
            ).fetchall()
            conversation_rows = conn.execute(
                "SELECT channel, ai_enabled FROM conversations WHERE company_id = ?",
                (company_id,),
            ).fetchall()

        # --- Contacts ---------------------------------------------------
        lifecycle_counter: Counter[str] = Counter()
        tag_counter: Counter[str] = Counter()
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        new_contacts_last_30_days = 0

        for row in customer_rows:
            stage = row["lifecycle_stage"] or "unknown"
            lifecycle_counter[stage] += 1

            for tag in _parse_tags(row["tags_json"]):
                if tag:
                    tag_counter[str(tag)] += 1

            first_seen = _parse_datetime(row["first_seen_at"])
            if first_seen is not None and first_seen >= cutoff:
                new_contacts_last_30_days += 1

        # --- Conversations ------------------------------------------------
        channel_counter: Counter[str] = Counter()
        ai_enabled_count = 0
        human_count = 0

        for row in conversation_rows:
            channel = row["channel"] or "unknown"
            channel_counter[channel] += 1
            if int(row["ai_enabled"] or 0) == 1:
                ai_enabled_count += 1
            else:
                human_count += 1

        return {
            "total_contacts": len(customer_rows),
            "total_conversations": len(conversation_rows),
            "new_contacts_last_30_days": new_contacts_last_30_days,
            "conversations_by_channel": [
                {"channel": channel, "count": count}
                for channel, count in sorted(channel_counter.items(), key=lambda item: item[1], reverse=True)
            ],
            "ai_vs_human": {
                "ai_enabled": ai_enabled_count,
                "human": human_count,
            },
            "contacts_by_lifecycle_stage": [
                {"stage": stage, "count": count}
                for stage, count in sorted(lifecycle_counter.items(), key=lambda item: item[1], reverse=True)
            ],
            "top_tags": [
                {"tag": tag, "count": count}
                for tag, count in tag_counter.most_common(10)
            ],
        }

    def get_conversation_volume_trend(self, company_id: int, days: int = 30) -> dict[str, Any]:
        """Real daily conversation counts for the last `days` days, bucketed
        by the calendar date (UTC) of conversations.created_at. Days with
        zero conversations are simply absent from `series` — nothing here
        is interpolated or estimated."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        with db.connect() as conn:
            rows = conn.execute(
                "SELECT created_at FROM conversations WHERE company_id = ?",
                (company_id,),
            ).fetchall()

        daily_counts: Counter[str] = Counter()
        for row in rows:
            created = _parse_datetime(row["created_at"])
            if created is None or created < cutoff:
                continue
            daily_counts[created.date().isoformat()] += 1

        return {
            "days": days,
            "series": [
                {"date": day, "count": count}
                for day, count in sorted(daily_counts.items())
            ],
        }

    def get_ai_vs_human_trend(self, company_id: int, days: int = 30) -> dict[str, Any]:
        """Daily breakdown of conversations.ai_enabled / needs_human for the
        last `days` days, bucketed by the calendar date (UTC) of
        conversations.created_at.

        IMPORTANT: ai_enabled and needs_human are snapshot fields captured
        at conversation-creation time (see conversation_control_service.py)
        — they describe how the conversation *started*, not how it was
        ultimately resolved. This is NOT an AI-resolution-rate metric."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        with db.connect() as conn:
            rows = conn.execute(
                "SELECT created_at, ai_enabled, needs_human FROM conversations WHERE company_id = ?",
                (company_id,),
            ).fetchall()

        daily: dict[str, dict[str, int]] = {}
        for row in rows:
            created = _parse_datetime(row["created_at"])
            if created is None or created < cutoff:
                continue
            day = created.date().isoformat()
            bucket = daily.setdefault(day, {"ai_enabled_count": 0, "human_count": 0, "needs_human_count": 0})
            if int(row["ai_enabled"] or 0) == 1:
                bucket["ai_enabled_count"] += 1
            else:
                bucket["human_count"] += 1
            if int(row["needs_human"] or 0) == 1:
                bucket["needs_human_count"] += 1

        return {
            "days": days,
            "note": (
                "ai_enabled/needs_human reflect each conversation's state at "
                "creation time, not a resolved outcome — this is not an "
                "AI-resolution-rate metric."
            ),
            "series": [
                {"date": day, **counts}
                for day, counts in sorted(daily.items())
            ],
        }


analytics_service = AnalyticsService()
