"""Opening /superadmin with no session must land on the console's own sign-in.

The operator console and the company app are two products sharing one bundle.
They have separate sessions, separate clients and separate login screens, and
the only thing that ties them together is that `/superadmin/*` is a route in
the same React tree -- so every provider the company app mounts also mounts for
the console.

That is how the console became unreachable. `ThemeProvider` wraps the whole
tree and fetches `/api/platform-ui/config` on mount. That endpoint answers for
a *company* session, so in a clean browser it answers 401, and the shared
client's default reading of a 401 is "your session expired" -- it calls
`handleUnauthorized`, which replaces the location with the company `/login`.
The operator never saw `/superadmin/login`; they were redirected off it before
it painted, and no credential could fix it because the redirect happened before
the form existed.

The property is narrow and worth stating exactly: a request the app makes
*before anyone has signed in* must not be able to trigger the signed-out
redirect. Every request made after sign-in still must, which is why this
checks the one call rather than removing the redirect.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

CLIENT = ROOT / "frontend/src/api/client.js"

THEME_CONTEXT = ROOT / "frontend/src/contexts/ThemeContext.jsx"

APP = ROOT / "frontend/src/App.jsx"


def test_the_console_shares_the_company_apps_provider_tree():
    """The reason the rest of this file has to exist.

    If `/superadmin` is ever given its own entry point, this fails and the
    checks below can be reconsidered rather than silently guarding nothing.
    """
    assert "/superadmin/*" in APP.read_text()
    assert "ThemeProvider" in (ROOT / "frontend/src/main.jsx").read_text()


def test_the_theme_config_is_fetched_before_anyone_has_signed_in():
    """The call is unconditional on mount -- that is what makes it dangerous."""
    source = THEME_CONTEXT.read_text()

    assert "getPlatformUiConfigRequest()" in source
    assert "useEffect" in source


def test_a_401_from_the_theme_config_does_not_redirect_to_the_company_login():
    source = CLIENT.read_text()

    match = re.search(
        r"export async function getPlatformUiConfigRequest\(\)\s*\{(.*?)\n\}",
        source,
        re.DOTALL,
    )

    assert match, "getPlatformUiConfigRequest is no longer declared as expected"

    assert "authenticated: false" in match.group(1), (
        "ThemeProvider fetches /api/platform-ui/config on mount, before anyone "
        "has signed in. Under the client's default a 401 there means 'session "
        "expired' and replaces the location with the company /login -- which "
        "throws an operator off /superadmin/login before the console paints. "
        "Pass `authenticated: false` so the 401 is returned to the caller, "
        "which already falls back to platformDefaults."
    )


def test_the_redirect_itself_is_still_in_place_for_every_other_request():
    """Removing the guard entirely would trade one defect for a worse one."""
    source = CLIENT.read_text()

    assert "handleUnauthorized();" in source
    assert 'window.location.replace(LOGIN_PATH);' in source
