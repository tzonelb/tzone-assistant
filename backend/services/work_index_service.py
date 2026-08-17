"""Which companies have background work outstanding.

The background sweeps used to answer that question by opening every company's
encrypted database and asking it. At one company that is a rounding error; at a
thousand it is a thousand SQLCipher opens every two seconds, in sequence, almost
all of them to be told there is nothing to do. The sweep then takes longer than
its own interval, so the two-second cadence becomes a fiction and customers get
their replies late.

This module keeps the answer in the control database instead — the one database
a worker can read without opening anything belonging to a customer. A row here
says only "company 42 has a reply due at 12:01:03": no customer, no channel, no
message, no content. That is a scheduling fact about the platform's own queue,
which is exactly what the control plane is for.

The index is an accelerator, never the truth
--------------------------------------------
The tenant tables remain authoritative. This index only decides *which
databases are worth opening*; once a company is open, its own queue decides what
runs. That separation is what makes the whole thing safe, because it means the
index is allowed to be wrong in one direction:

* **too eager** — an entry for a company with nothing due costs one database
  open, which is precisely what the old sweep paid for every company anyway;
* **too lazy** — a missing entry means nobody ever opens that company, and the
  work simply never runs. A customer's reply is never sent. There is no error,
  no log line, nothing to notice.

Every rule below is chosen to make the second case unreachable, and the first
case self-correcting:

1. **Adding is unconditional and eager.** ``note`` only ever moves a deadline
   *earlier*. A writer that queues work can never be talked out of registering
   it, and a batch whose delivery is pushed back stays registered at its old,
   earlier time until a sweep corrects it.
2. **Adding happens before the work is committed.** A writer registers the
   deadline while its tenant transaction is still open, so a control-plane
   failure aborts the enqueue instead of committing work nothing will collect.
   The reverse order can strand work; this order can only produce an entry for
   work that was rolled back, which costs one open.
3. **Removing is conditional.** Only a sweep — which has just re-read the tenant
   tables — may lower or delete an entry, and only if the row's ``revision`` has
   not moved since it looked. A writer that registered new work in the meantime
   wins, and the sweep leaves the eager entry alone.
4. **Everything is reconciled anyway.** ``reconcile_all`` rebuilds the index
   from the tenant tables. It runs at boot — which is how the index survives a
   restart, and how a database written by a release that predates it is picked
   up — and hourly after that, so any entry lost to a crash between (2) and the
   commit is bounded by one hour rather than by forever.

Suspended companies
-------------------
``due_companies`` filters to active companies with a provisioned database, which
is the same set ``database_manager.list_company_ids`` returns and therefore the
same set the sweeps served before this change. A suspended company keeps its
index rows — nothing prunes them, because nothing sweeps it — so when it is
reactivated its outstanding work is found immediately rather than having to wait
for the next reconcile. This is deliberately not a decision about whether a
suspended company *should* be served: it preserves the behaviour that already
existed, because changing who gets served is not a scheduling change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from database.manager import database_manager


logger = logging.getLogger(__name__)


KIND_PENDING_REPLY = "pending_reply"
KIND_SCHEDULED_POST = "scheduled_post"
KIND_TAKEOVER = "takeover"

KINDS: tuple[str, ...] = (KIND_PENDING_REPLY, KIND_SCHEDULED_POST, KIND_TAKEOVER)


# The authoritative question behind each kind, asked of the company's own
# database. Each one mirrors the WHERE clause of the claim it feeds, minus the
# lease: a batch somebody is currently holding stays in the index, because it
# may yet fail and need retrying, and being early here is the harmless
# direction.
_DUE_QUERIES: dict[str, str] = {
    KIND_PENDING_REPLY: """
        SELECT MIN(deliver_after) AS due FROM pending_replies
    """,
    KIND_SCHEDULED_POST: """
        SELECT MIN(scheduled_for) AS due FROM scheduled_posts
        WHERE status = 'approved'
    """,
    KIND_TAKEOVER: """
        SELECT MIN(takeover_expires_at) AS due FROM conversations
        WHERE handled_by_ai = 0 AND takeover_expires_at IS NOT NULL
    """,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkIndexService:
    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def note(self, company_id: int, kind: str, due_at: Any) -> None:
        """Register outstanding work, keeping whichever deadline is earlier.

        Called by whoever queues the work, while its own transaction is still
        open. Raising here is intentional: an enqueue that cannot be registered
        must fail rather than commit work no sweep will collect.

        Only ever moves a deadline earlier. A deadline pushed later — a customer
        who keeps typing, an employee who renews a takeover — leaves the earlier
        entry in place, so the next sweep opens the company, finds nothing due,
        and rewrites the entry with the real deadline. One wasted open, and the
        index converges.
        """
        kind = self._validate_kind(kind)
        deadline = self._normalise(due_at)

        if deadline is None:
            return

        now = utc_now_iso()

        with database_manager.control() as conn:
            conn.execute(
                """
                INSERT INTO company_work_index (
                    company_id, kind, due_at, revision, updated_at
                )
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(company_id, kind) DO UPDATE SET
                    due_at = MIN(company_work_index.due_at, excluded.due_at),
                    revision = company_work_index.revision + 1,
                    updated_at = excluded.updated_at
                """,
                (int(company_id), kind, deadline, now),
            )
            conn.commit()

    def refresh(
        self, company_id: int, kinds: Iterable[str] = KINDS
    ) -> dict[str, str | None]:
        """Rewrite a company's entries from its own tables. Returns the deadlines.

        This is the only path that may make the index *less* eager, and it is
        run by a sweep that has just finished a company — including a sweep that
        found nothing, which is how a stale entry is cleared.

        The read-then-write is guarded by ``revision``. Between reading the
        company's queue and writing the result, a webhook may have queued
        something new; without the guard this would delete an entry for work
        that really exists, which is the one failure this index must never have.
        With it, the concurrent writer wins and its eager entry survives.
        """
        selected = [self._validate_kind(kind) for kind in kinds]
        before = self._entries(int(company_id))

        due: dict[str, str | None] = {}

        with database_manager.tenant(int(company_id)) as conn:
            for kind in selected:
                row = conn.execute(_DUE_QUERIES[kind]).fetchone()
                due[kind] = self._normalise(row["due"] if row else None)

        self._write_back(int(company_id), due, before)

        return due

    def reconcile_company(self, company_id: int) -> dict[str, str | None]:
        """Rebuild every kind for one company. The backstop for rule (4)."""
        return self.refresh(company_id, KINDS)

    def reconcile_all(self) -> dict[str, int]:
        """Rebuild the whole index from the tenant databases.

        Run at boot — which is what makes the index survive a restart, and what
        picks up queues written by a release that predates it — and hourly by
        the maintenance worker.

        Every provisioned company is visited, suspended ones included: their
        rows are never touched by a sweep, so leaving them unreconciled is the
        one way an entry could stay wrong indefinitely.

        One company at a time on purpose. It is the only full sweep left, it
        runs twice a day at most, and a failure has to be attributable to a
        company rather than lost in a batch.
        """
        company_ids = self.provisioned_company_ids()

        indexed = 0
        failed = 0

        for company_id in company_ids:
            try:
                due = self.reconcile_company(company_id)
            except Exception:
                failed += 1
                logger.exception(
                    "Could not reconcile the work index for company %s", company_id
                )
                continue

            if any(value is not None for value in due.values()):
                indexed += 1

        return {
            "companies": len(company_ids),
            "with_work": indexed,
            "failed": failed,
        }

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def due_companies(
        self, kind: str, now: str | None = None, limit: int | None = None
    ) -> list[int]:
        """Companies with work of this kind already due, earliest first.

        Restricted to active companies with a provisioned database — the same
        set ``database_manager.list_company_ids`` returns, so this changes how
        many databases a sweep opens and not which companies it serves.

        Earliest first matters: it makes a sweep that cannot finish its list
        within one tick fair rather than arbitrary, because whoever waited
        longest is served first on the next one.
        """
        kind = self._validate_kind(kind)
        cutoff = now or utc_now_iso()

        sql = """
            SELECT company_work_index.company_id AS company_id
            FROM company_work_index
            JOIN companies
                ON companies.id = company_work_index.company_id
            JOIN company_databases
                ON company_databases.company_id = company_work_index.company_id
            WHERE company_work_index.kind = ?
              AND company_work_index.due_at <= ?
              AND companies.status = 'active'
            ORDER BY company_work_index.due_at ASC
        """

        params: list[Any] = [kind, cutoff]

        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with database_manager.control() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [int(row["company_id"]) for row in rows]

    def provisioned_company_ids(self) -> list[int]:
        """Every company with a database file, suspended ones included."""
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT company_id FROM company_databases ORDER BY company_id
                """
            ).fetchall()

        return [int(row["company_id"]) for row in rows]

    def snapshot(self, company_id: int) -> dict[str, dict[str, Any]]:
        """Diagnostics view of one company's entries."""
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT kind, due_at, revision, updated_at
                FROM company_work_index
                WHERE company_id = ?
                """,
                (int(company_id),),
            ).fetchall()

        return {str(row["kind"]): dict(row) for row in rows}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_kind(kind: str) -> str:
        text = str(kind or "").strip().lower()

        if text not in KINDS:
            raise ValueError(f"Unknown work kind: {kind!r}")

        return text

    @staticmethod
    def _normalise(value: Any) -> str | None:
        """Accept a timestamp as text or datetime; reject anything empty.

        Stored as text, and compared as text, because that is how both queues
        already compare their own deadlines. Timestamps written by this platform
        always carry the ``+00:00`` offset, so the ordering is chronological. A
        naive one from an older row sorts earlier than its true position, which
        is the eager direction.
        """
        if value is None:
            return None

        if isinstance(value, datetime):
            moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return moment.astimezone(timezone.utc).isoformat()

        text = str(value).strip()

        return text or None

    def _entries(self, company_id: int) -> dict[str, tuple[str, int]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT kind, due_at, revision FROM company_work_index
                WHERE company_id = ?
                """,
                (company_id,),
            ).fetchall()

        return {
            str(row["kind"]): (str(row["due_at"]), int(row["revision"]))
            for row in rows
        }

    def _write_back(
        self,
        company_id: int,
        due: dict[str, str | None],
        before: dict[str, tuple[str, int]],
    ) -> None:
        # The common case by a wide margin is a company whose deadline has not
        # moved — a batch still leased by the worker that just claimed it, a
        # post still waiting for its minute. Writing an unchanged row would cost
        # a control-plane commit on every company on every tick, which is the
        # kind of per-tick cost this index exists to remove.
        changes = {
            kind: deadline
            for kind, deadline in due.items()
            if before.get(kind, (None, None))[0] != deadline
        }

        if not changes:
            return

        now = utc_now_iso()

        with database_manager.control() as conn:
            for kind, deadline in changes.items():
                seen = before.get(kind, (None, None))[1]

                if deadline is None:
                    if seen is None:
                        continue

                    # Conditional delete: if a writer bumped the revision while
                    # we were reading the company's tables, its work is real and
                    # this row must stay.
                    conn.execute(
                        """
                        DELETE FROM company_work_index
                        WHERE company_id = ? AND kind = ? AND revision = ?
                        """,
                        (company_id, kind, seen),
                    )
                    continue

                if seen is None:
                    conn.execute(
                        """
                        INSERT INTO company_work_index (
                            company_id, kind, due_at, revision, updated_at
                        )
                        VALUES (?, ?, ?, 1, ?)
                        ON CONFLICT(company_id, kind) DO UPDATE SET
                            due_at = MIN(
                                company_work_index.due_at, excluded.due_at
                            ),
                            revision = company_work_index.revision + 1,
                            updated_at = excluded.updated_at
                        """,
                        (company_id, kind, deadline, now),
                    )
                    continue

                conn.execute(
                    """
                    UPDATE company_work_index
                    SET due_at = ?,
                        revision = revision + 1,
                        updated_at = ?
                    WHERE company_id = ? AND kind = ? AND revision = ?
                    """,
                    (deadline, now, company_id, kind, seen),
                )

            conn.commit()


work_index_service = WorkIndexService()
