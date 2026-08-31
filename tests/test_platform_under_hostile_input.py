"""What the platform does with values nobody would type on purpose.

The other pressure file asks what happens when the code runs many times at
once. This one asks what happens when it runs once, on input chosen to break
it: a megabyte where a sentence was expected, a null byte in the middle of a
name, a number far outside what any screen can produce.

Two of these values reach the platform from outside any screen. A customer's
display name and message text arrive from Meta's webhook and are entirely under
the sender's control — nobody at the company types them, and no form validates
them on the way in. Everything a webhook can carry is attacker-chosen by
definition, so it is the right place to point this file first.

The bar is deliberately not "is refused". A platform that refuses an emoji in a
customer's name is broken for most of the region it serves. The bar is: it is
stored and read back unchanged, or it is refused with a message — never
silently truncated, never corrupted, and never a crash the caller cannot name.
"""

from __future__ import annotations

import sys

import pytest


# One from each family that has actually broken text handling somewhere:
# a null byte (C string terminator), an RTL override (display spoofing), a
# zero-width joiner (length arithmetic), an astral-plane emoji (UTF-16 pairs),
# combining marks (normalisation), and a lone surrogate (invalid UTF-8).
NASTY_NAMES = [
    ("null byte", "Ali\x00Hassan"),
    ("rtl override", "Ali‮Hassan"),
    ("zero width joiner", "Ali‍Hassan"),
    ("astral emoji", "Ali 👨‍👩‍👧‍👦 Hassan"),
    ("combining marks", "Ali" + "́" * 40),
    ("arabic presentation forms", "ﻋﻠﻲ ﺣﺴﻦ"),
    ("newlines", "Ali\nHassan\r\nثاني"),
    ("tabs and spaces", "  Ali\tHassan  "),
    ("sql-ish", "Ali'; DROP TABLE customers; --"),
    ("template-ish", "${jndi:ldap://x/y}"),
    ("html", "<script>alert(1)</script>"),
]


@pytest.fixture()
def wired(platform, monkeypatch):
    """Point every service at the test platform.

    Same shape as the concurrency file's helper, and for the same reason: a
    service that quietly kept a previous test's database would make every
    assertion below meaningless.
    """
    from database.manager import DatabaseManager

    import database.manager as manager_module

    import backend.services.customer_service  # noqa: F401
    import backend.services.knowledge_service  # noqa: F401
    import backend.services.company_settings_service  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    for name in (
        "backend.services.customer_service",
        "backend.services.knowledge_service",
        "backend.services.company_settings_service",
    ):
        assert getattr(sys.modules[name], "database_manager", None) is test_manager

    return test_manager


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


# ------------------------------------------------- text from the outside world


@pytest.mark.parametrize("label,name", NASTY_NAMES, ids=[n for n, _ in NASTY_NAMES])
def test_a_customer_name_from_a_webhook_round_trips(wired, platform, label, name):
    """The name arrives from Meta, not from a form. Whatever it is, reading it
    back must give the same characters or a clean refusal — a name that is
    stored differently from how it was sent means the inbox shows one customer
    under two identities."""
    from backend.services.customer_service import customer_service

    customer = customer_service.upsert_from_channel(
        company_id=_alpha(platform),
        channel="messenger",
        external_user_id=f"ext-{label.replace(' ', '-')}",
        display_name=name,
    )

    read_back = customer_service.get_customer(
        company_id=_alpha(platform), customer_id=customer["id"]
    )

    stored = read_back["display_name"]

    assert stored is not None, f"{label}: the name vanished entirely"
    assert stored == name.strip() or stored == name, (
        f"{label}: stored as {stored!r}, sent as {name!r}"
    )


def test_a_very_long_customer_name_does_not_crash(wired, platform):
    """No column here has a length, so a long name is stored whole. The
    property is that it is handled deliberately — stored or refused — not that
    it raises something the API turns into a 500."""
    from backend.services.customer_service import customer_service

    name = "ع" * 100_000

    customer = customer_service.upsert_from_channel(
        company_id=_alpha(platform),
        channel="messenger",
        external_user_id="ext-long",
        display_name=name,
    )

    read_back = customer_service.get_customer(
        company_id=_alpha(platform), customer_id=customer["id"]
    )

    assert len(read_back["display_name"]) == len(name), (
        "a long name was silently truncated — the inbox and the database now "
        "disagree about who this customer is"
    )


# ------------------------------------------------------------ numbers and ids


@pytest.mark.parametrize(
    "value",
    [
        -1,
        0,
        10**9,
        2**63 - 1,
    ],
)
def test_an_absurd_page_size_is_clamped_not_obeyed(wired, platform, value):
    """`list_entries` takes a limit straight from a query string. Obeying a
    billion would read the whole log into memory to answer one request."""
    from backend.services.activity_service import activity_service

    page = activity_service.list_entries(company_id=_alpha(platform), limit=value)

    assert 1 <= page["limit"] <= activity_service.MAX_LIMIT, (
        f"limit={value} was accepted as {page['limit']}"
    )


@pytest.mark.parametrize("value", [-1, -(10**9)])
def test_a_negative_offset_is_refused_rather_than_wrapped(wired, platform, value):
    """A negative OFFSET is a SQLite error, not a page."""
    from backend.services.activity_service import activity_service

    page = activity_service.list_entries(company_id=_alpha(platform), offset=value)

    assert page["offset"] >= 0


def test_an_id_that_cannot_be_a_number_is_refused_cleanly(wired, platform):
    from backend.services.customer_service import customer_service

    with pytest.raises((ValueError, TypeError, LookupError, KeyError)):
        customer_service.get_customer(
            company_id=_alpha(platform), customer_id="1 OR 1=1"
        )


# ----------------------------------------------------------------- settings


def test_a_deeply_nested_settings_value_does_not_recurse_to_death(wired, platform):
    """Settings are stored as JSON. A structure nested a few thousand deep is
    the classic way to turn `json.dumps` into a RecursionError, and this one is
    written from an authenticated screen — so the worst case is one company's
    own settings, not the platform. It still must not be an unhandled crash."""
    from backend.services.company_settings_service import company_settings_service

    payload: dict = {"note": "x"}

    for _ in range(2000):
        payload = {"nested": payload}

    try:
        company_settings_service.update_section(
            _alpha(platform), "ai_behavior", {"tone": payload}, None
        )
    except (ValueError, TypeError, RecursionError) as exc:
        # Named and refused is a fine outcome.
        assert str(exc)
        return

    # Or accepted — in which case it must read back without exploding.
    section = company_settings_service.get_section(_alpha(platform), "ai_behavior")

    assert "tone" in section["values"]


def test_a_huge_settings_value_is_stored_or_refused_but_not_corrupted(wired, platform):
    from backend.services.company_settings_service import company_settings_service

    blob = "ب" * 200_000

    try:
        company_settings_service.update_section(
            _alpha(platform), "ai_behavior", {"tone": blob}, None
        )
    except ValueError:
        return

    section = company_settings_service.get_section(_alpha(platform), "ai_behavior")

    assert section["values"]["tone"] == blob, "a large setting came back changed"


@pytest.mark.parametrize(
    "section",
    ["../../etc/passwd", "made_up", "", "   ", "ai_behaviour", "AI_BEHAVIOR"],
)
def test_an_unknown_settings_section_is_refused(wired, platform, section):
    """The section name comes from the URL.

    `_normalize_section` was already written, already correct, and already
    wired into `set_override` and `clear_override` — the two Super Admin
    methods. The company's own `get_section` and `update_section`, next to
    them in the same class, used a bare `.strip().lower()`. So a request to
    `PUT /api/settings/anything` stored a row nothing would ever read: not a
    leak and not a traversal, but unbounded growth in the company's settings
    table and its settings-history log, one row and up to a couple of hundred
    kilobytes at a time.

    `AI_BEHAVIOR` is in this list to hold the other half — the name is
    lowercased before it is judged, so the check must not have become
    case-sensitive on the way in.
    """
    from backend.services.company_settings_service import company_settings_service

    if section.strip().lower() in {"ai_behavior"}:
        # Valid after normalisation. Asserting it is *accepted* is what stops
        # this test being satisfied by a check that refuses everything.
        company_settings_service.update_section(
            _alpha(platform), section, {"tone": "friendly"}, None
        )
        return

    with pytest.raises(ValueError):
        company_settings_service.update_section(
            _alpha(platform), section, {"x": 1}, None
        )

    with pytest.raises(ValueError):
        company_settings_service.get_section(_alpha(platform), section)


# ------------------------------------------------------------------ knowledge


def test_a_knowledge_item_of_only_whitespace_is_refused(wired, platform):
    """Otherwise the assistant is taught a blank fact and quotes it."""
    from backend.services.knowledge_service import knowledge_service

    with pytest.raises(ValueError):
        knowledge_service.create_item(
            company_id=_alpha(platform),
            data={"title": "   \t\n  ", "content_ar": "شي"},
        )


def test_a_knowledge_item_with_no_content_is_refused(wired, platform):
    from backend.services.knowledge_service import knowledge_service

    with pytest.raises(ValueError):
        knowledge_service.create_item(
            company_id=_alpha(platform),
            data={"title": "Title", "content_ar": "  ", "content_en": ""},
        )


def test_a_megabyte_of_knowledge_round_trips(wired, platform):
    """Companies really do paste a whole price list."""
    from backend.services.knowledge_service import knowledge_service

    body = "سعر المنتج ١٠٠ ريال. " * 50_000

    item = knowledge_service.create_item(
        company_id=_alpha(platform),
        data={"title": "Price list", "content_ar": body},
    )

    stored = knowledge_service.get_item(
        company_id=_alpha(platform), item_id=item["id"]
    )

    assert stored["content_ar"] == body.strip(), "a large knowledge item changed"
