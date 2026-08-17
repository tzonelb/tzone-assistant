"""Dashboard summary.

The counters read from both databases: company, users and subscription from the
control plane, and everything the company actually owns from its own encrypted
file. Several tiles previously counted tables nothing ever wrote to, so they
always read zero — those now count the real records.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.services.auth_service import auth_service, require_permission
from backend.services.plan_service import plan_service
from database.manager import DatabaseError, database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _active_subscription(conn, company_id: int) -> dict[str, Any] | None:
    """The plan and its allowances — deliberately without the price.

    `plans.price_monthly` used to be selected here and reached anyone holding
    `dashboard.view`, which is the permission almost every employee has. What
    the company pays is commercial information about the business, not
    operational information about the inbox; it belongs on the subscription
    screen, behind `subscriptions.view`.

    The allowances stay, because an employee who is about to be refused for
    exceeding one needs to be able to see it coming.

    Resolution moved to `plan_service`. It used to be answered here and again
    in `platform_service`, and the two disagreed: this one treated a blank
    expiry as expired — while the console's own form says "Leave the date empty
    for a plan that does not expire" — and the other ignored `expires_at`
    entirely, so a subscription that ran out last year still named the plan.
    """
    subscription = plan_service.subscription(company_id, conn=conn)

    if not subscription:
        return None

    # The price is not selected by `plan_service` either, but strip anything
    # commercial defensively: this response is the widest-read one on the
    # platform and a column added to `plans` must not reach it by accident.
    return {
        key: value
        for key, value in subscription.items()
        if key not in ("price_monthly",)
    }


def _subscription_is_active(subscription: dict[str, Any] | None) -> bool:
    return plan_service.is_active(subscription)


@router.get("/summary")
def dashboard_summary(
    company_id: int | None = Query(default=None, ge=1),
    current_user: dict = Depends(require_permission("dashboard.view")),
):
    resolved_company_id = auth_service.resolve_company_id(
        current_user=current_user,
        requested_company_id=company_id,
    )

    counts: dict[str, int] = {}

    with database_manager.control() as conn:
        # Named columns rather than SELECT *: a column added to `companies`
        # later should not reach the browser because this query was lazy.
        company = conn.execute(
            """
            SELECT id, name, slug, country, currency, timezone,
                   default_language, status, created_at
            FROM companies
            WHERE id = ?
            LIMIT 1
            """,
            (resolved_company_id,),
        ).fetchone()

        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found.",
            )

        subscription = _active_subscription(conn, resolved_company_id)

        counts["users"] = int(
            conn.execute(
                """
                SELECT COUNT(*) AS total FROM company_users
                WHERE company_id = ? AND status = 'active'
                """,
                (resolved_company_id,),
            ).fetchone()["total"]
        )

        counts["channel_accounts"] = int(
            conn.execute(
                """
                SELECT COUNT(*) AS total FROM channel_accounts
                WHERE company_id = ? AND status = 'active'
                """,
                (resolved_company_id,),
            ).fetchone()["total"]
        )

        # No `page_id`, `phone_number_id` or `external_account_id` here. Those
        # are the provider identifiers the webhook layer routes on, and this
        # endpoint is guarded by `dashboard.view` — the permission nearly every
        # employee holds. They are served by GET /api/dashboard/channels
        # instead, which requires `channels.view`; the tile below only needs to
        # say which channels are connected and whether they are healthy.
        channel_rows = conn.execute(
            """
            SELECT
                channel_accounts.id,
                channel_accounts.channel,
                channel_accounts.name,
                channel_accounts.status,
                channel_accounts.ai_enabled,
                channel_accounts.flow_enabled,
                channel_accounts.voice_ai_enabled,
                channel_accounts.image_ai_enabled,
                branches.name AS branch_name
            FROM channel_accounts
            LEFT JOIN branches ON branches.id = channel_accounts.branch_id
            WHERE channel_accounts.company_id = ?
            ORDER BY channel_accounts.id ASC
            """,
            (resolved_company_id,),
        ).fetchall()

    try:
        with database_manager.tenant(resolved_company_id) as conn:
            for key, query in {
                "conversations": "SELECT COUNT(*) AS total FROM conversations",
                "open_conversations": (
                    "SELECT COUNT(*) AS total FROM conversations WHERE status != 'closed'"
                ),
                "unread_conversations": (
                    "SELECT COUNT(*) AS total FROM conversations WHERE unread_count > 0"
                ),
                "human_handled": (
                    "SELECT COUNT(*) AS total FROM conversations WHERE handled_by_ai = 0"
                ),
                "messages": "SELECT COUNT(*) AS total FROM messages",
                "customers": "SELECT COUNT(*) AS total FROM customers",
                "knowledge_items": (
                    "SELECT COUNT(*) AS total FROM knowledge_items WHERE status = 'active'"
                ),
                "tickets": "SELECT COUNT(*) AS total FROM tickets",
                "open_tickets": (
                    "SELECT COUNT(*) AS total FROM tickets WHERE status = 'open'"
                ),
                "pending_replies": "SELECT COUNT(*) AS total FROM pending_replies",
            }.items():
                counts[key] = int(conn.execute(query).fetchone()["total"])

            recent_conversations = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        id, channel, external_user_id, language, department,
                        topic, status, needs_human, last_message_at, created_at
                    FROM conversations
                    ORDER BY COALESCE(last_message_at, created_at) DESC
                    LIMIT 10
                    """
                ).fetchall()
            ]
    except DatabaseError:
        logger.exception(
            "Could not open the database for company %s", resolved_company_id
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This company's data is temporarily unavailable.",
        ) from None

    return {
        "company": dict(company),
        "subscription": subscription,
        "subscription_active": _subscription_is_active(subscription),
        "counts": counts,
        "channels": [dict(row) for row in channel_rows],
        "recent_conversations": recent_conversations,
    }


@router.get("/company")
def get_company(
    company_id: int | None = Query(default=None, ge=1),
    current_user: dict = Depends(require_permission("dashboard.view")),
):
    resolved_company_id = auth_service.resolve_company_id(
        current_user=current_user,
        requested_company_id=company_id,
    )

    with database_manager.control() as conn:
        # Named columns rather than SELECT *: a column added to `companies`
        # later should not reach the browser because this query was lazy.
        company = conn.execute(
            """
            SELECT id, name, slug, country, currency, timezone,
                   default_language, status, created_at
            FROM companies
            WHERE id = ?
            LIMIT 1
            """,
            (resolved_company_id,),
        ).fetchone()

    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    return dict(company)


@router.get("/subscription")
def get_subscription(
    company_id: int | None = Query(default=None, ge=1),
    current_user: dict = Depends(require_permission("subscriptions.view")),
):
    resolved_company_id = auth_service.resolve_company_id(
        current_user=current_user,
        requested_company_id=company_id,
    )

    with database_manager.control() as conn:
        subscription = _active_subscription(conn, resolved_company_id)

    return {
        "subscription": subscription,
        "active": _subscription_is_active(subscription),
    }


@router.get("/channels")
def get_channels(
    company_id: int | None = Query(default=None, ge=1),
    current_user: dict = Depends(require_permission("channels.view")),
):
    resolved_company_id = auth_service.resolve_company_id(
        current_user=current_user,
        requested_company_id=company_id,
    )

    with database_manager.control() as conn:
        rows = conn.execute(
            """
            SELECT
                channel_accounts.id,
                channel_accounts.company_id,
                channel_accounts.branch_id,
                channel_accounts.channel,
                channel_accounts.name,
                channel_accounts.external_account_id,
                channel_accounts.phone_number_id,
                channel_accounts.page_id,
                channel_accounts.instagram_business_id,
                channel_accounts.status,
                channel_accounts.ai_enabled,
                channel_accounts.flow_enabled,
                channel_accounts.voice_ai_enabled,
                channel_accounts.image_ai_enabled,
                channel_accounts.created_at,
                channel_accounts.updated_at,
                branches.name AS branch_name
            FROM channel_accounts
            LEFT JOIN branches ON branches.id = channel_accounts.branch_id
            WHERE channel_accounts.company_id = ?
            ORDER BY channel_accounts.id ASC
            """,
            (resolved_company_id,),
        ).fetchall()

    # Sealed credentials are deliberately not selected: nothing outside the
    # sending code needs them, and they must never reach a browser.
    return {"items": [dict(row) for row in rows], "total": len(rows)}
