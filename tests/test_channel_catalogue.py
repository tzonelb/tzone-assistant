"""One list of channels, checked against every copy of it.

The platform supports four channels. The frontend named them in several places,
each screen keeping its own copy, and the copies had drifted: two ended in
`website`, which is not in `SUPPORTED_CHANNELS`, has no routing field, no
webhook, no sender, and cannot be chosen on the Channels screen. Every company
saw a Website tab on its inbox — captioned "Website is not connected yet" — and
a Website option in its notification filter. Not connected, and not
connectable.

That is the same defect as a setting that saves and decides nothing, moved up
into the navigation: the screen offers something the backend cannot do.

The fix was not to delete the word. It was to stop the frontend keeping its own
catalogue. The inbox reads `supported_channels` off the response; the constant
in `utils/channels.js` is only what a screen shows before the first response.
This file is what stops it drifting again — a check nobody has to remember,
which is the only kind that survives.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

FRONTEND = ROOT / "frontend/src"


def _backend_channels() -> set[str]:
    from backend.services.channel_account_service import SUPPORTED_CHANNELS

    return set(SUPPORTED_CHANNELS)


def _frontend_constant() -> set[str]:
    source = (FRONTEND / "utils/channels.js").read_text()
    body = re.search(
        r"export const SUPPORTED_CHANNELS = \[(.*?)\];", source, re.S
    )

    assert body, "utils/channels.js no longer exports SUPPORTED_CHANNELS"

    return set(re.findall(r'"([a-z_]+)"', body.group(1)))


def test_the_frontend_constant_matches_the_backend():
    """A drifted copy is how `website` survived. The two lists are compared
    rather than trusted to stay in step."""
    backend = _backend_channels()
    frontend = _frontend_constant()

    assert frontend == backend, (
        "frontend/src/utils/channels.js has drifted from SUPPORTED_CHANNELS.\n"
        f"  only in the frontend: {sorted(frontend - backend)}\n"
        f"  only in the backend:  {sorted(backend - frontend)}"
    )


def test_every_channel_the_backend_claims_can_actually_be_routed():
    """A channel in the list with no routing field would be offered on the
    Channels screen and fail on save."""
    from backend.services.channel_account_service import (
        ROUTING_FIELD,
        SUPPORTED_CHANNELS,
    )

    missing = sorted(set(SUPPORTED_CHANNELS) - set(ROUTING_FIELD))

    assert not missing, f"Channel(s) with no routing field: {missing}"


def test_no_screen_keeps_its_own_channel_catalogue():
    """The root cause, not the symptom.

    `website` was not a typo — it was a copy of the channel list that nobody
    updated when the real one changed. A screen that names all four channels in
    a row is keeping a copy, so the check looks for the shape rather than for
    the stale word.
    """
    result = subprocess.run(
        [
            "grep", "-rn",
            r'"messenger".*"whatsapp"',
            "--include=*.jsx", "--include=*.js",
            "src",
        ],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
    )

    copies = [
        line
        for line in result.stdout.splitlines()
        # The one place the list is allowed to exist.
        if "utils/channels.js" not in line
    ]

    assert not copies, (
        "A screen keeps its own copy of the channel catalogue:\n  "
        + "\n  ".join(copies)
        + "\n\nImport SUPPORTED_CHANNELS from utils/channels.js, or read "
        "`supported_channels` off the response."
    )


def test_the_inbox_response_carries_the_catalogue():
    """The inbox greys out channels with no account, so it needs the full list
    as well as the connected one. Without this it falls back to its constant
    and drifts again the day a channel is added."""
    source = (ROOT / "backend/api/routes/conversations.py").read_text()

    assert '"supported_channels"' in source, (
        "The conversations response no longer sends supported_channels; the "
        "inbox would be back to its own hardcoded list."
    )


def test_website_is_gone_from_the_frontend():
    """The specific symptom, kept as its own line so a failure names it."""
    result = subprocess.run(
        ["grep", "-rn", r'"website"', "--include=*.jsx", "--include=*.js", "src"],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
    )

    assert not result.stdout.strip(), (
        "`website` is offered as a channel again:\n" + result.stdout
    )
