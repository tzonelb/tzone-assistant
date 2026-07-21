from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)

from backend.services.auth_service import (
    auth_service,
    get_current_user,
)
from database.database import db


router = APIRouter(
    prefix="/api/dashboard",
    tags=["Dashboard"],
)


def require_dashboard_access(
    current_user: dict,
    company_id: int,
):
    allowed = auth_service.has_permission(
        user_id=current_user["id"],
        company_id=company_id,
        permission_code="dashboard.view",
        is_super_admin=bool(
            current_user.get("is_super_admin")
        ),
    )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have dashboard access.",
        )


@router.get("/summary")
def dashboard_summary(
    company_id: int | None = Query(
        default=None,
        ge=1,
    ),
    current_user: dict = Depends(
        get_current_user
    ),
):
    resolved_company_id = (
        auth_service.resolve_company_id(
            current_user=current_user,
            requested_company_id=company_id,
        )
    )

    require_dashboard_access(
        current_user,
        resolved_company_id,
    )

    with db.connect() as conn:
        company = conn.execute("""
            SELECT *
            FROM companies
            WHERE id = ?
            LIMIT 1
        """, (
            resolved_company_id,
        )).fetchone()

        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found.",
            )

        subscription = db.get_active_subscription(
            resolved_company_id
        )

        counts = {}

        count_queries = {
            "users": """
                SELECT COUNT(*) AS total
                FROM company_users
                WHERE company_id = ?
                  AND status = 'active'
            """,
            "channel_accounts": """
                SELECT COUNT(*) AS total
                FROM channel_accounts
                WHERE company_id = ?
                  AND status = 'active'
            """,
            "knowledge_items": """
                SELECT COUNT(*) AS total
                FROM knowledge_items
                WHERE company_id = ?
                  AND status = 'active'
            """,
            "conversations": """
                SELECT COUNT(*) AS total
                FROM conversations
                WHERE company_id = ?
            """,
            "open_conversations": """
                SELECT COUNT(*) AS total
                FROM conversations
                WHERE company_id = ?
                  AND status = 'open'
            """,
            "messages": """
                SELECT COUNT(*) AS total
                FROM messages
                WHERE company_id = ?
            """,
            "tickets": """
                SELECT COUNT(*) AS total
                FROM tickets
                WHERE company_id = ?
            """,
            "open_tickets": """
                SELECT COUNT(*) AS total
                FROM tickets
                WHERE company_id = ?
                  AND status = 'open'
            """,
            "products": """
                SELECT COUNT(*) AS total
                FROM products
                WHERE company_id = ?
                  AND status = 'active'
            """,
            "connectors": """
                SELECT COUNT(*) AS total
                FROM business_connectors
                WHERE company_id = ?
                  AND status = 'active'
            """,
        }

        for key, query in count_queries.items():
            row = conn.execute(
                query,
                (resolved_company_id,),
            ).fetchone()

            counts[key] = (
                row["total"]
                if row
                else 0
            )

        recent_conversations = conn.execute("""
            SELECT
                id,
                channel,
                external_user_id,
                language,
                department,
                topic,
                status,
                needs_human,
                last_message_at,
                created_at
            FROM conversations
            WHERE company_id = ?
            ORDER BY
                COALESCE(
                    last_message_at,
                    created_at
                ) DESC
            LIMIT 10
        """, (
            resolved_company_id,
        )).fetchall()

        active_channels = conn.execute("""
            SELECT
                id,
                channel,
                name,
                external_account_id,
                phone_number_id,
                page_id,
                status,
                ai_enabled,
                flow_enabled,
                voice_ai_enabled,
                image_ai_enabled
            FROM channel_accounts
            WHERE company_id = ?
            ORDER BY id ASC
        """, (
            resolved_company_id,
        )).fetchall()

    subscription_active = db.is_subscription_active(
        resolved_company_id
    )

    return {
        "company": dict(company),
        "subscription": subscription,
        "subscription_active": subscription_active,
        "counts": counts,
        "channels": [
            dict(row)
            for row in active_channels
        ],
        "recent_conversations": [
            dict(row)
            for row in recent_conversations
        ],
    }


@router.get("/company")
def get_company(
    company_id: int | None = Query(
        default=None,
        ge=1,
    ),
    current_user: dict = Depends(
        get_current_user
    ),
):
    resolved_company_id = (
        auth_service.resolve_company_id(
            current_user=current_user,
            requested_company_id=company_id,
        )
    )

    require_dashboard_access(
        current_user,
        resolved_company_id,
    )

    company = db.get_company(
        resolved_company_id
    )

    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    return company


@router.get("/subscription")
def get_subscription(
    company_id: int | None = Query(
        default=None,
        ge=1,
    ),
    current_user: dict = Depends(
        get_current_user
    ),
):
    resolved_company_id = (
        auth_service.resolve_company_id(
            current_user=current_user,
            requested_company_id=company_id,
        )
    )

    subscription = db.get_active_subscription(
        resolved_company_id
    )

    return {
        "subscription": subscription,
        "active": db.is_subscription_active(
            resolved_company_id
        ),
    }


@router.get("/channels")
def get_channels(
    company_id: int | None = Query(
        default=None,
        ge=1,
    ),
    current_user: dict = Depends(
        get_current_user
    ),
):
    resolved_company_id = (
        auth_service.resolve_company_id(
            current_user=current_user,
            requested_company_id=company_id,
        )
    )

    require_dashboard_access(
        current_user,
        resolved_company_id,
    )

    with db.connect() as conn:
        rows = conn.execute("""
            SELECT
                channel_accounts.*,
                branches.name AS branch_name
            FROM channel_accounts
            LEFT JOIN branches
                ON branches.id =
                   channel_accounts.branch_id
            WHERE channel_accounts.company_id = ?
            ORDER BY channel_accounts.id ASC
        """, (
            resolved_company_id,
        )).fetchall()

    return {
        "items": [
            dict(row)
            for row in rows
        ],
        "total": len(rows),
    }