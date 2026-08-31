"""Small persistent event stream for Super Admin diagnostics.

Deliberately independent from the customer-facing timeline: it records technical
workflow events (webhook received, buffer started, AI sent, errors) without
polluting the conversation audit trail.

Every event belongs to exactly one company and is written to that company's own
encrypted database. There is no platform-wide event table any more, so an event
that arrives without a company is dropped with a warning rather than guessed
into somebody's data.

Table creation belongs to `database/schema_tenant.py` alone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiagnosticsService:
    RETENTION_DAYS = 14

    def record(
        self,
        *,
        event_type: str,
        company_id: int | None = None,
        channel: str | None = None,
        external_user_id: str | None = None,
        severity: str = "info",
        status: str | None = None,
        duration_ms: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        clean_type = str(event_type or "unknown").strip().lower()
        clean_severity = str(severity or "info").strip().lower()
        if clean_severity not in {"debug", "info", "warning", "error", "critical"}:
            clean_severity = "info"

        if company_id is None:
            # There is no shared database to fall back to and choosing a company
            # would file this event against a tenant it does not belong to.
            logger.warning(
                "Diagnostic event dropped: no company. type=%s severity=%s channel=%s",
                clean_type,
                clean_severity,
                channel,
            )
            return

        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                INSERT INTO diagnostic_events (
                    company_id, channel, external_user_id, event_type,
                    severity, status, duration_ms, data_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    channel,
                    external_user_id,
                    clean_type,
                    clean_severity,
                    status,
                    duration_ms,
                    json.dumps(data or {}, ensure_ascii=False, default=str),
                    utc_now_iso(),
                ),
            )
            conn.commit()

    def list_events(
        self,
        *,
        company_id: int,
        limit: int = 100,
        event_type: str | None = None,
        severity: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        company_id = int(company_id)
        conditions = ["company_id = ?"]
        params: list[Any] = [company_id]
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type.strip().lower())
        if severity:
            conditions.append("severity = ?")
            params.append(severity.strip().lower())
        if channel:
            conditions.append("channel = ?")
            params.append(channel.strip().lower())
        params.append(max(1, min(int(limit), 500)))

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM diagnostic_events
                WHERE {' AND '.join(conditions)}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            try:
                item["data"] = json.loads(item.pop("data_json") or "{}")
            except json.JSONDecodeError:
                item["data"] = {}
                item.pop("data_json", None)
            result.append(item)
        return result

    def summary(self, *, company_id: int) -> dict[str, Any]:
        company_id = int(company_id)
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        with database_manager.tenant(company_id) as conn:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN severity IN ('error', 'critical') THEN 1 ELSE 0 END) AS errors,
                    SUM(CASE WHEN event_type = 'incoming_message' THEN 1 ELSE 0 END) AS incoming,
                    SUM(CASE WHEN event_type = 'outgoing_message' THEN 1 ELSE 0 END) AS outgoing,
                    SUM(CASE WHEN event_type = 'ai_buffer_scheduled' THEN 1 ELSE 0 END) AS buffered,
                    SUM(CASE WHEN event_type = 'ai_reply_sent' THEN 1 ELSE 0 END) AS ai_replies
                FROM diagnostic_events
                WHERE company_id = ?
                  AND created_at >= ?
                """,
                (company_id, since),
            ).fetchone()
            last_event = conn.execute(
                """
                SELECT event_type, severity, status, channel, external_user_id, created_at
                FROM diagnostic_events
                WHERE company_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (company_id,),
            ).fetchone()

        values = dict(counts or {})
        return {
            "period": "24h",
            "incoming_messages": int(values.get("incoming") or 0),
            "outgoing_messages": int(values.get("outgoing") or 0),
            "ai_buffers_started": int(values.get("buffered") or 0),
            "ai_replies_sent": int(values.get("ai_replies") or 0),
            "errors": int(values.get("errors") or 0),
            "total_events": int(values.get("total") or 0),
            "last_event": dict(last_event) if last_event else None,
        }

    def cleanup(self, *, company_id: int, retention_days: int | None = None) -> int:
        """Drop events older than the retention window for one company.

        Retention is per company now: each company's events live in its own
        database file, so there is no single table to sweep.
        """
        company_id = int(company_id)
        days = max(1, min(int(retention_days or self.RETENTION_DAYS), 365))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                "DELETE FROM diagnostic_events WHERE created_at < ?",
                (cutoff,),
            )
            conn.commit()
            deleted = int(cursor.rowcount or 0)

        logger.info(
            "Diagnostics cleanup company id=%s retention_days=%s deleted=%s",
            company_id,
            days,
            deleted,
        )
        return deleted


diagnostics_service = DiagnosticsService()
