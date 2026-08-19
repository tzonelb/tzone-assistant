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

# Names that have appeared in this repository's channel lists and are not
# channels the platform can connect. `website` is the one this file was written
# for; the rest are here so that reviving any of them in a screen fails loudly
# rather than shipping another permanently empty tab.
_RETIRED_OR_UNSUPPORTED = frozenset(
    {
        "website",
        # Added after this file failed to catch it. `website_chat` sat in five
        # backend lists and one frontend list while every check here passed,
        # because the watch-list said `website` and the spelling was different
        # by one word. A watch-list is only as good as somebody's memory of
        # what to watch for — which is why the check below it now compares the
        # real constants instead of recognising names.
        "website_chat",
        "web",
        "sms",
        "email",
        "viber",
        "wechat",
        "line",
        "tiktok",
    }
)


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



def _code_lines(pattern: str) -> list[str]:
    """Matching lines in the frontend, excluding comments.

    Written after a comment in this very repository — one explaining that
    `website` used to be offered — failed the check that `website` is not
    offered. It is the fourth time in this audit that a source-scanning check
    has matched somebody's prose about the defect instead of the defect. A
    check that cannot be explained in a comment is a check people work around.
    """
    result = subprocess.run(
        [
            "grep", "-rn", "-E", pattern,
            "--include=*.jsx", "--include=*.js",
            "src",
        ],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
    )

    lines = []

    for line in result.stdout.splitlines():
        _, _, code = line.partition(":")
        _, _, code = code.partition(":")
        stripped = code.strip()

        if stripped.startswith(("//", "*", "/*")):
            continue

        # The one place the list is allowed to exist.
        if "utils/channels.js" in line:
            continue

        lines.append(line)

    return lines


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


def _channel_keyed_blocks(source: str) -> list[tuple[int, set[str]]]:
    """Every object literal in a file that is keyed by channel.

    A run of `key: value` lines, or a single line holding several of them.
    A block counts as channel-keyed when it names two or more channels the
    backend supports — one is a coincidence, two is a list.
    """
    known = _backend_channels()
    blocks: list[tuple[int, set[str]]] = []
    run: list[str] = []
    run_start = 0

    def flush():
        if run and len(set(run) & known) >= 2:
            blocks.append((run_start, set(run)))

    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()

        if stripped.startswith(("//", "*", "/*")):
            continue

        keys = re.findall(r"(?:^|[{,\s])([A-Za-z_][A-Za-z0-9_]*)\s*:", line)

        # A single line carrying the whole map.
        if len(set(keys) & known) >= 2:
            blocks.append((number, set(keys)))
            run = []
            continue

        if keys:
            if not run:
                run_start = number
            run.extend(keys)
        else:
            flush()
            run = []

    flush()

    return blocks


def test_no_screen_lists_a_channel_the_backend_does_not_support():
    """The property that actually matters, after two weaker versions of it.

    The first looked for a quoted list and missed every map written as object
    keys. The second flagged any map keyed by channel — which condemned the
    label maps in `ChannelsPage` and `AiTeachingPage`, where mapping a code to
    a display name is exactly the right thing to do and an unknown code costs
    nothing worse than an unprettified label.

    The defect was never "a file names the channels". It is a file offering a
    channel that does not exist, which is what `website` was in five places:
    a tab on the inbox, an option in the notification filter, a toggle on the
    Preferences screen, an icon, and a per-user default.

    Limit worth stating: this recognises names on a watch-list, so a brand new
    invented channel would pass here. What catches that instead is
    `test_the_frontend_constant_matches_the_backend` for the one real
    catalogue, and the inbox reading `supported_channels` off the response —
    the two places that decide what is *offered*. This test's job is the stale
    copies those two cannot see.
    """
    known = _backend_channels()
    offenders = []

    for path in (FRONTEND).rglob("*.js*"):
        if path.name == "channels.js" or "/dist/" in str(path):
            continue

        for line, keys in _channel_keyed_blocks(path.read_text()):
            # Keys that are not channels at all — `enabled`, `types` — sit in
            # the same object as the channel ones in some files. Only a key
            # that reads as a channel and is not one is reported.
            strays = {
                key
                for key in keys - known
                if key in _RETIRED_OR_UNSUPPORTED
            }

            if strays:
                offenders.append(
                    f"{path.relative_to(ROOT)}:{line} offers {sorted(strays)}"
                )

    assert not offenders, (
        "A screen offers a channel the platform cannot connect:\n  "
        + "\n  ".join(offenders)
        + "\n\nRemove it, or add it to SUPPORTED_CHANNELS with a routing "
        "field, a webhook and a sender."
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
    offered = _code_lines(r'"website"|\bwebsite\s*:')

    assert not offered, (
        "`website` is offered as a channel again:\n  " + "\n  ".join(offered)
    )


# --------------------------------------------------- every list, not a watch-list


def test_no_python_channel_list_offers_more_than_the_catalogue():
    """The check that would have caught `website_chat`, and did not exist.

    Everything above compares the *frontend* to the backend, or recognises
    names on a watch-list. Neither can see a second backend list that has
    quietly grown an extra entry — and that is exactly what happened:
    `config.SUPPORTED_CHANNELS` held five channels, the real catalogue held
    four, and the AI Teaching screen offered personas and reply policies for a
    channel with no routing field, no webhook and no sender. A company could
    configure it in detail and nothing would ever read the configuration.

    So this imports the constants themselves rather than reading source or
    matching names. A new list added anywhere with an invented channel in it
    fails here on the day it is written, whatever it is called.
    """
    from backend.api.schemas.ai_teaching import PreviewChannel
    from backend.services.bot_profile_service import PREVIEW_CHANNELS
    from backend.services.reply_policy_service import POLICY_CHANNELS
    from channels.sender import SUPPORTED_CHANNELS as SENDER_CHANNELS
    from core.intent_transition import IntentTransitionManager

    import typing

    catalogue = _backend_channels()

    lists = {
        "bot_profile_service.PREVIEW_CHANNELS": set(PREVIEW_CHANNELS),
        "reply_policy_service.POLICY_CHANNELS": set(POLICY_CHANNELS),
        "channels.sender.SUPPORTED_CHANNELS": set(SENDER_CHANNELS),
        "schemas.ai_teaching.PreviewChannel": set(typing.get_args(PreviewChannel)),
        "IntentTransitionManager.GENERAL_CHANNELS": set(
            IntentTransitionManager.GENERAL_CHANNELS
        ),
    }

    offenders = {
        name: sorted(values - catalogue)
        for name, values in lists.items()
        if values - catalogue
    }

    assert not offenders, (
        "A list offers a channel the platform cannot connect:\n  "
        + "\n  ".join(f"{name}: {extra}" for name, extra in offenders.items())
        + f"\n\nThe catalogue is {sorted(catalogue)}. Add the channel to it "
        "with a routing field, a webhook and a sender, or take it out of the "
        "list."
    )


def test_there_is_only_one_channel_catalogue():
    """`config.SUPPORTED_CHANNELS` was a second one, and it was wrong.

    Nothing read it — the readers all import the catalogue from
    `channel_account_service` — but a comment in `reply_policy_service` cited
    it as the source its own list mirrored, and the list it described had five
    entries where the real one had four. A dead constant that documentation
    points at is worse than a dead constant, because it is believed.

    This is the same shape as the two `DEFAULT_SETTINGS` catalogues: the one
    that was seeded was not the one that was served, and every check ran
    against the wrong copy.
    """
    from config.settings import config

    assert not hasattr(config, "SUPPORTED_CHANNELS"), (
        "config has a SUPPORTED_CHANNELS again. There is one catalogue, in "
        "backend/services/channel_account_service.py, and a second one drifts "
        "silently because nothing compares them."
    )
