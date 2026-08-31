"""The file an operator copies must not defeat a default that knows better.

`config.settings` has exactly one setting whose default depends on the
environment: `ENABLE_DOCS`, off in production because the interactive docs
enumerate every endpoint and schema on the platform, the operator console
included. The default is the only thing in the system that knows which
environment this is.

`.env.example` had `ENABLE_DOCS=true`, three lines under its own comment saying
the docs stay off in production. `deploy/install.sh` copies that file to the
production environment file and overrides `APP_ENV`, `CORS_ORIGINS` and the
paths -- but not this one. So every install made from the documented
instructions served `/docs`, `/redoc` and `/openapi.json` publicly, and the
production-aware default never got to run, because a default cannot override a
value that is set.

Two things are checked, because fixing either alone leaves the hole open: the
shipped example must not pin it, and the installer must set it off explicitly
so an install that already has the old value is corrected on its next run.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

EXAMPLE = ROOT / ".env.example"

INSTALLER = ROOT / "deploy/install.sh"


def _pinned(name: str) -> bool:
    """Is this variable assigned, uncommented, in the shipped example?"""
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=", re.MULTILINE)

    return bool(pattern.search(EXAMPLE.read_text()))


def test_the_example_still_documents_the_setting():
    """Removing the line entirely would pass the check below and help nobody."""
    assert "ENABLE_DOCS" in EXAMPLE.read_text()


def test_the_shipped_example_does_not_turn_the_api_docs_on():
    assert not _pinned("ENABLE_DOCS"), (
        ".env.example assigns ENABLE_DOCS. deploy/install.sh copies this file "
        "to the production environment file, so assigning it here publishes "
        "/docs, /redoc and /openapi.json on every install -- and defeats the "
        "APP_ENV-aware default, which is the only thing that knows this is "
        "production. Leave the line commented."
    )


def test_the_installer_turns_the_api_docs_off_explicitly():
    source = INSTALLER.read_text()

    assert re.search(r"^set_env ENABLE_DOCS false\s*$", source, re.MULTILINE), (
        "deploy/install.sh does not set ENABLE_DOCS. Commenting the line in "
        ".env.example fixes new installs and leaves every existing one serving "
        "its docs, because the value is already written into their env file "
        "and a default cannot override a value that is set."
    )


def test_the_default_is_off_in_production_and_on_outside_it():
    """The behaviour the two checks above exist to protect.

    Run in a subprocess rather than by reloading `config.settings` in place.
    Reloading rebinds the module's `config` object, and every module that did
    `from config.settings import config` keeps the old one -- so the reload
    leaves half the process holding a stale settings object. It cost four
    unrelated mailer tests when this file first ran alongside them, which is
    the whole argument against doing it: a test that decides what the rest of
    the suite sees is not testing anything, it is changing the subject.
    """
    import subprocess
    import sys

    for environment, expected in (("production", "False"), ("development", "True")):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from config.settings import config; print(config.ENABLE_DOCS)",
            ],
            cwd=ROOT,
            env={
                **{k: v for k, v in os.environ.items() if k != "ENABLE_DOCS"},
                "APP_ENV": environment,
            },
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected, (
            f"APP_ENV={environment} should default ENABLE_DOCS to {expected}, "
            f"got {result.stdout.strip()!r}"
        )
