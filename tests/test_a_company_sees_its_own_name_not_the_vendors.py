"""Nothing the platform vendor writes for itself belongs on a customer's screen.

Two separate ways that keeps happening, both found on live screens.

The first is branding. `/api/platform-ui/config` has carried each company's own
`brand.logoUrl` since the workspace config was built, and nothing read it: the
redesigned sidebar imports the bundled T-ZONE mark directly, so a business that
had uploaded its logo still looked at the vendor's. The v1 sidebar it replaced
reads `branding.logo_url` correctly, which is what makes this a regression
rather than an unbuilt feature.

The second is notes to the developer left in customer-facing copy. The Publish
screen told a company's owner that "T-ZONE already has a real AI pipeline (used
for AI Teaching/replies) — connecting it here ... ask if you want it next".
That names the vendor to a customer, describes the vendor's internals, and ends
by asking the reader to request work from someone they have no way to reach.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

CUSTOMER_SCREENS = ROOT / "frontend/src/pages"

SIDEBAR = ROOT / "frontend/src/components/layout/SidebarV2.jsx"

# Phrases that only make sense addressed to whoever is building the platform.
# A company's owner reading any of these has nobody to ask.
NOTES_TO_THE_DEVELOPER = (
    "ask if you want it next",
    "ask if you want this next",
    "let me know if you want",
)


def _customer_facing_sources():
    """Screens a company's employees look at.

    `superadmin/` is excluded deliberately: it is the vendor's own console,
    where naming the vendor is correct.
    """
    return sorted(CUSTOMER_SCREENS.rglob("*.jsx"))


def test_the_sweep_reads_the_screens():
    sources = _customer_facing_sources()

    assert len(sources) > 20, f"only {len(sources)} screens found"


def test_no_screen_asks_the_customer_to_request_work():
    offenders = []

    for path in _customer_facing_sources():
        text = path.read_text()

        for phrase in NOTES_TO_THE_DEVELOPER:
            if phrase in text.lower():
                offenders.append(f"{path.relative_to(ROOT)}: {phrase!r}")

    assert not offenders, (
        "Copy written for the developer is being shown to a company's "
        "employees:\n  "
        + "\n  ".join(offenders)
        + "\n\nSay what the screen does and does not do today, in words "
        "addressed to the person reading it."
    )


def test_the_shell_shows_the_company_its_own_logo():
    source = SIDEBAR.read_text()

    match = re.search(r"<img\s([^>]*?)className=\"sidebar-v2-logo\"", source, re.DOTALL)

    assert match, "the sidebar logo is no longer an <img> with that class"

    attributes = match.group(1)

    assert "brand?.logoUrl" in attributes, (
        "The sidebar hard-codes the bundled T-ZONE mark, so a company that has "
        "uploaded its own logo still sees the platform vendor's. "
        "`usePlatformTheme().brand.logoUrl` already carries it."
    )


def test_no_company_is_handed_a_logo_url_that_nothing_serves():
    """`/tzone-logo.png` is not a file on this server; null is answerable.

    Matched against the assignment rather than the file, so the prose
    explaining the change does not itself trip the check.
    """
    route = (ROOT / "backend/api/routes/platform_ui.py").read_text()
    defaults = (ROOT / "frontend/src/config/platformDefaults.js").read_text()

    served = re.search(r'"logoUrl":\s*([^,\n]+)', route)

    assert served, "the workspace config no longer answers logoUrl"
    assert "/tzone-logo.png" not in served.group(1), (
        "The workspace config hands every company without a logo a path "
        "nothing serves, so the shell renders a broken image instead of "
        "falling back to the bundled mark."
    )

    bundled = re.search(r"logoUrl:\s*([^,}\n]+)", defaults)

    assert bundled
    assert "/tzone-logo.png" not in bundled.group(1)
