"""Analytics API.

Aggregates the caller's own company data into chart-ready series over a
requested date range (default: the last 30 days). Every query is scoped to
the caller's resolved company_id for multi-tenant isolation, and access is
gated by the "dashboard.view" permission (the same permission that already
guards the company dashboard these numbers summarize).

Metrics are computed only from tables the platform actually writes:
  - conversations              (backend/services/conversation_control_service.py)
  - customers                  (backend/services/customer_service.py)
  - tickets                    (database.database.create_ticket)
  - users / company_users      (employee names for the activity breakdown)

The DB `messages` table is intentionally NOT used: message history is
persisted to the file-based conversation store, so the table is empty in
practice and any "message count" metric would be fabricated.
"""
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.services.auth_service import auth_service, get_current_user
from database.database import db


router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30


def _company_id(current_user: dict[str, Any], requested_company_id: int | None) -> int:
    return auth_service.resolve_company_id(
        current_user=current_user,
        requested_company_id=requested_company_id,
    )


def _require_analytics_access(current_user: dict[str, Any], company_id: int) -> None:
    if auth_service.has_permission(
        user_id=current_user["id"],
        company_id=company_id,
        permission_code="dashboard.view",
        is_super_admin=bool(current_user.get("is_super_admin")),
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to view analytics.",
    )


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date '{value}'. Use YYYY-MM-DD.",
        ) from exc


def _resolve_range(from_value: str | None, to_value: str | None) -> tuple[date, date]:
    today = datetime.utcnow().date()
    to_date = _parse_date(to_value, today)
    from_date = _parse_date(from_value, to_date - timedelta(days=DEFAULT_RANGE_DAYS - 1))

    if from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'from' date must be on or before 'to' date.",
        )

    if (to_date - from_date).days > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range cannot exceed {MAX_RANGE_DAYS} days.",
        )

    return from_date, to_date


def _range_bounds(from_date: date, to_date: date) -> tuple[str, str]:
    """Inclusive lower bound, exclusive upper bound as ISO strings.

    conversations/customers/tickets store created_at as 'YYYY-MM-DD HH:MM:SS'
    (UTC), which sorts lexicographically the same as chronologically, so plain
    string comparison against date-only bounds is correct.
    """
    lower = from_date.isoformat()
    upper = (to_date + timedelta(days=1)).isoformat()
    return lower, upper


def _empty_day_series(from_date: date, to_date: date) -> dict[str, int]:
    series: dict[str, int] = {}
    cursor = from_date
    while cursor <= to_date:
        series[cursor.isoformat()] = 0
        cursor += timedelta(days=1)
    return series


def _daily_series(conn, table: str, company_id: int, lower: str, upper: str,
                  from_date: date, to_date: date) -> list[dict[str, Any]]:
    # `table` is a fixed internal literal (never user input); company_id and the
    # date bounds are always bound parameters for tenant isolation and safety.
    rows = conn.execute(
        f"""
        SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS total
        FROM {table}
        WHERE company_id = ?
          AND created_at >= ?
          AND created_at < ?
        GROUP BY day
        """,
        (company_id, lower, upper),
    ).fetchall()

    series = _empty_day_series(from_date, to_date)
    for row in rows:
        day = row["day"]
        if day in series:
            series[day] = row["total"]

    return [{"date": day, "value": value} for day, value in sorted(series.items())]


def _grouped_counts(conn, sql: str, company_id: int, lower: str, upper: str,
                    label_key: str = "label") -> list[dict[str, Any]]:
    rows = conn.execute(sql, (company_id, lower, upper)).fetchall()
    return [{"label": row[label_key], "value": row["total"]} for row in rows]


@router.get("")
def get_analytics(
    company_id: int | None = Query(default=None, ge=1),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    resolved_company_id = _company_id(current_user, company_id)
    _require_analytics_access(current_user, resolved_company_id)

    from_date, to_date = _resolve_range(from_, to)
    lower, upper = _range_bounds(from_date, to_date)

    with db.connect() as conn:
        params = (resolved_company_id, lower, upper)

        # --- KPI totals (all scoped + range-bounded) ---
        totals: dict[str, int] = {}

        totals["conversations"] = conn.execute(
            """
            SELECT COUNT(*) AS total FROM conversations
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            """,
            params,
        ).fetchone()["total"]

        totals["open_conversations"] = conn.execute(
            """
            SELECT COUNT(*) AS total FROM conversations
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
              AND status = 'open'
            """,
            params,
        ).fetchone()["total"]

        totals["ai_handled_conversations"] = conn.execute(
            """
            SELECT COUNT(*) AS total FROM conversations
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
              AND handled_by_ai = 1
            """,
            params,
        ).fetchone()["total"]

        totals["human_handled_conversations"] = conn.execute(
            """
            SELECT COUNT(*) AS total FROM conversations
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
              AND handled_by_ai = 0
            """,
            params,
        ).fetchone()["total"]

        totals["new_customers"] = conn.execute(
            """
            SELECT COUNT(*) AS total FROM customers
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            """,
            params,
        ).fetchone()["total"]

        totals["tickets"] = conn.execute(
            """
            SELECT COUNT(*) AS total FROM tickets
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            """,
            params,
        ).fetchone()["total"]

        totals["open_tickets"] = conn.execute(
            """
            SELECT COUNT(*) AS total FROM tickets
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
              AND status = 'open'
            """,
            params,
        ).fetchone()["total"]

        # --- Time series ---
        conversations_over_time = _daily_series(
            conn, "conversations", resolved_company_id, lower, upper, from_date, to_date
        )
        customers_over_time = _daily_series(
            conn, "customers", resolved_company_id, lower, upper, from_date, to_date
        )

        # --- Categorical breakdowns ---
        conversations_by_channel = _grouped_counts(
            conn,
            """
            SELECT COALESCE(NULLIF(TRIM(channel), ''), 'unknown') AS label,
                   COUNT(*) AS total
            FROM conversations
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY label
            ORDER BY total DESC
            """,
            resolved_company_id, lower, upper,
        )

        conversations_by_status = _grouped_counts(
            conn,
            """
            SELECT COALESCE(NULLIF(TRIM(status), ''), 'unknown') AS label,
                   COUNT(*) AS total
            FROM conversations
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY label
            ORDER BY total DESC
            """,
            resolved_company_id, lower, upper,
        )

        conversations_by_department = _grouped_counts(
            conn,
            """
            SELECT COALESCE(NULLIF(TRIM(department), ''), 'Unassigned') AS label,
                   COUNT(*) AS total
            FROM conversations
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY label
            ORDER BY total DESC
            """,
            resolved_company_id, lower, upper,
        )

        tickets_by_status = _grouped_counts(
            conn,
            """
            SELECT COALESCE(NULLIF(TRIM(status), ''), 'unknown') AS label,
                   COUNT(*) AS total
            FROM tickets
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY label
            ORDER BY total DESC
            """,
            resolved_company_id, lower, upper,
        )

        # --- AI vs human split (chart-friendly two-slice series) ---
        ai_vs_human = [
            {"label": "AI-handled", "value": totals["ai_handled_conversations"]},
            {"label": "Human-handled", "value": totals["human_handled_conversations"]},
        ]

        # --- Employee activity: human-handled conversations per assigned user ---
        employee_rows = conn.execute(
            """
            SELECT
                conversations.assigned_user_id AS user_id,
                COALESCE(
                    NULLIF(TRIM(users.full_name), ''),
                    users.email,
                    'User #' || conversations.assigned_user_id
                ) AS label,
                COUNT(*) AS total
            FROM conversations
            LEFT JOIN users ON users.id = conversations.assigned_user_id
            WHERE conversations.company_id = ?
              AND conversations.created_at >= ?
              AND conversations.created_at < ?
              AND conversations.assigned_user_id IS NOT NULL
            GROUP BY conversations.assigned_user_id, label
            ORDER BY total DESC
            """,
            params,
        ).fetchall()

        employee_activity = [
            {"label": row["label"], "value": row["total"]} for row in employee_rows
        ]

    return {
        "range": {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
        "totals": totals,
        "conversations_over_time": conversations_over_time,
        "customers_over_time": customers_over_time,
        "conversations_by_channel": conversations_by_channel,
        "conversations_by_status": conversations_by_status,
        "conversations_by_department": conversations_by_department,
        "tickets_by_status": tickets_by_status,
        "ai_vs_human": ai_vs_human,
        "employee_activity": employee_activity,
    }
