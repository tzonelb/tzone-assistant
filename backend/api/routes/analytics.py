"""Reporting endpoints.

One request returns the whole report so the screen does not fan out into six
round trips, each re-opening the company's encrypted database.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.services.analytics_service import (
    DEFAULT_RANGE_DAYS,
    MAX_RANGE_DAYS,
    analytics_service,
)
from backend.services.auth_service import auth_service, require_permission


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("/summary")
def analytics_summary(
    days: int = Query(default=DEFAULT_RANGE_DAYS, ge=1, le=MAX_RANGE_DAYS),
    current_user: dict[str, Any] = Depends(require_permission("analytics.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    employees = analytics_service.employee_activity(company_id=company_id, days=days)

    # Employee names live in the control database, so they are resolved once for
    # the whole report rather than per row.
    names = auth_service.user_display_names(
        company_id, [entry["user_id"] for entry in employees]
    )

    for entry in employees:
        entry["name"] = names.get(entry["user_id"], f"User {entry['user_id']}")

    return {
        "overview": analytics_service.overview(company_id=company_id, days=days),
        "volume_by_day": analytics_service.volume_by_day(
            company_id=company_id, days=days
        ),
        "by_channel": analytics_service.by_channel(company_id=company_id, days=days),
        "hourly_distribution": analytics_service.hourly_distribution(
            company_id=company_id, days=days
        ),
        "assistant": analytics_service.assistant_health(
            company_id=company_id, days=days
        ),
        "employees": employees,
        "first_response": analytics_service.first_response_times(
            company_id=company_id, days=days
        ),
    }
