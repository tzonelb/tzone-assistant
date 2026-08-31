"""What a company can see and ask for about its own plan.

The operator's side of the same subject lives in `platform_service` and is
reached from the console. This is the company's side: its own current plan, the
catalogue it may move to, and the requests it has submitted. Every method takes
a `company_id` the caller has already resolved from the session — nothing here
accepts one from a request.

Payment is not wired up. A plan change is therefore a *request*: a row the
operator reviews and applies from the console, never a change this module makes
on its own. That is why `request_change` writes to `subscription_requests` and
touches neither `subscriptions` nor `plans` — a company that could move itself
onto a larger plan would be granting itself the allowances that go with it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager
from backend.services.plan_service import plan_service


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# What a company may see about a plan it might move to. `id` and `code` are
# needed to request one; the rest is what the comparison cards draw.
#
# Listed rather than `SELECT *` on purpose: a commercial column added to `plans`
# later — a cost, a margin, an internal note — must not reach a company's screen
# because somebody added it to a table.
_PLAN_FIELDS: tuple[str, ...] = (
    "id",
    "code",
    "name",
    "price_monthly",
    "max_users",
    "max_channel_accounts",
    "max_ai_messages",
    "max_knowledge_items",
    "voice_ai_enabled",
    "image_ai_enabled",
    "accounting_connector_enabled",
    "product_connector_enabled",
)

_BOOLEAN_PLAN_FIELDS = frozenset(
    {
        "voice_ai_enabled",
        "image_ai_enabled",
        "accounting_connector_enabled",
        "product_connector_enabled",
    }
)

# A request that has been submitted and not yet reviewed. One per company per
# plan: asking twice for the same plan is one request, not two, or a company
# clicking a button repeatedly builds a queue the operator has to read through.
PENDING = "pending"


class BillingService:
    @staticmethod
    def _plan_row(row: Any) -> dict[str, Any]:
        plan = {key: row[key] for key in _PLAN_FIELDS}

        for key in _BOOLEAN_PLAN_FIELDS:
            plan[key] = bool(plan[key])

        return plan

    def plans(self) -> list[dict[str, Any]]:
        """Every plan a company may ask to move onto."""
        with database_manager.control() as conn:
            rows = conn.execute(
                "SELECT * FROM plans ORDER BY price_monthly ASC, id ASC"
            ).fetchall()

        return [self._plan_row(row) for row in rows]

    def _counts(self, company_id: int) -> dict[str, int]:
        """What this company currently occupies of the two visible allowances.

        Counted the way the guards count it — active rows only — so the number
        the billing screen shows is the number that will refuse the next
        invitation or the next channel connection.
        """
        with database_manager.control() as conn:
            users = conn.execute(
                "SELECT COUNT(*) AS total FROM company_users "
                "WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchone()["total"]
            channels = conn.execute(
                "SELECT COUNT(*) AS total FROM channel_accounts "
                "WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchone()["total"]

        return {"users": int(users), "channels": int(channels)}

    def subscription(self, company_id: int) -> dict[str, Any]:
        """This company's plan, its allowances and what it has used of them.

        The price is here, unlike on `/api/dashboard/subscription`, and that is
        the whole reason this endpoint exists separately: what the company pays
        is commercial information about the business, so it sits behind
        `subscriptions.view` on the billing screen rather than behind the
        `dashboard.view` almost every employee holds.

        The most recent subscription is reported even when it has expired.
        `has_subscription` says there is a row and `subscription_active` says
        whether it still entitles — a lapsed company needs to see the plan it
        lapsed from in order to renew it, and reporting nothing would leave the
        renew button with no plan to name.
        """
        company_id = int(company_id)
        record = plan_service.subscription(company_id)
        limits = plan_service.limits(company_id)
        counts = self._counts(company_id)

        if not record:
            return {
                "has_subscription": False,
                "subscription_active": False,
                "plan_id": None,
                "plan_code": None,
                "plan_name": None,
                "subscription_status": "none",
                "expires_at": None,
                "price_monthly": 0,
                "users": {"used": counts["users"], "max": limits["max_users"]},
                "channels": {
                    "used": counts["channels"],
                    "max": limits["max_channel_accounts"],
                },
                "max_ai_messages": limits["max_ai_messages"],
                "features": plan_service.features(company_id),
            }

        with database_manager.control() as conn:
            price = conn.execute(
                "SELECT price_monthly FROM plans WHERE id = ? LIMIT 1",
                (int(record["plan_id"]),),
            ).fetchone()

        return {
            "has_subscription": True,
            "subscription_active": plan_service.is_active(record),
            "plan_id": int(record["plan_id"]),
            "plan_code": record["plan_code"],
            "plan_name": record["plan_name"],
            "subscription_status": record["status"],
            "expires_at": record["expires_at"],
            "price_monthly": price["price_monthly"] if price else 0,
            "users": {"used": counts["users"], "max": limits["max_users"]},
            "channels": {
                "used": counts["channels"],
                "max": limits["max_channel_accounts"],
            },
            "max_ai_messages": limits["max_ai_messages"],
            "features": plan_service.features(company_id),
        }

    def modules(self, company_id: int) -> dict[str, bool]:
        """Which modules the operator has switched on for this company.

        Read-only here. The switch is the platform administrator's and is
        enforced by `require_module`; showing it is so an owner can tell a
        module they do not have from a permission their role is missing, which
        look identical from a screen that simply is not there.
        """
        from backend.services.platform_service import platform_service

        return dict(platform_service.get_platform_config(int(company_id))["modules"])

    def requests(self, company_id: int) -> list[dict[str, Any]]:
        """The plan-change and renewal requests this company has submitted.

        This is the billing history the screen draws. There are no invoices to
        list — nothing is charged online — and inventing a fake one would have
        been worse than showing the real log, which at least says what was asked
        for and what happened to it.
        """
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT
                    subscription_requests.id,
                    subscription_requests.plan_id,
                    subscription_requests.note,
                    subscription_requests.status,
                    subscription_requests.created_at,
                    plans.name AS plan_name,
                    plans.code AS plan_code
                FROM subscription_requests
                JOIN plans ON plans.id = subscription_requests.plan_id
                WHERE subscription_requests.company_id = ?
                ORDER BY subscription_requests.id DESC
                """,
                (int(company_id),),
            ).fetchall()

        return [dict(row) for row in rows]

    def request_change(
        self,
        *,
        company_id: int,
        plan_id: int,
        note: str | None,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        """Ask the operator to move this company onto a plan, or renew it.

        Nothing about the subscription changes here, deliberately — see the
        module docstring. A second request for a plan already pending returns
        the pending one rather than adding a row, so an owner clicking twice
        does not leave the operator two identical things to review.
        """
        company_id, plan_id = int(company_id), int(plan_id)
        note = (note or "").strip()[:500] or None
        now = utc_now_iso()

        with database_manager.control() as conn:
            plan = conn.execute(
                "SELECT id, name FROM plans WHERE id = ? LIMIT 1", (plan_id,)
            ).fetchone()

            if not plan:
                raise ValueError("That plan does not exist.")

            existing = conn.execute(
                """
                SELECT id FROM subscription_requests
                WHERE company_id = ? AND plan_id = ? AND status = ?
                LIMIT 1
                """,
                (company_id, plan_id, PENDING),
            ).fetchone()

            if existing:
                request_id = int(existing["id"])
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO subscription_requests (
                        company_id, plan_id, requested_by_user_id, note,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        plan_id,
                        actor_user_id,
                        note,
                        PENDING,
                        now,
                        now,
                    ),
                )
                request_id = int(cursor.lastrowid)
                conn.commit()

        return {
            "id": request_id,
            "plan_id": plan_id,
            "plan_name": plan["name"],
            "status": PENDING,
            "note": note,
            "created_at": now,
        }


billing_service = BillingService()
