"""Tests for the platform checking itself.

`health.py` returned a constant. It said `{"status": "ok"}` whether or not the
master key was loadable, whether or not a single company database could be
opened, and whether or not the disk had run out — so the one endpoint a monitor
watches was the only thing on the platform that could not fail.

`PRAGMA integrity_check` did not appear anywhere in the repository, which meant
silent corruption had nothing looking for it. `upgrade_all_tenants` existed and
had no callers at all, so a release that added a column left every existing
company failing at query time until somebody remembered the CLI.

Three traps the checks had to avoid, each with a test below:

* `list_company_ids` filters to active companies — correctly, because the
  sweeps must not serve a suspended one. A health check that reused it would
  report a clean platform while a suspended company's file was corrupt.
* A freshly provisioned company recorded `schema_version` in the control plane
  and left `PRAGMA user_version` at zero, so a version comparison would have
  flagged every new company. That is the kind of false alarm that teaches an
  operator to ignore the check.
* A missing reading must not be reported as zero. "0% memory used" reads as
  healthy.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    # Both imported before the sweep. A module that has not been imported yet
    # holds no reference to rebind and binds the real singleton later — so the
    # `administered` fixture below would create its administrator in the
    # production control database, and the second test to run would fail with
    # "a user with this email already exists" for a reason that has nothing to
    # do with what it is testing.
    import backend.services.auth_service  # noqa: F401
    import backend.services.health_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.health_service",
        "backend.services.auth_service",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.health_service import HealthService

    # A fresh instance rather than the singleton: `last_report` is process
    # state, and one test's report leaking into another's assertion would make
    # both of them lie.
    return HealthService()


@pytest.fixture()
def administered(wired):
    """A platform with an administrator, which is the healthy baseline.

    Without one the control-plane check reports critical — correctly, because
    nobody can suspend a company, rotate a code or reinstate anything. Tests
    that assert an overall "ok" need one, and the state itself is asserted in
    `test_a_platform_with_no_administrator_is_critical`.
    """
    from backend.services.auth_service import auth_service

    auth_service.create_user(
        email="root@platform.example.com",
        password="PlatformPass123!",
        full_name="Root",
        is_super_admin=True,
    )

    return wired


# ------------------------------------------------------------------- the sweep


def test_a_healthy_platform_reports_ok(administered, alpha, beta):
    report = administered.report()

    assert report["status"] == "ok", report
    assert report["checks"]["companies"]["checked"] == 2
    assert report["checks"]["companies"]["failing"] == 0


def test_the_deep_check_runs_sqlite_integrity_check(wired, alpha):
    """It did not appear anywhere in the repository before, so silent
    corruption had nothing looking for it."""
    report = wired.check_company(alpha["id"], deep=True)

    assert report["integrity"] == "ok"


def test_the_shallow_check_does_not_read_every_page(wired, alpha):
    """The deep check belongs on the timer, not on a request an operator is
    waiting for."""
    assert "integrity" not in wired.check_company(alpha["id"], deep=False)


def test_a_company_whose_database_is_gone_is_reported_critical(
    wired, platform, alpha
):
    platform["manager"].tenant_path(alpha["id"]).unlink()

    report = wired.report()

    assert report["status"] == "critical"
    assert report["checks"]["companies"]["failing"] == 1
    assert report["checks"]["companies"]["items"][0]["company_id"] == alpha["id"]


def test_one_broken_company_does_not_hide_the_others(wired, platform, alpha, beta):
    """A sweep that stopped at the first failure would report one problem on a
    platform with fifty."""
    platform["manager"].tenant_path(alpha["id"]).unlink()

    report = wired.report()

    assert report["checks"]["companies"]["checked"] == 2


def test_only_the_failures_are_listed(administered, alpha, beta):
    """The whole list would be a thousand entries on a busy platform. The
    totals say the rest were looked at."""
    report = administered.report()

    assert report["checks"]["companies"]["items"] == []
    assert report["checks"]["companies"]["checked"] == 2


def test_the_worst_status_wins(administered, platform, alpha):
    """A platform with one unreadable database is not "mostly ok"."""
    platform["manager"].tenant_path(alpha["id"]).unlink()

    report = administered.report()

    assert report["checks"]["disk"]["status"] == "ok"
    assert report["status"] == "critical"


# ------------------------------------------------------- the suspended-company trap


def test_a_suspended_company_is_still_checked(wired, platform, alpha, beta):
    """`list_company_ids` filters to active companies, correctly — the sweeps
    must not deliver a suspended company's replies. Reusing it here would
    report a clean platform while a suspended company's file was corrupt.

    Their data is still on disk, still encrypted, and still has to be readable
    on the day they are reinstated.
    """
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE companies SET status = 'suspended' WHERE id = ?", (alpha["id"],)
        )
        conn.commit()

    assert platform["manager"].list_company_ids() == [beta["id"]]
    assert alpha["id"] in platform["manager"].list_all_company_ids()

    platform["manager"].tenant_path(alpha["id"]).unlink()

    report = wired.report()

    assert report["status"] == "critical", (
        "a suspended company's corrupt database went unreported"
    )


# ----------------------------------------------------- the schema-version trap


def test_a_freshly_provisioned_company_is_not_reported_as_out_of_date(
    wired, alpha
):
    """`_build_tenant_schema` did not stamp `PRAGMA user_version` while
    `provision_company` recorded `schema_version` in the control plane — two
    records of one fact that disagreed from the moment a company was created.

    Nothing read them, so nothing noticed. A version check would have flagged
    every new company, which is the kind of false alarm that teaches an operator
    to ignore the check.
    """
    from database.schema_tenant import TENANT_SCHEMA_VERSION

    result = wired.check_company(alpha["id"])

    assert result["schema_version"] == TENANT_SCHEMA_VERSION
    assert result["status"] == "ok"


def test_an_out_of_date_company_is_reported_as_a_warning_not_a_failure(
    wired, platform, alpha
):
    """It still opens and still serves; it needs an upgrade, not a restore."""
    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    result = wired.check_company(alpha["id"])

    assert result["status"] == "warning"
    assert "version 1" in result["detail"]


def test_the_startup_upgrade_only_opens_what_is_behind(wired, platform, alpha, beta):
    """`upgrade_all_tenants` opens every database, which at a thousand
    companies is a thousand decryptions before the first request is served."""
    from database.schema_tenant import TENANT_SCHEMA_VERSION

    assert platform["manager"].upgrade_outdated_tenants() == {}

    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE company_databases SET schema_version = 1 WHERE company_id = ?",
            (alpha["id"],),
        )
        conn.commit()

    upgraded = platform["manager"].upgrade_outdated_tenants()

    assert list(upgraded) == [alpha["id"]]
    assert (
        platform["manager"].tenant_schema_version(alpha["id"])
        == TENANT_SCHEMA_VERSION
    )


def test_a_suspended_company_is_still_upgraded(wired, platform, alpha):
    """Skipping them would leave a company that came back after two releases
    unable to open."""
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE companies SET status = 'suspended' WHERE id = ?", (alpha["id"],)
        )
        conn.execute(
            "UPDATE company_databases SET schema_version = 1 WHERE company_id = ?",
            (alpha["id"],),
        )
        conn.commit()

    assert alpha["id"] in platform["manager"].upgrade_outdated_tenants()


# ------------------------------------------------------------ the control plane


def test_a_platform_with_no_administrator_is_critical(wired, platform):
    """Running and unadministrable. Nobody can suspend a company, rotate a code
    or reinstate anything."""
    result = wired.check_control_plane()

    assert result["status"] == "critical"
    assert "create-super-admin" in result["detail"]


def test_a_platform_with_an_administrator_is_ok(administered):
    assert administered.check_control_plane()["status"] == "ok"


def test_a_missing_master_key_short_circuits_the_report(wired, monkeypatch):
    """Every company check below would fail identically and produce a page of
    noise that says one thing."""
    import backend.services.health_service as module

    class NoKey:
        def master_key(self):
            raise RuntimeError("TZONE_MASTER_KEY is not set")

    monkeypatch.setattr(module, "database_manager", NoKey())

    report = wired.report()

    assert report["status"] == "critical"
    assert list(report["checks"]) == ["master_key"]


# ------------------------------------------------------------------- the server


def test_the_server_metrics_are_readable(wired):
    server = wired.server()

    assert server["cpu_count"]
    assert server["disk"]["total_bytes"] > 0
    assert server["process_uptime_seconds"] >= 0


def test_an_unreadable_metric_is_none_and_not_zero(wired, monkeypatch):
    """A monitor cannot tell a real zero from a missing one, and "0% memory
    used" reads as healthy."""
    import builtins

    real_open = builtins.open

    def refuse(path, *args, **kwargs):
        if str(path).startswith("/proc/"):
            raise OSError("not available")

        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refuse)

    server = wired.server()

    assert server["memory"] is None
    assert server["uptime_seconds"] is None


def test_disk_pressure_is_reported_before_it_is_a_crisis(wired, monkeypatch):
    """The fix — pruning backups, resizing the volume — takes time an operator
    needs to have."""
    import backend.services.health_service as module

    class Usage:
        total = 100
        used = 90
        free = 10

    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: Usage())

    result = wired.check_disk()

    assert result["status"] == "warning"
    assert result["used_percent"] == 90.0


def test_a_full_disk_is_critical(wired, monkeypatch):
    import backend.services.health_service as module

    class Usage:
        total = 100
        used = 99
        free = 1

    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: Usage())

    assert wired.check_disk()["status"] == "critical"


# ------------------------------------------------------------------ the caching


def test_the_last_report_is_kept_for_a_dashboard_to_read(administered, alpha):
    """A screen that re-ran the deep check on every refresh would be its own
    load problem."""
    assert administered.last_report() is None

    administered.report()

    assert administered.last_report() is not None
    assert administered.last_report()["status"] == "ok"


def test_liveness_stays_a_constant(wired):
    """A liveness probe that checks its dependencies restarts the process when
    a database is slow, which is when restarting helps least — the new process
    finds the same slow database and the restart loop becomes the outage."""
    assert wired.liveness() == {"status": "ok", "service": "T-ZONE Platform API"}
