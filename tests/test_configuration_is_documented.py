"""Every knob the code reads is a knob an operator is told about.

The same defect this audit kept finding, one layer out. A setting a company
can store and nothing reads is a control that decides nothing; a setting the
*platform* reads and nobody documents is a control an operator cannot find.
`SWEEP_MAX_CONCURRENT_COMPANIES` was the second kind — read on every boot, used
to bound the background sweep, and absent from `.env.example`, so the only way
to discover it was to read `config/settings.py`.

The reverse matters more. A name in `.env.example` that the code does not read
is worse than undocumented: an operator sets it, deploys, and believes they
have changed something. That is the D-013 defect with a production host on the
other end of it.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# Read outside `config/settings.py`, so a sweep of that one file cannot see
# them. Each is named here with where it is actually read.
READ_ELSEWHERE: dict[str, str] = {
    # `backend/security/keyring.py` holds it as `MASTER_KEY_ENV`, because the
    # keyring must not import the settings module: it is what the settings
    # module's own encrypted values depend on.
    "TZONE_MASTER_KEY": "backend/security/keyring.py",
}


# Four ways this module reads the environment. The first version of this check
# knew about one of them and reported the twelve settings read through the
# typed helpers as undocumented — a check that cries wolf about correct code is
# one the next person deletes rather than fixes.
_READ_FORMS = (
    r'os\.(?:environ\.get|getenv)\(\s*["\']([A-Z0-9_]+)',
    r'_env_bool\(\s*["\']([A-Z0-9_]+)',
    r'_env_path\(\s*["\']([A-Z0-9_]+)',
    r'_env_list\(\s*["\']([A-Z0-9_]+)',
)


def _read_by_settings() -> set[str]:
    source = (ROOT / "config" / "settings.py").read_text()
    found: set[str] = set()

    for pattern in _READ_FORMS:
        found |= set(re.findall(pattern, source))

    return found


def _documented() -> set[str]:
    source = (ROOT / ".env.example").read_text()

    return set(re.findall(r"^\s*#?\s*([A-Z0-9_]+)\s*=", source, re.M))


def test_both_sides_can_be_read():
    """Without this, a change to how settings are declared would make the
    checks below pass by comparing two empty sets."""
    assert len(_read_by_settings()) > 30
    assert len(_documented()) > 30


def test_every_setting_the_code_reads_is_documented():
    """An operator tuning the platform should not have to read the source to
    find out what there is to tune."""
    undocumented = sorted(_read_by_settings() - _documented())

    assert not undocumented, (
        "Environment variable(s) the platform reads and .env.example does not "
        f"mention: {undocumented}\n\nDocument them, with the default and what "
        "raising or lowering them costs."
    )


def test_every_documented_setting_is_read_by_something():
    """The dangerous direction.

    A name in `.env.example` that nothing reads is not merely untidy: an
    operator sets it, restarts, and believes the platform changed. That is a
    control that decides nothing, with a production host behind it.
    """
    stale = sorted(_documented() - _read_by_settings() - set(READ_ELSEWHERE))

    assert not stale, (
        f"Environment variable(s) documented and read by nothing: {stale}\n\n"
        "Wire it, delete it, or add it to READ_ELSEWHERE naming the module "
        "that reads it."
    )


def test_the_elsewhere_list_is_honest():
    """An entry claiming a file reads a variable, where that file does not."""
    lying = []

    for name, where in READ_ELSEWHERE.items():
        source = (ROOT / where).read_text()

        if name not in source:
            lying.append(f"{name} is not read by {where}")

    assert not lying, "READ_ELSEWHERE is wrong:\n  " + "\n  ".join(lying)


def test_every_third_party_import_is_installable():
    """A module imported and missing from `requirements.txt` runs on a
    developer's machine and crashes on a fresh deploy — the one failure that
    never shows up in testing, because testing runs where it is installed."""
    import ast
    import sys

    stdlib = set(sys.stdlib_module_names)
    local = {
        "backend", "core", "channels", "gateway", "config", "database",
        "tools", "main", "tests",
    }

    imported: set[str] = set()

    for root in ("backend", "core", "channels", "gateway", "config", "database", "tools"):
        for path in (ROOT / root).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])

    requirements = (ROOT / "requirements.txt").read_text().lower()

    missing = sorted(
        module
        for module in imported - stdlib - local
        if module.lower() not in requirements
        and module.lower().replace("_", "-") not in requirements
    )

    assert not missing, (
        f"Imported and not in requirements.txt: {missing}\n\nThis runs here and "
        "fails on a fresh install."
    )
