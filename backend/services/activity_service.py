"""One record of everything that happened in a company's workspace.

Of seventeen modules, three wrote any audit at all — and two of those three had
no endpoint to read it back, so the trail existed and nobody could see it. There
was no record of a knowledge item being edited, a price being changed, a channel
being connected or disconnected, a permission being granted, or an employee
signing in.

The price one is the sharpest: the assistant quotes catalogue prices to
customers as confirmed facts. An owner asking "who changed that, and when" had
nowhere to look.

This module is the one writer. Everything it records goes into the company's own
encrypted database, because it is the company's record of its own business. The
control plane gets a *mirror* of the security events only — what an operator
needs to run the platform, never the detail of what a business did inside it.

### Why the actor's name is copied rather than joined

`users` lives in the control plane and `activity_log` lives in the tenant file.
SQLite cannot join across two files, and three existing queries in
`conversation_control_service` try: they `LEFT JOIN users` inside a tenant
connection, match nothing, and render every actor as "System". Anyone reading
that timeline would conclude the platform did it.

So the display name is copied in at write time. It also survives the employee
leaving, which a join never would — and a log that forgets who did something the
moment they resign is not a log.

### Three kinds, because they need different treatment

* `change` — somebody altered something. Kept longest; this is the record.
* `read` — somebody opened a conversation or a customer file. High volume, and
  its own retention, so recording who read what does not bury who changed what.
* `security` — a sign-in, a refusal, a limit hit. Also mirrored to the control
  plane, so an operator can see an attack across companies that no single
  company's log would reveal.

### Writing never fails the thing it is recording

Every method swallows its own errors and logs them. An audit write that can fail
a price update means the price does not change because the note about it could
not be filed — the worst possible trade. A gap in the log is recoverable; a
customer-facing failure is not.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)


KINDS = ("change", "read", "security")

# Categories are the module keys, so a log entry filters with the same
# vocabulary the module switches and the permissions use. `auth` and `platform`
# are added because a sign-in and an operator's action belong to neither module.
CATEGORIES = (
    "ai_teaching",
    "analytics",
    "appointments",
    "auth",
    "catalogue",
    "channels",
    "comments",
    "company_settings",
    "conversations",
    "customers",
    "departments",
    "knowledge",
    "platform",
    "roles",
    "scheduler",
    "tasks",
    "team_chat",
)


def _loads(raw: Any) -> Any:
    """Stored JSON, or None. A row that will not parse must not take the whole
    history down with it — the point of reading these is to find out what
    happened, and that is most needed when something is wrong."""
    if not raw:
        return None

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Unreadable JSON in an audit row")
        return None


class Action:
    """Every action name in one place.

    Twenty-one event types were previously scattered through the codebase as
    bare string literals, and two of them were misspelled at the reading end:
    `analytics_service` counted `'human_took_over'` and `'assigned_user_changed'`
    while the writers wrote `human_takeover` and `assignment_changed`. Both
    counters read zero for every company, for ever, and nothing said so.

    A constant does not prevent a typo. It makes one fail at import rather than
    silently return zero.
    """

    # --- knowledge
    KNOWLEDGE_CREATED = "knowledge.item_created"
    KNOWLEDGE_UPDATED = "knowledge.item_updated"
    KNOWLEDGE_DELETED = "knowledge.item_deleted"
    KNOWLEDGE_CATEGORY_CREATED = "knowledge.category_created"

    # --- catalogue. Price changes are called out separately: the assistant
    # states them to customers as confirmed facts.
    PRODUCT_CREATED = "catalogue.product_created"
    PRODUCT_UPDATED = "catalogue.product_updated"
    PRODUCT_PRICE_CHANGED = "catalogue.price_changed"
    PRODUCT_DELETED = "catalogue.product_deleted"

    # --- channels
    CHANNEL_CONNECTED = "channels.account_connected"
    CHANNEL_UPDATED = "channels.account_updated"
    CHANNEL_CREDENTIALS_REPLACED = "channels.credentials_replaced"
    CHANNEL_DISCONNECTED = "channels.account_disconnected"

    # --- roles and access
    ROLE_CREATED = "roles.role_created"
    ROLE_UPDATED = "roles.role_updated"
    PERMISSIONS_CHANGED = "roles.permissions_changed"
    USER_ADDED = "roles.user_added"
    USER_UPDATED = "roles.user_updated"
    USER_PASSWORD_RESET = "roles.password_reset_forced"
    USER_UNLOCKED = "roles.user_unlocked"
    BRANCH_CREATED = "roles.branch_created"
    BRANCH_UPDATED = "roles.branch_updated"
    BRANCH_DELETED = "roles.branch_deleted"

    # --- the assistant
    BOT_PROFILE_UPDATED = "ai_teaching.profile_updated"
    REPLY_POLICY_UPDATED = "ai_teaching.reply_policy_updated"
    DEPARTMENT_CREATED = "departments.created"
    DEPARTMENT_UPDATED = "departments.updated"
    DEPARTMENT_DELETED = "departments.deleted"

    # --- the rest of the workspace
    SETTINGS_UPDATED = "company_settings.updated"
    TASK_CREATED = "tasks.created"
    TASK_UPDATED = "tasks.updated"
    APPOINTMENT_CREATED = "appointments.created"
    APPOINTMENT_UPDATED = "appointments.updated"
    POST_SCHEDULED = "scheduler.post_scheduled"
    POST_APPROVED = "scheduler.post_approved"
    COMMENT_REPLIED = "comments.replied"

    CUSTOMER_UPDATED = "customers.updated"

    # --- reads, kept apart from changes
    CONVERSATION_OPENED = "conversations.opened"
    CONVERSATION_EXPORTED = "conversations.exported"
    CUSTOMER_OPENED = "customers.opened"

    # --- security, mirrored to the control plane
    SIGNED_IN = "auth.signed_in"
    SIGN_IN_FAILED = "auth.sign_in_failed"
    WORKSPACE_CODE_REJECTED = "auth.workspace_code_rejected"
    ACCOUNT_LOCKED = "auth.account_locked"
    PASSWORD_CHANGED = "auth.password_changed"
    PERMISSION_DENIED = "auth.permission_denied"
    PLAN_LIMIT_HIT = "platform.plan_limit_hit"
    WEBHOOK_SIGNATURE_REJECTED = "platform.webhook_signature_rejected"


# Which actions are mirrored into the control plane. Everything else stays in
# the company's own database: an operator running the platform needs to see an
# attack, not what a business sells or teaches its assistant.
SECURITY_ACTIONS = frozenset(
    {
        Action.SIGNED_IN,
        Action.SIGN_IN_FAILED,
        Action.WORKSPACE_CODE_REJECTED,
        Action.ACCOUNT_LOCKED,
        Action.PASSWORD_CHANGED,
        Action.PERMISSION_DENIED,
        Action.PLAN_LIMIT_HIT,
        Action.WEBHOOK_SIGNATURE_REJECTED,
        Action.CHANNEL_CONNECTED,
        Action.CHANNEL_DISCONNECTED,
        Action.CHANNEL_CREDENTIALS_REPLACED,
        Action.PERMISSIONS_CHANGED,
    }
)


def _dumps(value: Any) -> str | None:
    if value is None:
        return None

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"unserialisable": str(type(value))})


class ActivityService:
    MAX_LIMIT = 200

    # Retention, in days, per kind. Reads expire soonest because they are the
    # highest volume by far and the least useful after the fact; security is
    # kept longest because an investigation starts after the damage.
    RETENTION_DAYS = {"change": 730, "read": 90, "security": 730}

    # ------------------------------------------------------------------ write

    def record(
        self,
        *,
        company_id: int,
        action: str,
        category: str,
        kind: str = "change",
        actor_user_id: int | None = None,
        actor_label: str | None = None,
        target_type: str | None = None,
        target_id: Any = None,
        summary: str | None = None,
        before: Any = None,
        after: Any = None,
        ip_address: str | None = None,
        severity: str = "info",
    ) -> None:
        """File one entry. Never raises.

        An audit write that can fail a price update means the price does not
        change because the note about it could not be filed. A gap in the log is
        recoverable; refusing the customer's work is not.
        """
        if kind not in KINDS:
            kind = "change"

        database_manager.after_release(
            lambda: self._write(
                company_id=company_id,
                action=action,
                category=category,
                kind=kind,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
                target_type=target_type,
                target_id=target_id,
                summary=summary,
                before=before,
                after=after,
                ip_address=ip_address,
                severity=severity,
            )
        )

    def _write(
        self,
        *,
        company_id: int,
        action: str,
        category: str,
        kind: str,
        actor_user_id: int | None,
        actor_label: str | None,
        target_type: str | None,
        target_id: Any,
        summary: str | None,
        before: Any,
        after: Any,
        ip_address: str | None,
        severity: str,
    ) -> None:
        """The body of `record`, run once this thread holds no database open.

        Split out rather than inlined because the caller is very often inside
        an open transaction on the very database being written to, and a second
        connection would then wait on its caller until `busy_timeout` expired.
        `DatabaseManager.after_release` documents the fifteen-second refusal
        that made this necessary.
        """
        try:
            with database_manager.tenant(int(company_id)) as conn:
                conn.execute(
                    """
                    INSERT INTO activity_log (
                        company_id, kind, category, action, actor_user_id,
                        actor_label, target_type, target_id, summary,
                        before_json, after_json, ip_address, severity, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(company_id),
                        kind,
                        str(category),
                        str(action),
                        int(actor_user_id) if actor_user_id is not None else None,
                        actor_label,
                        target_type,
                        str(target_id) if target_id is not None else None,
                        summary,
                        _dumps(before),
                        _dumps(after),
                        ip_address,
                        str(severity),
                        utc_now_iso(),
                    ),
                )
                conn.commit()
        except Exception:
            logger.exception(
                "Could not record %s for company %s", action, company_id
            )

        if action in SECURITY_ACTIONS:
            self._mirror(
                company_id=company_id,
                action=action,
                actor_user_id=actor_user_id,
                target_type=target_type,
                target_id=target_id,
                ip_address=ip_address,
                summary=summary,
            )

    def record_for(
        self,
        current_user: dict[str, Any] | None,
        *,
        company_id: int,
        action: str,
        category: str,
        **fields: Any,
    ) -> None:
        """`record`, with the actor filled in from a request's user.

        The label is taken from the session's own user rather than looked up, so
        recording costs no extra database read on a path that is already doing
        real work.
        """
        user = current_user or {}

        fields.setdefault("actor_user_id", user.get("id"))
        fields.setdefault(
            "actor_label",
            user.get("full_name") or user.get("email") or None,
        )

        self.record(
            company_id=company_id, action=action, category=category, **fields
        )

    def record_unattributed(
        self,
        *,
        action: str,
        summary: str | None = None,
        ip_address: str | None = None,
        target_id: Any = None,
    ) -> None:
        """A security event that belongs to no company, in the control plane only.

        A refused sign-in is the case. The email may not exist, or may exist in
        a company the caller never named — and looking it up to decide where to
        file the entry would take a different amount of time depending on
        whether the account is real. That is a timing oracle for enumerating
        employees, on the one endpoint an attacker is already pointed at.
        `authenticate` runs a dummy password check for exactly this reason;
        undoing that work to write a nicer log entry would be a poor trade.

        So the entry goes to the control plane with no company. An operator can
        still see the attack — the shape of it across the platform is what
        matters there anyway — and the company's own log gets the events that
        *can* be attributed, such as the lock that follows.
        """
        self._mirror(
            company_id=None,
            action=action,
            actor_user_id=None,
            target_type="auth",
            target_id=target_id,
            ip_address=ip_address,
            summary=summary,
        )

    def _mirror(
        self,
        *,
        company_id: int | None,
        action: str,
        actor_user_id: int | None,
        target_type: str | None,
        target_id: Any,
        ip_address: str | None,
        summary: str | None = None,
    ) -> None:
        """Copy a security event to the control plane.

        Deliberately without `before`/`after`. An operator needs to see that a
        company was attacked, from where and how often — an attack spread
        across a thousand companies is invisible in any single company's log.
        What the company sells, teaches or tells its customers is not part of
        that, and copying it here would walk around the tenant boundary the rest
        of this platform keeps.
        """
        database_manager.after_release(
            lambda: self._mirror_write(
                company_id=company_id,
                action=action,
                actor_user_id=actor_user_id,
                target_type=target_type,
                target_id=target_id,
                ip_address=ip_address,
                summary=summary,
            )
        )

    def _mirror_write(
        self,
        *,
        company_id: int | None,
        action: str,
        actor_user_id: int | None,
        target_type: str | None,
        target_id: Any,
        ip_address: str | None,
        summary: str | None,
    ) -> None:
        try:
            with database_manager.control() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_log (
                        company_id, actor_user_id, action, target_type,
                        target_id, data_json, ip_address, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(company_id) if company_id is not None else None,
                        int(actor_user_id) if actor_user_id is not None else None,
                        str(action),
                        target_type,
                        str(target_id) if target_id is not None else None,
                        # A one-line summary only — never the before/after
                        # values, which are the company's business.
                        _dumps({"summary": summary}) if summary else None,
                        ip_address,
                        utc_now_iso(),
                    ),
                )
                conn.commit()
        except Exception:
            logger.exception(
                "Could not mirror %s for company %s to the control plane",
                action,
                company_id,
            )

    # ------------------------------------------------------------------- read

    def list_entries(
        self,
        *,
        company_id: int,
        kind: str | None = None,
        category: str | None = None,
        action: str | None = None,
        actor_user_id: int | None = None,
        search: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """The company's own log, newest first."""
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        for column, value in (
            ("kind", kind),
            ("category", category),
            ("action", action),
        ):
            if value:
                where.append(f"{column} = ?")
                params.append(str(value))

        if actor_user_id is not None:
            where.append("actor_user_id = ?")
            params.append(int(actor_user_id))

        if since:
            where.append("created_at >= ?")
            params.append(str(since))

        if until:
            where.append("created_at <= ?")
            params.append(str(until))

        if search:
            where.append("(summary LIKE ? OR actor_label LIKE ? OR target_id LIKE ?)")
            params.extend([f"%{search}%"] * 3)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM activity_log WHERE {clause}",
                    params,
                ).fetchone()["total"]
            )

            rows = conn.execute(
                f"""
                SELECT * FROM activity_log
                WHERE {clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        items = []

        for row in rows:
            entry = dict(row)
            entry["before"] = self._loads(entry.pop("before_json", None))
            entry["after"] = self._loads(entry.pop("after_json", None))
            items.append(entry)

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _loads(value: Any) -> Any:
        if not value:
            return None

        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None

    def options(self, company_id: int) -> dict[str, Any]:
        """The filters this company's log can actually offer.

        Built from what is in the table rather than from the full catalogue: a
        dropdown listing thirty actions a company has never performed is a
        dropdown nobody can use.
        """
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT category, action FROM activity_log
                WHERE company_id = ?
                ORDER BY category, action
                """,
                (int(company_id),),
            ).fetchall()

        return {
            "kinds": list(KINDS),
            "categories": sorted({str(row["category"]) for row in rows}),
            "actions": sorted({str(row["action"]) for row in rows}),
        }

    # ------------------------------------------------------------ the detail
    #
    # `activity_log` records that a settings section or a customer changed, and
    # which keys, never the values — a settings section is an open bag and a
    # customer field is somebody's phone number, and the log is read by the
    # whole company.
    #
    # The values do exist. `company_setting_audit` and `customer_audit` have
    # held the before-and-after since each shipped, and no endpoint has ever
    # read either. Rows accumulated where nobody could open them and nothing
    # pruned them.
    #
    # These two readers are what makes them a record instead of storage. They
    # sit behind the same permission as the thing they describe, and they are
    # deliberately narrow: the history of *one* section or *one* customer,
    # asked for on purpose, rather than a feed of every value the company has
    # ever held.

    def settings_history(
        self, *, company_id: int, section: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """What a settings section held before and after each change."""
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT id, section, actor_user_id, old_value_json,
                       new_value_json, created_at
                FROM company_setting_audit
                WHERE company_id = ? AND section = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (int(company_id), str(section), max(1, min(int(limit), 200))),
            ).fetchall()

        return [
            {
                "id": int(row["id"]),
                "section": row["section"],
                "actor_user_id": row["actor_user_id"],
                "before": _loads(row["old_value_json"]),
                "after": _loads(row["new_value_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def customer_history(
        self, *, company_id: int, customer_id: int, limit: int = 50
    ) -> list[dict[str, Any]]:
        """What changed on one customer's record, and what it was set to."""
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT id, customer_id, actor_user_id, action, data_json,
                       created_at
                FROM customer_audit
                WHERE company_id = ? AND customer_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (int(company_id), int(customer_id), max(1, min(int(limit), 200))),
            ).fetchall()

        return [
            {
                "id": int(row["id"]),
                "customer_id": int(row["customer_id"]),
                "actor_user_id": row["actor_user_id"],
                "action": row["action"],
                "changed": _loads(row["data_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # -------------------------------------------------------------- retention

    def prune(self, company_id: int) -> dict[str, int]:
        """Drop entries past their kind's retention. Returns what was removed."""
        from datetime import datetime, timedelta, timezone

        removed: dict[str, int] = {}
        now = datetime.now(timezone.utc)

        try:
            with database_manager.tenant(int(company_id)) as conn:
                for kind, days in self.RETENTION_DAYS.items():
                    cutoff = (now - timedelta(days=days)).isoformat()
                    cursor = conn.execute(
                        "DELETE FROM activity_log WHERE kind = ? AND created_at < ?",
                        (kind, cutoff),
                    )
                    removed[kind] = int(cursor.rowcount or 0)

                # The detail behind a change entry, pruned on the same clock as
                # the entry it belongs to. Keeping it longer would leave values
                # in the database after the record of who changed them is gone;
                # keeping it shorter would leave an entry pointing at a detail
                # that no longer exists.
                detail_cutoff = (
                    now - timedelta(days=self.RETENTION_DAYS["change"])
                ).isoformat()

                for table in ("company_setting_audit", "customer_audit"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE created_at < ?",
                        (detail_cutoff,),
                    )
                    removed[table] = int(cursor.rowcount or 0)

                conn.commit()
        except Exception:
            logger.exception("Could not prune the activity log for company %s", company_id)

            return {}

        return removed


activity_service = ActivityService()
