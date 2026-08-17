"""What a company is allowed, and how much of it is left.

The allowances existed and were enforced nowhere. `plans.max_users`,
`max_channel_accounts`, `max_knowledge_items` and `max_ai_messages` were read in
exactly one place — the dashboard, to draw a number on a card. Nothing refused a
sixth user on a five-user plan. `voice_ai_enabled`, `image_ai_enabled` and the
two connector flags were read nowhere at all.

Two things had to be fixed before enforcement could mean anything, because both
made "which plan is this company on" answer differently depending on who asked:

* **A blank expiry meant expired.** `_subscription_is_active` returned False
  when `expires_at` was empty — while the console's own form says "Leave the
  date empty for a plan that does not expire". Every company deliberately set
  up not to expire read as having no active subscription.
* **An expired subscription was still the plan.** `_plan_by_company` filtered on
  `status = 'active'` and never looked at `expires_at`, so a subscription that
  ran out last year went on naming the company's plan in the console.

The two resolutions disagreed with each other, which is what happens whenever
the same question is answered in two places. There is now one answer here, and
both callers use it.

### Zero means unlimited

`plans` defaults every allowance to 0. Read as "none allowed", a plan created
without explicit numbers would forbid its customer from adding a single user —
a plan nobody could use, produced by leaving a field blank. Zero is therefore
unlimited, and a plan that genuinely wants to forbid something does it with the
feature flags, which say what they mean.

### Ceilings versus allowances

Nothing here protects the process. These are commercial numbers an operator
sets and a customer buys, and they sit *inside* the platform's hard ceilings
(body size, events per request, queue depth), which no plan can raise. Keeping
them apart is what stops an Enterprise plan from being able to purchase a value
that stalls the server.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from database.manager import DatabaseError, database_manager, utc_now_iso


logger = logging.getLogger(__name__)


UNLIMITED = 0

LIMIT_KEYS: tuple[str, ...] = (
    "max_users",
    "max_channel_accounts",
    "max_ai_messages",
    "max_knowledge_items",
)

FEATURE_KEYS: tuple[str, ...] = (
    "voice_ai_enabled",
    "image_ai_enabled",
    "accounting_connector_enabled",
    "product_connector_enabled",
)

# Statuses that entitle a company to its plan. `grace_period` is included
# deliberately: it exists so a lapsed payment does not lock a business out of
# its own inbox mid-conversation.
ACTIVE_STATUSES = ("active", "trial", "grace_period")

# What each allowance counts, in words an owner would recognise. Used in the
# refusal so the message names the thing rather than the column.
LIMIT_LABELS: dict[str, str] = {
    "max_users": "team members",
    "max_channel_accounts": "connected channels",
    "max_ai_messages": "assistant replies this month",
    "max_knowledge_items": "knowledge items",
}


class PlanLimitExceeded(Exception):
    """A company asked for more than its plan allows.

    Carries the numbers rather than only a sentence: the caller turns this into
    an HTTP response, and a refusal that does not say what the limit was and
    how much is used leaves the customer with nothing to act on.
    """

    def __init__(self, *, limit_key: str, limit: int, used: int, company_id: int):
        self.limit_key = limit_key
        self.limit = int(limit)
        self.used = int(used)
        self.company_id = int(company_id)

        label = LIMIT_LABELS.get(limit_key, limit_key)

        super().__init__(
            f"This plan allows {self.limit} {label}, and {self.used} are in use. "
            f"Upgrade the plan or ask your platform administrator to raise it."
        )

    def as_detail(self) -> dict[str, Any]:
        return {
            "error": "plan_limit_exceeded",
            "message": str(self),
            "limit_key": self.limit_key,
            "limit": self.limit,
            "used": self.used,
        }


def _parse(value: Any) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def current_period(now: datetime | None = None) -> str:
    """The billing month, as ``YYYY-MM``.

    A calendar month rather than a rolling window from the subscription date:
    an owner reconciling a bill counts what happened in a month, and a rolling
    window means the number on the screen never matches the one on the invoice.
    """
    moment = now or datetime.now(timezone.utc)

    return f"{moment.year:04d}-{moment.month:02d}"


class PlanService:
    # ------------------------------------------------------------ resolution

    def subscription(self, company_id: int, conn: Any = None) -> dict[str, Any] | None:
        """This company's most recent subscription, with its plan.

        Most recent rather than "the active one": a caller that needs to tell a
        customer their plan expired needs the expired row, not nothing.
        """
        if conn is not None:
            return self._subscription(conn, company_id)

        with database_manager.control() as connection:
            return self._subscription(connection, company_id)

    @staticmethod
    def _subscription(conn: Any, company_id: int) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT
                subscriptions.id,
                subscriptions.company_id,
                subscriptions.plan_id,
                subscriptions.status,
                subscriptions.starts_at,
                subscriptions.expires_at,
                subscriptions.grace_period_until,
                plans.name AS plan_name,
                plans.code AS plan_code,
                plans.max_users,
                plans.max_channel_accounts,
                plans.max_ai_messages,
                plans.max_knowledge_items,
                plans.voice_ai_enabled,
                plans.image_ai_enabled,
                plans.accounting_connector_enabled,
                plans.product_connector_enabled
            FROM subscriptions
            JOIN plans ON plans.id = subscriptions.plan_id
            WHERE subscriptions.company_id = ?
            ORDER BY subscriptions.id DESC
            LIMIT 1
            """,
            (int(company_id),),
        ).fetchone()

        return dict(row) if row else None

    @staticmethod
    def is_active(subscription: dict[str, Any] | None) -> bool:
        """Whether this subscription entitles the company to its plan today.

        **A blank expiry does not expire.** The console's own form says so, and
        the previous implementation returned False for exactly that case — so
        every company deliberately set up never to expire read as unsubscribed.
        """
        if not subscription:
            return False

        if subscription.get("status") not in ACTIVE_STATUSES:
            return False

        expiry = _parse(subscription.get("expires_at"))

        if expiry is None:
            # Either no date was set (a plan that does not expire) or the
            # stored value is unreadable. Both resolve to entitled: refusing a
            # paying company over a malformed timestamp is the worse error, and
            # it would be invisible until a customer called.
            return True

        if expiry >= datetime.now(timezone.utc):
            return True

        # Past the date, but a grace period may still be running. This is the
        # difference between a lapsed payment and a cancelled account.
        grace = _parse(subscription.get("grace_period_until"))

        return bool(grace and grace >= datetime.now(timezone.utc))

    def active_subscription(self, company_id: int) -> dict[str, Any] | None:
        subscription = self.subscription(company_id)

        return subscription if self.is_active(subscription) else None

    # ---------------------------------------------------------------- limits

    def overrides(self, company_id: int, conn: Any = None) -> dict[str, int]:
        """This company's departures from its plan, keyed by limit."""

        def read(connection: Any) -> dict[str, int]:
            rows = connection.execute(
                "SELECT limit_key, value FROM company_plan_overrides WHERE company_id = ?",
                (int(company_id),),
            ).fetchall()

            return {
                str(row["limit_key"]): int(row["value"])
                for row in rows
                if str(row["limit_key"]) in LIMIT_KEYS
            }

        if conn is not None:
            return read(conn)

        with database_manager.control() as connection:
            return read(connection)

    def limits(self, company_id: int) -> dict[str, int]:
        """The effective allowance for each limit: the override, else the plan.

        A company with no active subscription gets every allowance as unlimited
        rather than zero. Zero here would mean a billing lapse silently locks a
        business out of its own workspace — the subscription's consequences are
        a decision for the operator to make explicitly, not a side effect of a
        limit lookup.
        """
        with database_manager.control() as conn:
            subscription = self._subscription(conn, company_id)
            overrides = self.overrides(company_id, conn=conn)

        entitled = self.is_active(subscription)

        resolved: dict[str, int] = {}

        for key in LIMIT_KEYS:
            if key in overrides:
                resolved[key] = max(0, overrides[key])
                continue

            if not entitled or not subscription:
                resolved[key] = UNLIMITED
                continue

            try:
                resolved[key] = max(0, int(subscription.get(key) or 0))
            except (TypeError, ValueError):
                resolved[key] = UNLIMITED

        return resolved

    def limit(self, company_id: int, limit_key: str) -> int:
        if limit_key not in LIMIT_KEYS:
            raise KeyError(
                f"{limit_key!r} is not a plan limit. "
                f"Valid keys are: {', '.join(LIMIT_KEYS)}."
            )

        return self.limits(company_id).get(limit_key, UNLIMITED)

    def features(self, company_id: int) -> dict[str, bool]:
        """Which optional capabilities this company's plan includes.

        Off for a company with no active subscription: unlike an allowance,
        a feature nobody paid for was never theirs to keep.
        """
        subscription = self.active_subscription(company_id)

        if not subscription:
            return {key: False for key in FEATURE_KEYS}

        return {key: bool(subscription.get(key)) for key in FEATURE_KEYS}

    # ------------------------------------------------------------ enforcement

    def check(self, company_id: int, limit_key: str, used: int) -> None:
        """Refuse when ``used`` has already reached the allowance.

        Called before the write, with the count that exists now. ``used >=
        limit`` rather than ``>``: the caller is asking to add one more.

        A control-plane failure allows the write. Refusing would take a working
        company's workspace down over a number nobody changed, and the write
        being guarded is an ordinary one the customer is entitled to make.
        """
        try:
            allowance = self.limit(company_id, limit_key)
        except DatabaseError:
            logger.exception(
                "Could not read plan limits for company %s; allowing %s.",
                company_id,
                limit_key,
            )

            return

        if allowance == UNLIMITED:
            return

        if int(used) >= allowance:
            raise PlanLimitExceeded(
                limit_key=limit_key,
                limit=allowance,
                used=int(used),
                company_id=int(company_id),
            )

    def headroom(self, company_id: int, limit_key: str, used: int) -> dict[str, Any]:
        """How much of one allowance is left, for a screen or a warning."""
        allowance = self.limit(company_id, limit_key)
        used = max(0, int(used))

        if allowance == UNLIMITED:
            return {
                "limit_key": limit_key,
                "limit": UNLIMITED,
                "unlimited": True,
                "used": used,
                "remaining": None,
                "percent": None,
            }

        return {
            "limit_key": limit_key,
            "limit": allowance,
            "unlimited": False,
            "used": used,
            "remaining": max(0, allowance - used),
            "percent": round(min(100.0, (used / allowance) * 100), 1),
        }

    # -------------------------------------------------------------- overrides

    def set_override(
        self,
        *,
        company_id: int,
        limit_key: str,
        value: int,
        note: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, int]:
        if limit_key not in LIMIT_KEYS:
            raise KeyError(
                f"{limit_key!r} is not a plan limit. "
                f"Valid keys are: {', '.join(LIMIT_KEYS)}."
            )

        value = max(0, int(value))
        now = utc_now_iso()

        with database_manager.control() as conn:
            conn.execute(
                """
                INSERT INTO company_plan_overrides (
                    company_id, limit_key, value, note,
                    created_at, updated_at, updated_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, limit_key) DO UPDATE SET
                    value = excluded.value,
                    note = excluded.note,
                    updated_at = excluded.updated_at,
                    updated_by_user_id = excluded.updated_by_user_id
                """,
                (
                    int(company_id),
                    limit_key,
                    value,
                    note,
                    now,
                    now,
                    int(actor_user_id) if actor_user_id is not None else None,
                ),
            )
            conn.commit()

        return self.limits(company_id)

    def clear_override(self, *, company_id: int, limit_key: str) -> dict[str, int]:
        """Put this company back on its plan for one allowance."""
        with database_manager.control() as conn:
            conn.execute(
                "DELETE FROM company_plan_overrides WHERE company_id = ? AND limit_key = ?",
                (int(company_id), str(limit_key)),
            )
            conn.commit()

        return self.limits(company_id)

    # ------------------------------------------------------------------ usage

    def record_usage(
        self,
        *,
        company_id: int,
        metric: str,
        quantity: int = 1,
        channel: str | None = None,
        department_id: int | None = None,
        period: str | None = None,
    ) -> None:
        """Add to a counter. Numbers only — never a word of what was said.

        Never raises. A counter that can fail a reply is a counter that costs a
        customer their answer, which is a far worse outcome than a usage number
        that is low by one. Failures are logged.
        """
        try:
            with database_manager.control() as conn:
                now = utc_now_iso()

                conn.execute(
                    """
                    INSERT INTO usage_records (
                        company_id, period, metric, channel, department_id,
                        quantity, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, period, metric, channel, department_id)
                    DO UPDATE SET
                        quantity = quantity + excluded.quantity,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(company_id),
                        period or current_period(),
                        str(metric),
                        str(channel).lower() if channel else None,
                        int(department_id) if department_id is not None else None,
                        max(0, int(quantity)),
                        now,
                        now,
                    ),
                )
                conn.commit()
        except Exception:
            logger.exception(
                "Could not record %s usage for company %s", metric, company_id
            )

    def usage_total(
        self,
        *,
        company_id: int,
        metric: str,
        period: str | None = None,
    ) -> int:
        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS total FROM usage_records
                WHERE company_id = ? AND period = ? AND metric = ?
                """,
                (int(company_id), period or current_period(), str(metric)),
            ).fetchone()

        return int(row["total"]) if row else 0

    def usage_breakdown(
        self,
        *,
        company_id: int,
        period: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every counter for one month, for the usage screen."""
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT metric, channel, department_id, quantity, updated_at
                FROM usage_records
                WHERE company_id = ? AND period = ?
                ORDER BY metric, channel, department_id
                """,
                (int(company_id), period or current_period()),
            ).fetchall()

        return [dict(row) for row in rows]


plan_service = PlanService()
