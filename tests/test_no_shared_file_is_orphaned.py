"""A shipped data file with no reader is a leak waiting for a reader.

The worst defect this platform had was a shared file reaching customers:
`config/automation_policy.json` and `features/*/flow.json` together served
T-ZONE's own menu — `IPTV · Sales · About T-ZONE` — to the customers of every
company on the platform.

Fixing it removed the *readers*. `data/menus.json` was left behind: the same
menu, byte for byte, sitting in the repository with nothing loading it. So did
`core/knowledge_manager.py`'s two knowledge files, until they were deleted too.

An orphaned file of this kind is not harmless. It reads as an asset rather than
as a mistake, so the next person to need a default menu finds one already
written and wires it back in — and the leak returns with a plausible commit
message. The file being unreferenced is exactly what makes it look safe to use.

So the rule is that a file shipped under `config/`, `features/` or `data/` is
either loaded by something, or listed below with why it is kept.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SHIPPED_DIRS = ("config", "features", "data")

SEARCH_ROOTS = ("backend", "core", "channels", "gateway", "tools", "main.py")


# Shipped, loaded by nothing, and kept on purpose. Empty: there has not yet
# been a good reason for one, and an entry here should have to be argued for.
KEPT_WITHOUT_A_READER: dict[str, str] = {}


def _shipped_files() -> list[Path]:
    files: list[Path] = []

    for directory in SHIPPED_DIRS:
        base = ROOT / directory

        if not base.exists():
            continue

        files.extend(
            path
            for path in base.rglob("*.json")
            # Runtime state, not shipped content: databases, caches and
            # anything git does not track.
            if _is_tracked(path)
        )

    return sorted(files)


def _is_tracked(path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    return result.returncode == 0


def _loaded_by(path: Path) -> list[str]:
    """Files that name this one somewhere other than a comment or docstring.

    Prose costs a false positive here in a specific and misleading way:
    `knowledge_service.py` opens with a docstring explaining that it no longer
    reads `config/knowledge_base.json`, and a plain search reports that as a
    reader. The check would then call a genuinely orphaned file loaded, on the
    strength of a sentence saying it is not.
    """
    # `-F`, because the name is a filename and not a pattern. Without it the
    # dot in `content.json` matches any character, so `content=json.dumps(...)`
    # in an unrelated file counted as a reader and the check reported an
    # orphaned file as loaded. Found by mutation, not by reading.
    result = subprocess.run(
        ["grep", "-rnF", path.name, *SEARCH_ROOTS],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    readers = []

    for line in result.stdout.splitlines():
        _, _, code = line.partition(":")
        _, _, code = code.partition(":")
        stripped = code.strip()

        if stripped.startswith(("#", '"""', "'''", "*")):
            continue

        readers.append(line)

    return readers


def test_the_shipped_files_can_be_found():
    """Without this, moving the directories would make the check below pass by
    finding nothing to check."""
    files = _shipped_files()

    assert len(files) >= 5
    assert any(path.name == "response_policy.json" for path in files)


def test_every_shipped_file_is_loaded_by_something():
    orphaned = []

    for path in _shipped_files():
        relative = str(path.relative_to(ROOT))

        if relative in KEPT_WITHOUT_A_READER:
            continue

        if not _loaded_by(path):
            orphaned.append(relative)

    assert not orphaned, (
        "Shipped file(s) nothing loads:\n  "
        + "\n  ".join(orphaned)
        + "\n\nDelete it, or add it to KEPT_WITHOUT_A_READER with the reason. "
        "An unreferenced data file reads as an asset rather than as a mistake, "
        "and the next person to need one finds it already written."
    )


def test_the_kept_list_has_no_stale_entries():
    shipped = {str(path.relative_to(ROOT)) for path in _shipped_files()}
    stale = sorted(set(KEPT_WITHOUT_A_READER) - shipped)

    assert not stale, f"KEPT_WITHOUT_A_READER names files that are gone: {stale}"


def test_the_leaked_menu_file_is_gone():
    """Kept as its own line so a failure names the specific one.

    `data/menus.json` held the exact menu that reached every company's
    customers — `IPTV`, `Sales`, `About T-ZONE`. Its reader was deleted when
    that leak was fixed; the file was not, and it sat in the repository for
    anybody who needed a default menu to find.
    """
    assert not (ROOT / "data" / "menus.json").exists(), (
        "data/menus.json is back. It is one company's menu, and every reader it "
        "ever had served it to all of them."
    )
