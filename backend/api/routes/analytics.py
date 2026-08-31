"""Reporting endpoints.

One request returns the whole report so the screen does not fan out into six
round trips, each re-opening the company's encrypted database.

Which company is reported on comes from the session and nothing else. There is
no `company_id` parameter here, on any of these routes, and there must never be
one: the report reads the company's own encrypted file directly, so a caller
who could name the file would be reading somebody else's business.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.services.analytics_service import (
    DEFAULT_RANGE_DAYS,
    MAX_RANGE_DAYS,
    analytics_service,
)
from backend.services.auth_service import auth_service, require_permission


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


# Cells beginning with one of these are read as a formula by Excel, LibreOffice
# and Google Sheets. Nothing in a report is safe by virtue of being a number:
# a department code, an employee's own name and a customer's channel handle all
# reach these cells verbatim, and a customer who calls themselves
# `=cmd|'/C calc'!A0` turns the owner's export into a live exploit on the
# owner's machine. Prefixing a single quote makes the spreadsheet read the cell
# as text, which is the same neutralisation the conversation export applies.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_cell(value: object) -> object:
    if isinstance(value, str) and value[:1] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


def _export_filename(report: str, days: int) -> str:
    safe_report = str(report).replace("/", "_")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"analytics_{safe_report}_{days}d_{timestamp}.csv"


def _with_names(company_id: int, employees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach employee names to rows that carry only ids.

    Names live in the control database, which is a different file: a tenant
    query cannot join onto `users`, and attempting it is the bug this codebase
    has already shipped once. They are resolved once for the whole report
    rather than per row.
    """
    names = auth_service.user_display_names(
        company_id, [entry["user_id"] for entry in employees]
    )

    for entry in employees:
        entry["name"] = names.get(entry["user_id"], f"User {entry['user_id']}")

    return employees


@router.get("/summary")
def analytics_summary(
    days: int = Query(default=DEFAULT_RANGE_DAYS, ge=1, le=MAX_RANGE_DAYS),
    current_user: dict[str, Any] = Depends(require_permission("analytics.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    report = analytics_service.report(company_id=company_id, days=days)
    _with_names(company_id, report["employees"])

    return report


# ----------------------------------------------------------------------
# CSV export
# ----------------------------------------------------------------------


def _employee_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "employee": entry.get("name"),
            "user_id": entry.get("user_id"),
            "conversations_handled": entry.get("conversations"),
            "replies_sent": entry.get("replies"),
            "takeovers": entry.get("takeovers"),
            "replies_to_a_waiting_customer": entry.get("answered"),
            "average_response_seconds": entry.get("average_response_seconds"),
            "slowest_response_seconds": entry.get("slowest_response_seconds"),
            "first_reply_at": entry.get("first_reply_at"),
            "last_reply_at": entry.get("last_reply_at"),
        }
        for entry in report["employees"]
    ]


def _department_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "department": entry.get("name"),
            "code": entry.get("code"),
            "conversations": entry.get("conversations"),
            "messages": entry.get("messages"),
            "from_customers": entry.get("inbound"),
            "answered_by_assistant": entry.get("by_assistant"),
            "answered_by_employees": entry.get("by_employee"),
            "automation_rate": entry.get("automation_rate"),
            "waiting_for_a_human_now": entry.get("waiting_for_human"),
        }
        for entry in report["by_department"]
    ]


def _channel_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per day per channel — long, not pivoted.

    A spreadsheet pivots this in one step and cannot un-pivot a wide table, so
    the export gives the shape that can become either.
    """
    channels = report["channel_trend"]["channels"]

    return [
        {"day": day["day"], "channel": channel, "messages": day.get(channel, 0)}
        for day in report["channel_trend"]["days"]
        for channel in channels
    ]


def _volume_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "day": row["day"],
            "inbound": row["inbound"],
            "outbound": row["outbound"],
            "total": row["total"],
        }
        for row in report["volume_by_day"]
    ]


def _response_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The wait distribution, then the conversations that made up its tail.

    Two record shapes in one file on purpose: the bands answer "how are we
    doing", and the named rows under them answer "who is it happening to",
    which is the question a band alone cannot.
    """
    first_response = report["first_response"]

    rows: list[dict[str, Any]] = [
        {
            "record_type": "bucket",
            "label": bucket["label"],
            "conversations": bucket["conversations"],
        }
        for bucket in first_response["buckets"]
    ]

    rows.extend(
        {
            "record_type": "percentile",
            "label": label,
            "waited_seconds": value,
        }
        for label, value in (first_response.get("percentiles") or {}).items()
    )

    rows.extend(
        {
            "record_type": "slowest_answered",
            "label": entry.get("customer"),
            "channel": entry.get("channel"),
            "conversation_id": entry.get("conversation_id"),
            "asked_at": entry.get("asked_at"),
            "waited_seconds": entry.get("waited_seconds"),
        }
        for entry in first_response["slowest"]
    )

    rows.extend(
        {
            "record_type": "never_answered",
            "label": entry.get("customer"),
            "channel": entry.get("channel"),
            "conversation_id": entry.get("conversation_id"),
            "asked_at": entry.get("asked_at"),
            "waited_seconds": None,
        }
        for entry in first_response["never_answered"]
    )

    return rows


_REPORTS = {
    "employees": _employee_rows,
    "departments": _department_rows,
    "channels": _channel_rows,
    "volume": _volume_rows,
    "response": _response_rows,
}


@router.get("/export")
def export_report(
    report: str = Query(default="employees"),
    days: int = Query(default=DEFAULT_RANGE_DAYS, ge=1, le=MAX_RANGE_DAYS),
    current_user: dict[str, Any] = Depends(require_permission("analytics.view")),
):
    """One report table as CSV, so the numbers can leave the platform.

    `report` selects a table rather than dumping everything: a single file
    holding five differently-shaped tables is not a spreadsheet anybody can
    use. An unknown name falls back to the employee table instead of failing —
    an export that 422s teaches the owner nothing about which names are valid.
    """
    company_id = auth_service.resolve_company_id(current_user)

    name = str(report or "").strip().lower()
    build = _REPORTS.get(name)

    if build is None:
        name = "employees"
        build = _REPORTS[name]

    # The whole report, then one table out of it. Building only the requested
    # section would be cheaper and would let the file disagree with the screen
    # the owner exported it from — same range, same single connection, same
    # numbers, is worth more than the saved queries.
    built = analytics_service.report(company_id=company_id, days=days)
    _with_names(company_id, built["employees"])

    rows = build(built)

    output = io.StringIO()

    if rows:
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)

        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {key: _csv_cell(value) for key, value in row.items()}
            )

    filename = _export_filename(name, built["overview"]["range_days"])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
