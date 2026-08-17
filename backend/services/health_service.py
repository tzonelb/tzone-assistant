"""Does the platform actually work right now, and can it prove it?

`health.py` returned a constant. It said `{"status": "ok"}` whether or not the
master key was loadable, whether or not a single company database could be
opened, and whether or not the disk had run out — so the one endpoint a monitor
watches was the one thing on the platform that could not fail.

This module answers the question properly, and answers it on a timer rather than
only when somebody presses a button. The distinction matters: a corrupt company
database discovered when a customer writes in is an incident; the same corruption
found by a sweep at 3am is a restore.

### What is checked

* **The master key loads.** Nothing else works without it, so it is checked
  first and separately.
* **Every company database opens and decrypts.** This is the check that catches
  a lost key, a truncated file, or a restore from the wrong host.
* **`PRAGMA integrity_check`.** SQLite's own verification of its page
  structure. It did not appear anywhere in this repository, which meant silent
  corruption had nothing looking for it — and encrypted files fail in ways that
  are invisible until the moment you need the data.
* **The schema version matches** the code's, in both places it is recorded.
* **Disk space**, because every company database and every backup shares one
  filesystem and the failure mode is every write failing at once.

### Suspended companies are checked too

`list_company_ids` filters to active companies, correctly — the sweeps must not
deliver a suspended company's replies. A health check that reused it would
report a clean platform while a suspended company's file was corrupt. Their data
is still on disk, still encrypted, and still has to be readable on the day they
are reinstated, so this uses `list_all_company_ids`.

### The server metrics come from the standard library

`psutil` would be one more dependency to install, pin and audit for a handful of
numbers this platform's Linux host already publishes in `/proc`. Where a number
is genuinely unavailable it is reported as `None` rather than as a zero: a zero
is a reading, and a monitor cannot tell a real zero from a missing one.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from database.manager import DatabaseError, database_manager
from database.schema_tenant import TENANT_SCHEMA_VERSION


logger = logging.getLogger(__name__)


# Below this, a write can fail at any moment and every company shares the
# failure. Warned on well before it is a crisis, because the fix — pruning
# backups, resizing the volume — takes time an operator needs to have.
DISK_WARNING_PERCENT = 85.0
DISK_CRITICAL_PERCENT = 95.0

OK = "ok"
WARNING = "warning"
CRITICAL = "critical"

# Worst wins. A platform with one unreadable database is not "mostly ok".
_SEVERITY = {OK: 0, WARNING: 1, CRITICAL: 2}


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda status: _SEVERITY.get(status, 0), default=OK)


class HealthService:
    def __init__(self) -> None:
        self._last_report: dict[str, Any] | None = None
        self._started_at = time.time()

    # ------------------------------------------------------------------ checks

    def check_master_key(self) -> dict[str, Any]:
        """Checked first and on its own: nothing else works without it."""
        try:
            database_manager.master_key()
        except Exception as exc:
            return {
                "status": CRITICAL,
                "detail": f"The master key could not be loaded: {type(exc).__name__}",
            }

        return {"status": OK, "detail": "The master key is loaded."}

    def check_control_plane(self) -> dict[str, Any]:
        try:
            with database_manager.control() as conn:
                companies = int(
                    conn.execute(
                        "SELECT COUNT(*) AS total FROM companies"
                    ).fetchone()["total"]
                )
                admins = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS total FROM users
                        WHERE is_super_admin = 1 AND status = 'active'
                        """
                    ).fetchone()["total"]
                )
                integrity = str(
                    conn.execute("PRAGMA integrity_check").fetchone()[0]
                )
        except DatabaseError as exc:
            return {
                "status": CRITICAL,
                "detail": f"The control database could not be opened: {exc}",
            }

        if integrity != "ok":
            return {
                "status": CRITICAL,
                "detail": f"The control database failed its integrity check: {integrity}",
            }

        if admins == 0:
            # Not merely a warning: with no active administrator, nobody can
            # suspend a company, rotate a code or reinstate anything. The
            # platform is running and unadministrable.
            return {
                "status": CRITICAL,
                "companies": companies,
                "active_platform_admins": 0,
                "detail": (
                    "No active platform administrator. Create one with "
                    "`python -m tools.manage_platform create-super-admin`."
                ),
            }

        return {
            "status": OK,
            "companies": companies,
            "active_platform_admins": admins,
            "detail": "The control database opens and passes its integrity check.",
        }

    def check_company(self, company_id: int, *, deep: bool = False) -> dict[str, Any]:
        """One company's database: does it open, is it intact, is it current.

        ``deep`` runs `PRAGMA integrity_check`, which reads every page. It is
        off by default because on a large database it is slow enough to matter,
        and the periodic sweep is where it belongs — not on a request an
        operator is waiting for.
        """
        result: dict[str, Any] = {"company_id": int(company_id), "status": OK}

        try:
            with database_manager.tenant(int(company_id)) as conn:
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                result["schema_version"] = version
                result["expected_schema_version"] = TENANT_SCHEMA_VERSION

                if version != TENANT_SCHEMA_VERSION:
                    result["status"] = WARNING
                    result["detail"] = (
                        f"Schema is at version {version}, the code expects "
                        f"{TENANT_SCHEMA_VERSION}. Run "
                        "`python -m tools.manage_platform check` after upgrading."
                    )

                if deep:
                    integrity = str(
                        conn.execute("PRAGMA integrity_check").fetchone()[0]
                    )
                    result["integrity"] = integrity

                    if integrity != "ok":
                        result["status"] = CRITICAL
                        result["detail"] = (
                            f"The database failed its integrity check: {integrity}. "
                            "Restore this company from a backup."
                        )
        except DatabaseError as exc:
            result["status"] = CRITICAL
            result["detail"] = f"The database could not be opened: {exc}"
        except Exception as exc:  # noqa: BLE001
            # Broad, and it has to be: this runs over every company, and one
            # unreadable file must not stop the rest from being reported. The
            # type is preserved in the detail so it is diagnosable.
            result["status"] = CRITICAL
            result["detail"] = f"Unexpected failure: {type(exc).__name__}: {exc}"

        return result

    def check_companies(self, *, deep: bool = False) -> dict[str, Any]:
        """Every provisioned company, suspended ones included."""
        try:
            company_ids = database_manager.list_all_company_ids()
        except DatabaseError as exc:
            return {
                "status": CRITICAL,
                "detail": f"The company list could not be read: {exc}",
                "items": [],
            }

        items = [
            self.check_company(company_id, deep=deep) for company_id in company_ids
        ]

        failing = [item for item in items if item["status"] != OK]

        return {
            "status": _worst(*(item["status"] for item in items)) if items else OK,
            "checked": len(items),
            "failing": len(failing),
            # The whole list would be a thousand entries on a busy platform.
            # The failures are what an operator acts on; the totals say the
            # rest were looked at.
            "items": failing,
        }

    def check_disk(self) -> dict[str, Any]:
        """Space on the filesystem every database and backup shares."""
        try:
            path = Path(database_manager.data_dir)
            usage = shutil.disk_usage(path)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": WARNING,
                "detail": f"Disk usage could not be read: {type(exc).__name__}",
            }

        used_percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0

        if used_percent >= DISK_CRITICAL_PERCENT:
            status = CRITICAL
        elif used_percent >= DISK_WARNING_PERCENT:
            status = WARNING
        else:
            status = OK

        return {
            "status": status,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": used_percent,
            "detail": (
                f"{used_percent}% of the data volume is in use."
                if status == OK
                else (
                    f"{used_percent}% of the data volume is in use. Every "
                    "company shares this filesystem, so a full disk fails "
                    "every write at once."
                )
            ),
        }

    # ----------------------------------------------------------------- server

    def server(self) -> dict[str, Any]:
        """CPU, memory, disk and uptime, from the standard library.

        A number that cannot be read is reported as `None` rather than as zero.
        A monitor cannot tell a real zero from a missing one, and "0% memory
        used" reads as healthy.
        """
        return {
            "uptime_seconds": self._uptime(),
            "process_uptime_seconds": round(time.time() - self._started_at, 1),
            "load_average": self._load_average(),
            "cpu_count": os.cpu_count(),
            "memory": self._memory(),
            "disk": self.check_disk(),
        }

    @staticmethod
    def _uptime() -> float | None:
        try:
            with open("/proc/uptime", encoding="utf-8") as handle:
                return round(float(handle.read().split()[0]), 1)
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _load_average() -> dict[str, float] | None:
        try:
            one, five, fifteen = os.getloadavg()
        except (OSError, AttributeError):
            return None

        return {
            "1m": round(one, 2),
            "5m": round(five, 2),
            "15m": round(fifteen, 2),
        }

    @staticmethod
    def _memory() -> dict[str, Any] | None:
        try:
            values: dict[str, int] = {}

            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    parts = rest.split()

                    if parts:
                        values[key.strip()] = int(parts[0]) * 1024
        except (OSError, ValueError):
            return None

        total = values.get("MemTotal")
        available = values.get("MemAvailable")

        if not total:
            return None

        used = total - available if available is not None else None

        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "used_percent": (
                round((used / total) * 100, 1) if used is not None else None
            ),
        }

    # ----------------------------------------------------------------- report

    def report(self, *, deep: bool = False) -> dict[str, Any]:
        """The whole picture, in one object.

        ``deep`` runs `PRAGMA integrity_check` over every company. That reads
        every page of every database, so it belongs on the timer rather than on
        a request somebody is waiting for.
        """
        from database.manager import utc_now_iso

        started = time.perf_counter()

        master_key = self.check_master_key()

        # Short-circuited: with no master key, every company check below would
        # fail identically and produce a page of noise that says one thing.
        if master_key["status"] == CRITICAL:
            report = {
                "status": CRITICAL,
                "checked_at": utc_now_iso(),
                "deep": deep,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "checks": {"master_key": master_key},
                "server": self.server(),
            }
            self._last_report = report

            return report

        checks = {
            "master_key": master_key,
            "control_plane": self.check_control_plane(),
            "companies": self.check_companies(deep=deep),
            "disk": self.check_disk(),
        }

        report = {
            "status": _worst(*(check["status"] for check in checks.values())),
            "checked_at": utc_now_iso(),
            "deep": deep,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "checks": checks,
            "server": self.server(),
        }

        self._last_report = report

        return report

    def last_report(self) -> dict[str, Any] | None:
        """The most recent sweep, without running another.

        What a dashboard should read: the deep check is expensive, and a screen
        that re-ran it on every refresh would be its own load problem.
        """
        return self._last_report

    def liveness(self) -> dict[str, Any]:
        """Is this process answering. Nothing more, and deliberately so.

        A liveness probe that checks dependencies restarts the process when a
        database is slow, which is when restarting helps least. Readiness is
        `report`.
        """
        return {"status": OK, "service": "T-ZONE Platform API"}


health_service = HealthService()
