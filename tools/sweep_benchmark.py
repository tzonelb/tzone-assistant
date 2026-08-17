"""Measure what a background sweep costs before and after the work index.

Not part of the test suite, and deliberately so: it provisions hundreds of real
encrypted databases, and sealing each company's key behind a workspace code runs
600,000 PBKDF2 iterations by design. That is a minute of setup for a number,
which is worth having on demand and not on every commit.

    python -m tools.sweep_benchmark --companies 200 --with-work 5

It compares, for one tick of the assistant reply sweep:

    old   list every active company, open every company's database, ask its
          queue whether anything is due — which is what `_run_for_every_company`
          did on a two-second timer;
    new   read the control-plane work index once, open only the companies it
          names.

Both run the same claim against the same databases; the only difference is how
many databases are opened to find the same work.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _provision(manager, keyring, count: int) -> list[int]:
    from database.manager import utc_now_iso

    now = utc_now_iso()
    company_ids: list[int] = []

    with manager.control() as conn:
        conn.execute(
            """
            INSERT INTO workspaces (name, slug, status, created_at, updated_at)
            VALUES ('Benchmark', 'benchmark', 'active', ?, ?)
            """,
            (now, now),
        )

        for index in range(count):
            cursor = conn.execute(
                """
                INSERT INTO companies (
                    workspace_id, name, slug, status, created_at, updated_at
                )
                VALUES (1, ?, ?, 'active', ?, ?)
                """,
                (f"Company {index}", f"company-{index}", now, now),
            )
            company_ids.append(int(cursor.lastrowid))

        conn.commit()

    for company_id in company_ids:
        manager.provision_company(
            company_id=company_id,
            workspace_code=keyring.generate_workspace_code(),
        )

    return company_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", type=int, default=200)
    parser.add_argument(
        "--with-work",
        type=int,
        default=5,
        help="How many of them actually have a reply due.",
    )
    parser.add_argument("--ticks", type=int, default=5)
    args = parser.parse_args(argv)

    data_dir = Path(tempfile.mkdtemp(prefix="tzone-sweep-benchmark-"))
    os.environ.setdefault("DATA_DIR", str(data_dir))

    from backend.security import keyring

    if not os.getenv(keyring.MASTER_KEY_ENV):
        os.environ[keyring.MASTER_KEY_ENV] = keyring.generate_master_key()

    import database.manager as manager_module
    from database.manager import DatabaseManager

    manager = DatabaseManager(data_dir=data_dir)

    # Every service reads the module-level singleton; point it at this
    # throwaway platform the same way the test fixtures do.
    manager_module.database_manager = manager

    for module_name in (
        "backend.services.work_index_service",
        "backend.services.pending_reply_service",
    ):
        __import__(module_name)
        sys.modules[module_name].database_manager = manager

    from backend.services.pending_reply_service import pending_reply_service
    from backend.services.work_index_service import (
        KIND_PENDING_REPLY,
        work_index_service,
    )

    print(f"Provisioning {args.companies} encrypted company databases...")
    started = time.perf_counter()
    company_ids = _provision(manager, keyring, args.companies)
    print(f"  done in {time.perf_counter() - started:.1f}s\n")

    busy = company_ids[:: max(1, len(company_ids) // max(1, args.with_work))][
        : args.with_work
    ]

    for company_id in busy:
        pending_reply_service.enqueue(
            company_id=company_id,
            channel="messenger",
            external_user_id="benchmark-customer",
            message="hello",
            delay_seconds=0,
        )

        # `enqueue` clamps the wait to its minimum, so a freshly queued batch is
        # never immediately due. Bring it forward rather than sleeping for it,
        # and re-derive the index entry from the row the way a sweep does.
        with manager.tenant(company_id) as conn:
            conn.execute(
                "UPDATE pending_replies SET deliver_after = '2000-01-01T00:00:00+00:00'"
            )
            conn.commit()

        work_index_service.refresh(company_id, (KIND_PENDING_REPLY,))

    opened: list[int] = []
    original_tenant = manager.tenant

    def counting_tenant(company_id: int):
        opened.append(int(company_id))
        return original_tenant(company_id)

    manager.tenant = counting_tenant  # type: ignore[method-assign]

    def old_tick() -> None:
        for company_id in manager.list_company_ids():
            pending_reply_service.claim_due(company_id)

    def new_tick() -> None:
        for company_id in work_index_service.due_companies(KIND_PENDING_REPLY):
            pending_reply_service.claim_due(company_id)
            work_index_service.refresh(company_id, (KIND_PENDING_REPLY,))

    results: dict[str, tuple[float, int]] = {}

    for label, tick in (("old", old_tick), ("new", new_tick)):
        # One warm-up tick, then measure: the first tick of the new sweep
        # claims the batches and the rest find them leased, which is the steady
        # state a running platform is in.
        opened.clear()
        tick()
        opened.clear()

        started = time.perf_counter()
        for _ in range(args.ticks):
            tick()
        elapsed = (time.perf_counter() - started) / args.ticks
        results[label] = (elapsed, len(opened) / args.ticks)

    print(
        f"{args.companies} companies, {len(busy)} with a reply due, "
        f"{args.ticks} ticks each\n"
    )
    print(f"  {'':<6}{'per sweep':>12}{'db opens':>12}")
    for label in ("old", "new"):
        elapsed, opens = results[label]
        print(f"  {label:<6}{elapsed * 1000:>10.1f}ms{opens:>12.1f}")

    old_elapsed = results["old"][0]
    new_elapsed = results["new"][0]

    if new_elapsed > 0:
        print(f"\n  {old_elapsed / new_elapsed:.0f}x faster per sweep")

    print(
        f"\n  The reply sweep runs every 2s. Old: "
        f"{old_elapsed / 2 * 100:.0f}% of the interval spent sweeping. "
        f"New: {new_elapsed / 2 * 100:.2f}%."
    )

    shutil.rmtree(data_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
